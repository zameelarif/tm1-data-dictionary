"""tm1dd command-line interface."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click

from tm1_data_dictionary import __version__
from tm1_data_dictionary.bootstrap import ensure_schema
from tm1_data_dictionary.chore_reader import ChoreReader
from tm1_data_dictionary.config import ConfigError, load_config
from tm1_data_dictionary.credentials import (
    KEYRING_SERVICE_NAME,
    CredentialError,
    get_keyring_secret,
    set_keyring_secret,
)
from tm1_data_dictionary.exclusions import ExclusionRules, partition
from tm1_data_dictionary.extract import extract_all
from tm1_data_dictionary.graph import build_graph, render_html
from tm1_data_dictionary.parser.assignments import summarize_variables
from tm1_data_dictionary.parser.blocks import code_lines
from tm1_data_dictionary.parser.chain_rollup import rollup_chain_lineage
from tm1_data_dictionary.parser.const_prop import build_const_table
from tm1_data_dictionary.parser.datasource_rollup import datasource_row
from tm1_data_dictionary.parser.diagnostics import collect_unresolved, diagnose
from tm1_data_dictionary.parser.references import extract_references
from tm1_data_dictionary.parser.rollup import rollup_cube_lineage
from tm1_data_dictionary.parser.ti_reader import TIReader
from tm1_data_dictionary.schema import (
    audit_schema,
    chore_process_schema,
    process_chain_schema,
    process_cube_schema,
    process_datasource_schema,
)
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError
from tm1_data_dictionary.writers.audit_writer import AuditWriter
from tm1_data_dictionary.writers.process_chain_writer import write_chain_lineage
from tm1_data_dictionary.writers.process_cube_writer import write_cube_lineage

SCHEMA_VERSION = "1.1"


@click.group()
@click.version_option(version=__version__, prog_name="tm1dd")
def main() -> None:
    """TM1 Data Dictionary command-line tool."""


@main.command()
def check() -> None:
    """Run the environment diagnostic."""
    from scripts.check_environment import main as check_main  # noqa: PLC0415

    check_main()


@main.command(name="set-credential")
@click.option(
    "--name",
    default="TM1_METADICT_PWD",
    show_default=True,
    help="The logical name to store the secret under (matches password_env in config.yaml).",
)
def set_credential(name: str) -> None:
    """Store a secret (e.g. the TM1 password) securely in the OS keyring.

    You are prompted for the value; it is never echoed to the screen and never written
    to a file. On Windows the secret is stored in Credential Manager, tied to your user
    account. After storing it here, you can remove the plaintext value from your .env.
    """
    secret = click.prompt(
        f"Enter the secret for '{name}'",
        hide_input=True,
        confirmation_prompt=True,
    )
    try:
        set_keyring_secret(name, secret)
    except CredentialError as exc:
        raise click.ClickException(str(exc)) from exc

    # Read it straight back as a sanity check (never print the value itself).
    stored = get_keyring_secret(name)
    if stored == secret:
        click.echo(
            f"Stored '{name}' in the OS keyring (service '{KEYRING_SERVICE_NAME}'). "
            "You can now remove it from your .env file."
        )
    else:  # pragma: no cover - defensive
        raise click.ClickException(
            "The secret was written but could not be read back. Check your keyring backend."
        )


@main.command()
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
def bootstrap(config_path: str) -> None:
    """Create the }Meta_* schema (dimensions and cubes) in the target TM1 instance.

    Idempotent: objects that already exist are left untouched. Honours dry-run mode.
    """
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        with TM1Client(cfg) as client:
            r1 = ensure_schema(client, audit_schema())
            r2 = ensure_schema(client, process_cube_schema())
            r3 = ensure_schema(client, process_chain_schema())
            r4 = ensure_schema(client, process_datasource_schema())
            r5 = ensure_schema(client, chore_process_schema())
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    results = (r1, r2, r3, r4, r5)
    for result in results:
        for name in result.dimensions_created:
            click.echo(f"  created dimension  {name}")
        for name in result.dimensions_skipped:
            click.echo(f"  exists  dimension  {name}")
        for name in result.cubes_created:
            click.echo(f"  created cube       {name}")
        for name in result.cubes_skipped:
            click.echo(f"  exists  cube       {name}")

    if any(r.created_anything for r in results):
        click.echo("Bootstrap complete: schema created.")
    else:
        click.echo("Bootstrap complete: schema already present, nothing to do.")


@main.command(name="record-run")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
@click.option(
    "--status",
    default="Success",
    show_default=True,
    help="Exit status to record for this run.",
)
def record_run(config_path: str, status: str) -> None:
    """Write one run record into }Meta_Extraction_Audit.

    Useful for proving the write path end-to-end: it records a row with the current
    extractor version, start/end time, and status. Honours dry-run mode in config.
    """
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    start = datetime.now(UTC)
    try:
        with TM1Client(cfg) as client:
            writer = AuditWriter(client)
            record = writer.record_run(
                extractor_version=__version__,
                schema_version=SCHEMA_VERSION,
                start_time=start,
                exit_status=status,
            )
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Recorded run {record.run_id}")
    click.echo(f"  version {record.extractor_version}  schema {record.schema_version}")
    click.echo(f"  duration {record.duration_seconds}s  status {record.exit_status}")


@main.command(name="list-processes")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
@click.option(
    "--contains", default="", help="Only show names containing this text (case-insensitive)."
)
def list_processes(config_path: str, contains: str) -> None:
    """List TI process names in the instance (optionally filtered)."""
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    needle = contains.lower()
    try:
        with TM1Client(cfg) as client:
            names = TIReader(client).list_process_names()
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    shown = [n for n in names if needle in n.lower()] if needle else names
    for name in shown:
        click.echo(name)
    click.echo(f"({len(shown)} of {len(names)} processes)")


@main.command(name="inspect-process")
@click.argument("name")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
def inspect_process(name: str, config_path: str) -> None:
    """Print a summary of a single TI process (blocks, datasource, variables, parameters)."""
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        with TM1Client(cfg) as client:
            reader = TIReader(client)
            if not reader.exists(name):
                raise click.ClickException(f"Process not found: {name}")
            ti = reader.read(name)
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Process: {ti.name}")
    click.echo(f"  security access: {ti.has_security_access}")
    ds = ti.datasource
    click.echo(f"  datasource type: {ds.type}")
    if ds.name_for_server:
        click.echo(f"  source: {ds.name_for_server}")
    if ds.type in {"ASCII", "CHARACTERDELIMITED"}:
        click.echo(f"  delimiter: {ds.delimiter!r}  header rows: {ds.header_records}")

    click.echo(f"  variables ({ti.variable_count}):")
    for v in ti.variables:
        click.echo(f"    {v.position:>2}  {v.name}  ({v.var_type})")

    if ti.parameters:
        click.echo(f"  parameters ({ti.parameter_count}):")
        for p in ti.parameters:
            click.echo(f"    {p.name}  ({p.param_type})  default={p.default_value!r}")

    click.echo("  block line counts:")
    for block_name, text in ti.iter_blocks():
        lines = len(text.splitlines()) if text else 0
        click.echo(f"    {block_name:<9} {lines} lines")


@main.command(name="extract-refs")
@click.argument("name")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
def extract_refs(name: str, config_path: str) -> None:
    """Extract and print the function references (lineage) from a single TI process.

    Uses const-propagation so variable targets (e.g. cCube) are resolved to their
    literal values (e.g. WeeklySales) where it is safe to do so.
    """
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        with TM1Client(cfg) as client:
            reader = TIReader(client)
            if not reader.exists(name):
                raise click.ClickException(f"Process not found: {name}")
            ti = reader.read(name)
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    lines = code_lines(ti)
    const_table = build_const_table(lines)  # resolve cCube -> 'WeeklySales', etc.
    refs = extract_references(lines, const_table=const_table)

    click.echo(f"Process: {ti.name}")
    click.echo(f"References found: {len(refs)}")
    click.echo(f"Variables resolved by const-propagation: {len(const_table.values)}")
    click.echo("")
    click.echo(f"  {'BLOCK':<9} {'LINE':>4}  {'ROLE':<10} {'FUNCTION':<20} TARGET")
    click.echo(f"  {'-' * 9} {'-' * 4}  {'-' * 10} {'-' * 20} {'-' * 24}")
    for r in refs:
        if r.target_is_literal:
            target = r.target
        elif r.resolved_target is not None:
            target = f"{r.resolved_target} [={r.target}]"  # resolved from a variable
        else:
            target = f"({r.target})"  # still dynamic
        click.echo(f"  {r.block:<9} {r.line_no:>4}  {r.role.value:<10} {r.function:<20} {target}")

    click.echo("")
    counts: dict[str, int] = {}
    for r in refs:
        counts[r.role.value] = counts.get(r.role.value, 0) + 1
    summary = "  ".join(f"{role}={n}" for role, n in sorted(counts.items()))
    click.echo(f"Summary: {summary}" if summary else "Summary: no references found")


@main.command(name="show-vars")
@click.argument("name")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
@click.option(
    "--all-assignments",
    is_flag=True,
    default=False,
    help="Show every assignment (not just one summary line per variable).",
)
def show_vars(name: str, config_path: str, all_assignments: bool) -> None:
    """Show the variable dictionary for a TI: every variable and where its value comes from.

    Complements 'extract-refs': where const-propagation cannot safely resolve a variable
    (e.g. cCube set from a cube read), this shows the raw assignment(s) so a developer can
    trace it by hand.
    """
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        with TM1Client(cfg) as client:
            reader = TIReader(client)
            if not reader.exists(name):
                raise click.ClickException(f"Process not found: {name}")
            ti = reader.read(name)
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    variables = summarize_variables(code_lines(ti))

    click.echo(f"Process: {ti.name}")
    click.echo(f"Variables assigned in code: {len(variables)}")
    click.echo("")

    if all_assignments:
        # Detailed: every assignment, in source order per variable.
        click.echo(f"  {'VARIABLE':<24} {'BLOCK':<9} {'LINE':>4}  RHS")
        click.echo(f"  {'-' * 24} {'-' * 9} {'-' * 4}  {'-' * 40}")
        for info in variables.values():
            for a in info.assignments:
                cf = " *" if a.in_control_flow else ""
                click.echo(f"  {a.name:<24} {a.block:<9} {a.line_no:>4}  {a.rhs}{cf}")
        click.echo("")
        click.echo("  (* = assigned inside an IF/WHILE block)")
    else:
        # Summary: one line per variable, showing where its value comes from.
        click.echo(f"  {'VARIABLE':<24} {'#':>3}  {'CONST?':<6} DERIVED FROM")
        click.echo(f"  {'-' * 24} {'-' * 3}  {'-' * 6} {'-' * 40}")
        for info in variables.values():
            const = "yes" if info.is_constant_literal else "no"
            click.echo(
                f"  {info.name:<24} {info.assignment_count:>3}  {const:<6} {info.derived_from}"
            )


@main.command(name="extract-cube")
@click.argument("name")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
def extract_cube(name: str, config_path: str) -> None:
    """Parse a TI's cube lineage and write it into }Meta_Process_Cube.

    Honours dry-run mode in config (parses and reports, but writes nothing).
    """
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        with TM1Client(cfg) as client:
            reader = TIReader(client)
            if not reader.exists(name):
                raise click.ClickException(f"Process not found: {name}")
            ti = reader.read(name)

            lines = code_lines(ti)
            const_table = build_const_table(lines)
            refs = extract_references(lines, const_table=const_table)
            result = rollup_cube_lineage(ti.name, refs)

            # Report what we found.
            click.echo(f"Process: {ti.name}")
            click.echo(f"Cube-lineage rows: {len(result.rows)}")
            for row in result.rows:
                click.echo(
                    f"  {row.role.value:<10} {row.cube:<28} "
                    f"count={row.count}  first={row.first_block}:{row.first_line}"
                )
            if result.unresolved_count:
                click.echo(
                    f"  ({result.unresolved_count} cube references stayed dynamic "
                    "and were not written)"
                )

            if client.dry_run:
                click.echo("Dry-run: nothing written.")
                return

            written = write_cube_lineage(client, list(result.rows))
            click.echo(f"Wrote {written} rows into }}Meta_Process_Cube.")
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command(name="extract-chain")
@click.argument("name")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
def extract_chain(name: str, config_path: str) -> None:
    """Parse a TI's chain dependencies and write them into }Meta_Process_Chain."""
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        with TM1Client(cfg) as client:
            reader = TIReader(client)
            if not reader.exists(name):
                raise click.ClickException(f"Process not found: {name}")
            ti = reader.read(name)

            lines = code_lines(ti)
            const_table = build_const_table(lines)
            refs = extract_references(lines, const_table=const_table)
            result = rollup_chain_lineage(ti.name, refs)

            click.echo(f"Process: {ti.name}")
            click.echo(f"Chain dependencies: {len(result.rows)}")
            for row in result.rows:
                click.echo(
                    f"  triggers {row.callee:<50} "
                    f"count={row.count}  first={row.first_block}:{row.first_line}"
                )
            if result.unresolved_count:
                click.echo(f"  ({result.unresolved_count} chain calls stayed dynamic, not written)")

            if client.dry_run:
                click.echo("Dry-run: nothing written.")
                return

            written = write_chain_lineage(client, list(result.rows))
            click.echo(f"Wrote {written} rows into }}Meta_Process_Chain.")
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command(name="extract")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Suppress per-process progress lines (show only the summary).",
)
def extract(config_path: str, quiet: bool) -> None:
    """Extract cube, chain, datasource, and chore lineage for EVERY process in the instance.

    Applies exclusion rules (Bedrock/utility, test/temp). One malformed process does
    not abort the run. Honours dry-run mode in config (parses and reports, writes nothing).
    """
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    def _progress(i: int, total: int, name: str, status: str) -> None:
        if not quiet:
            click.echo(f"  [{i:>4}/{total}] {name:<50} {status}")

    try:
        with TM1Client(cfg) as client:
            if client.dry_run:
                click.echo("Dry-run: parsing all processes, nothing will be written.")
            click.echo("Extracting lineage for all processes...")
            summary = extract_all(client, progress=_progress)
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("")
    click.echo("Extraction complete.")
    for line in summary.as_lines():
        click.echo(f"  {line}")


@main.command(name="diagnose-unresolved")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
@click.option(
    "--top",
    default=25,
    show_default=True,
    help="How many top offender expressions to show (0 = all).",
)
@click.option(
    "--process",
    "process_name",
    default="",
    help="Diagnose a single process in detail instead of the whole model.",
)
@click.option(
    "--expression",
    "expression",
    default=None,
    help='Locate every occurrence of one exact target expression (use "" for the blank target).',
)
def diagnose_unresolved(
    config_path: str, top: int, process_name: str, expression: str | None
) -> None:
    """Report which cube-target expressions stay unresolved (read-only, no writes).

    Modes:
      * default            - whole-model "top offenders" table;
      * --process NAME     - one process's unresolved references, with line numbers;
      * --expression EXPR  - every process/line where EXPR is the unresolved target
                             (pass --expression "" to find blank-target parse edge cases).
    """
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    def _refs_for(reader: TIReader, name: str) -> list:
        ti = reader.read(name)
        lines = code_lines(ti)
        const_table = build_const_table(lines)
        return extract_references(lines, const_table=const_table)

    try:
        with TM1Client(cfg) as client:
            reader = TIReader(client)

            # ---- Single-process detail mode ----
            if process_name:
                if not reader.exists(process_name):
                    raise click.ClickException(f"Process not found: {process_name}")
                occ = collect_unresolved(process_name, _refs_for(reader, process_name))
                click.echo(f"Process: {process_name}")
                click.echo(f"Unresolved cube references: {len(occ)}")
                if occ:
                    click.echo(f"  {'BLOCK':<9} {'LINE':>5}  {'ROLE':<10} EXPRESSION")
                    click.echo(f"  {'-' * 9} {'-' * 5}  {'-' * 10} {'-' * 30}")
                    for o in occ:
                        expr = o.expression if o.expression != "" else "(blank)"
                        click.echo(f"  {o.block:<9} {o.line_no:>5}  {o.role.value:<10} {expr}")
                return

            # ---- Whole-model parse (shared by the summary and --expression modes) ----
            part = partition(reader.list_process_names(), ExclusionRules.default())
            process_refs: dict[str, list] = {}
            for name in part.included:
                try:
                    process_refs[name] = _refs_for(reader, name)
                except Exception as exc:  # noqa: BLE001 - isolate per-process failures
                    click.echo(f"  (skip {name}: {type(exc).__name__})")
            report = diagnose(process_refs)
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    # ---- --expression: locate every occurrence of one expression ----
    if expression is not None:
        found = report.find(expression)
        shown = expression if expression != "" else "(blank)"
        click.echo(f"Occurrences of target expression {shown!r}: {len(found)}")
        if found:
            click.echo(f"  {'PROCESS':<45} {'BLOCK':<9} {'LINE':>5}  ROLE")
            click.echo(f"  {'-' * 45} {'-' * 9} {'-' * 5}  {'-' * 10}")
            for o in found:
                click.echo(f"  {o.process:<45} {o.block:<9} {o.line_no:>5}  {o.role.value}")
        return

    # ---- default whole-model summary ----
    click.echo("")
    click.echo(f"Included processes analysed: {len(process_refs)}")
    click.echo(f"Total unresolved cube references: {report.total}")
    click.echo("")
    limit = None if top == 0 else top
    groups = report.top(limit=limit)
    click.echo(f"Top {'all' if limit is None else limit} unresolved target expressions:")
    click.echo(f"  {'COUNT':>6}  {'PROCS':>5}  EXPRESSION")
    click.echo(f"  {'-' * 6}  {'-' * 5}  {'-' * 40}")
    for g in groups:
        expr = g.expression if g.expression != "" else "(blank)"
        click.echo(f"  {g.count:>6}  {g.process_count:>5}  {expr}")
    if limit is not None and len(report.groups) > limit:
        click.echo(f"  ... and {len(report.groups) - limit} more distinct expressions")
    click.echo("")
    click.echo('Tip: locate any expression with:  tm1dd diagnose-unresolved --expression "NAME"')
    click.echo('     (use --expression "" to find the blank-target references)')


@main.command(name="export-graph")
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config.yaml.",
)
@click.option(
    "--out",
    "out_path",
    default="data_flow.html",
    show_default=True,
    help="Output HTML file path.",
)
@click.option(
    "--title",
    default="TM1 Data Flow",
    show_default=True,
    help="Title shown at the top of the page.",
)
@click.option(
    "--vis-js",
    "vis_js_path",
    default="",
    help="Path to a local vis-network.min.js to inline for a fully offline file.",
)
def export_graph(config_path: str, out_path: str, title: str, vis_js_path: str) -> None:
    """Export an interactive HTML data-flow map (processes, cubes, datasources, chores)."""
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    cube_rows: list = []
    chain_rows: list = []
    ds_rows: list = []
    chore_rows: list = []
    try:
        with TM1Client(cfg) as client:
            reader = TIReader(client)
            part = partition(reader.list_process_names(), ExclusionRules.default())
            click.echo(f"Parsing {len(part.included)} processes for the data-flow map...")
            for name in part.included:
                try:
                    ti = reader.read(name)
                    lines = code_lines(ti)
                    const_table = build_const_table(lines)
                    refs = extract_references(lines, const_table=const_table)
                    cube_rows.extend(rollup_cube_lineage(name, refs).rows)
                    chain_rows.extend(rollup_chain_lineage(name, refs).rows)
                    d = datasource_row(name, getattr(ti, "datasource", None))
                    if d is not None:
                        ds_rows.append(d)
                except Exception as exc:  # noqa: BLE001 - isolate per-process failures
                    click.echo(f"  (skip {name}: {type(exc).__name__})")

            # Chores are instance-level: read once, INSIDE the with-block (client open).
            try:
                chore_rows = ChoreReader(client).read_all()
            except Exception as exc:  # noqa: BLE001 - isolate chore-read failures
                click.echo(f"  (skip chores: {type(exc).__name__})")
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    vis_js = ""
    if vis_js_path:
        vis_js = Path(vis_js_path).read_text(encoding="utf-8")

    graph = build_graph(cube_rows, chain_rows, ds_rows, chore_rows)
    html_text = render_html(graph, title=title, vis_js=vis_js)
    Path(out_path).write_text(html_text, encoding="utf-8")

    click.echo(
        f"Wrote {out_path}: {len(graph.process_ids())} processes, "
        f"{len(graph.cube_ids())} cubes, {graph.edge_count} relationships."
    )
    if not vis_js:
        click.echo("Open it in a browser. (First load fetches vis-network from a CDN;")
        click.echo(" download vis-network.min.js and pass --vis-js <path> for a fully")
        click.echo(" offline file.)")


if __name__ == "__main__":
    main()
