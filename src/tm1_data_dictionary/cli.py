"""tm1dd command-line interface."""

from __future__ import annotations

import getpass
from datetime import UTC, datetime
from pathlib import Path

import click

from tm1_data_dictionary import __version__
from tm1_data_dictionary.bootstrap import (
    STATUS_ADD,
    STATUS_CREATE,
    STATUS_REBUILD,
    all_schemas,
    check_schema,
    ensure_schema,
    execute_rebuild,
    plan_rebuild,
)
from tm1_data_dictionary.chore_reader import ChoreReader
from tm1_data_dictionary.config import ConfigError, load_config
from tm1_data_dictionary.credentials import (
    KEYRING_SERVICE_NAME,
    CredentialError,
    get_keyring_secret,
    set_keyring_secret,
)
from tm1_data_dictionary.env_check import run_checks
from tm1_data_dictionary.exclusions import ExclusionRules, partition
from tm1_data_dictionary.extract import extract_all
from tm1_data_dictionary.extract_rules import extract_all_rules
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
from tm1_data_dictionary.schema import LEGACY_CUBES
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError
from tm1_data_dictionary.views import DEFAULT_PREFIX, create_views
from tm1_data_dictionary.writers.audit_writer import AuditWriter
from tm1_data_dictionary.writers.process_chain_writer import write_chain_lineage
from tm1_data_dictionary.writers.process_cube_writer import write_cube_lineage

SCHEMA_VERSION = "1.7"


# --------------------------------------------------------------------------- #
# Shared options
# --------------------------------------------------------------------------- #


def _config_option(func):
    """Attach the --config option to a command."""
    return click.option(
        "--config",
        "config_path",
        default="config.yaml",
        show_default=True,
        help="Path to config.yaml.",
    )(func)


def _env_option(func):
    """Attach the --env option to a command.

    Selects a named environment from an ``environments`` block in config.yaml.
    If omitted, ``default_environment`` is used (or the legacy single block).
    """
    return click.option(
        "--env",
        "environment",
        default=None,
        help="Named environment from config.yaml (e.g. dev, demo).",
    )(func)


def _load(config_path: str, environment: str | None):
    """Load config for the selected environment, raising a Click error nicely."""
    try:
        return load_config(Path(config_path), environment=environment)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _echo_env(cfg) -> None:
    """Print which environment was used, when one is named."""
    if cfg.environment:
        click.echo(f"Environment: {cfg.environment}")


@click.group()
@click.version_option(version=__version__, prog_name="tm1dd")
def main() -> None:
    """TM1 Data Dictionary command-line tool."""


@main.command()
@_config_option
@_env_option
def check(config_path: str, environment: str | None) -> None:
    """Check Python, config, the TM1 connection and write permission.

    The write test creates and deletes a scratch dimension (}Meta_ConnCheck_Scratch);
    it is skipped in dry-run mode.
    """
    results = run_checks(Path(config_path), environment)
    for result in results:
        click.echo(f"  {result.status}  {result.name:<16} {result.detail}")
    failed = [r for r in results if not r.ok]
    if failed:
        raise click.ClickException(f"{len(failed)} check(s) failed. Fix and re-run.")
    click.echo("All checks passed.")


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


def _print_check(check) -> None:  # noqa: ANN001
    """Print a schema check: one line per object that is not OK, then a summary."""
    labels = {
        STATUS_CREATE: "missing  - bootstrap will create it",
        STATUS_ADD: "outdated - bootstrap will add",
        STATUS_REBUILD: "REBUILD  -",
    }
    for item in check.items:
        if item.status in labels:
            detail = f" {item.detail}" if item.detail else ""
            click.echo(f"  {item.kind:<9} {item.name:<34} {labels[item.status]}{detail}")
    ok = len(check.with_status("OK"))
    click.echo(
        f"{ok} OK, {len(check.with_status(STATUS_CREATE))} to create, "
        f"{len(check.with_status(STATUS_ADD))} to update, "
        f"{len(check.with_status(STATUS_REBUILD))} need a rebuild."
    )


def _rebuild_hint(cubes: list[str], env_flag: str) -> str:
    names = " ".join(f'--rebuild-cube "{name}"' for name in cubes)
    return f"tm1dd bootstrap{env_flag} {names}"


def _run_check(client: TM1Client, env_flag: str) -> None:
    check = check_schema(client)
    _print_check(check)
    if check.up_to_date:
        click.echo("Schema is up to date. Nothing to do.")
        return
    if check.with_status(STATUS_CREATE) or check.with_status(STATUS_ADD):
        click.echo(f"Run: tm1dd bootstrap{env_flag}   (adds what is missing, deletes nothing)")
    if check.cubes_to_rebuild:
        click.echo("These cubes cannot be fixed in place. Rebuild them (their data and views")
        click.echo("are deleted; re-run the extractions and create-views afterwards):")
        click.echo(f"  {_rebuild_hint(check.cubes_to_rebuild, env_flag)}")


def _run_rebuild(client: TM1Client, names: list[str], label: str, assume_yes: bool) -> bool:
    """Delete the named cubes (after confirmation). Return True if anything was deleted."""
    try:
        plan = plan_rebuild(client, names)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    verb = "would delete" if client.dry_run else "delete"
    for name in plan.not_found:
        click.echo(f"  {name} does not exist yet - it will simply be created")
    for name in plan.cubes:
        click.echo(f"  {verb}  cube       {name}")
        views = plan.views.get(name, [])
        if views:
            click.echo(f"           with {len(views)} public view(s): {', '.join(views)}")
    for name in plan.dimensions:
        click.echo(f"  {verb}  dimension  {name}  (element types changed)")
    if not plan.cubes:
        return False
    if client.dry_run:
        click.echo("Dry-run: nothing deleted.")
        return False
    if not assume_yes:
        click.confirm(
            f"Delete {len(plan.cubes)} cube(s) in '{label}' and create them again? "
            "Their data and views are lost",
            abort=True,
        )
    result = execute_rebuild(client, plan)
    for name in result.deleted_cubes:
        click.echo(f"  deleted cube       {name}")
    for name in result.deleted_dimensions:
        click.echo(f"  deleted dimension  {name}")
    for name, error in result.failed:
        click.echo(f"  FAILED to delete {name}: {error}")
    return bool(result.deleted_cubes)


@main.command()
@_config_option
@_env_option
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    default=False,
    help="Read-only: report what is missing, outdated or needs a rebuild. Changes nothing.",
)
@click.option(
    "--rebuild-cube",
    "rebuild_cubes",
    multiple=True,
    metavar="NAME",
    help="Delete this tm1dd cube and create it again (repeatable). Its data and views "
    "are lost. Only needed when --check says REBUILD.",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help="With --rebuild-cube, do not ask for confirmation (for scripts).",
)
@click.option(
    "--drop-legacy",
    is_flag=True,
    default=False,
    help="Also delete cubes from older schema versions that tm1dd no longer writes.",
)
def bootstrap(
    config_path: str,
    environment: str | None,
    check_only: bool,
    rebuild_cubes: tuple[str, ...],
    assume_yes: bool,
    drop_legacy: bool,
) -> None:
    """Create the }Meta_* schema, and bring an existing one up to date.

    \b
    Default:          create missing dimensions and cubes, and add missing elements
                      (e.g. new measures) to existing dimensions. Never deletes or
                      changes existing data or views. Safe on an instance in use.
    --check:          read-only report of what is missing, outdated or needs a rebuild.
    --rebuild-cube:   delete the named tm1dd cube(s) and create them again - only
                      for changes that cannot be applied in place (--check says so).
    --drop-legacy:    delete cubes renamed in schema 1.6.
    """
    if check_only and (rebuild_cubes or drop_legacy):
        raise click.UsageError("--check is read-only; use it on its own.")
    if assume_yes and not rebuild_cubes:
        raise click.UsageError("--yes only applies with --rebuild-cube.")
    cfg = _load(config_path, environment)
    _echo_env(cfg)
    label = cfg.environment or "default environment"
    env_flag = f" --env {cfg.environment}" if cfg.environment else ""
    rebuilt = False
    dropped: list[str] = []
    try:
        with TM1Client(cfg) as client:
            if check_only:
                _run_check(client, env_flag)
                return
            if rebuild_cubes:
                rebuilt = _run_rebuild(client, list(rebuild_cubes), label, assume_yes)
                if client.dry_run:
                    return
            results = tuple(ensure_schema(client, schema) for schema in all_schemas())
            if drop_legacy:
                for name in LEGACY_CUBES:
                    if not client.service.cubes.exists(name):
                        continue
                    client.ensure_writable("delete legacy cube")
                    client.service.cubes.delete(name)
                    dropped.append(name)
            remaining = check_schema(client).cubes_to_rebuild
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    for name in dropped:
        click.echo(f"  deleted legacy cube {name}")
    for result in results:
        for name in result.dimensions_created:
            click.echo(f"  created dimension  {name}")
        for dim, element in result.elements_added:
            click.echo(f"  added element      {dim} / {element}")
        for name in result.cubes_created:
            click.echo(f"  created cube       {name}")

    if any(r.created_anything for r in results):
        click.echo("Bootstrap complete: schema created or updated.")
    else:
        click.echo("Bootstrap complete: schema already present, nothing to do.")
    if rebuilt:
        click.echo("Next: refill the rebuilt cubes and their views:")
        click.echo(f"  tm1dd extract{env_flag}")
        click.echo(f"  tm1dd extract-rules{env_flag}")
        click.echo(f"  tm1dd create-views{env_flag}")
    if remaining:
        click.echo(f"WARNING: {len(remaining)} cube(s) have an outdated shape and need a rebuild:")
        click.echo(f"  {_rebuild_hint(remaining, env_flag)}")


@main.command(name="create-views")
@_config_option
@_env_option
@click.option(
    "--prefix",
    default=DEFAULT_PREFIX,
    show_default=True,
    help="Text every view name starts with.",
)
def create_views_cmd(config_path: str, environment: str | None, prefix: str) -> None:
    """Create (or replace) public views on every }Meta_* cube.

    One default view per cube ('<prefix> All') plus focused views such as
    'Broken References', 'Missing Cubes' and 'Hierarchy Functions'. Re-run after an
    upgrade to refresh them. Honours dry-run mode.
    """
    cfg = _load(config_path, environment)
    _echo_env(cfg)
    try:
        with TM1Client(cfg) as client:
            result = create_views(client, prefix=prefix)
            dry = client.dry_run
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    verb = "would write" if dry else "wrote"
    for label in result.written:
        click.echo(f"  {verb}  {label}")
    for label in result.skipped_no_cube:
        click.echo(f"  skipped (cube missing - run bootstrap)  {label}")
    for label in result.skipped_no_element:
        click.echo(f"  skipped (nothing to show yet)  {label}")
    for label, error in result.failed:
        click.echo(f"  FAILED  {label}: {error}")
    skipped = len(result.skipped_no_cube) + len(result.skipped_no_element)
    click.echo(
        f"{len(result.written)} view(s) {verb}, {skipped} skipped, {len(result.failed)} failed."
    )
    if result.failed:
        raise click.ClickException("Some views could not be created (see above).")


@main.command(name="record-run")
@_config_option
@_env_option
@click.option(
    "--status",
    default="Success",
    show_default=True,
    help="Exit status to record for this run.",
)
def record_run(config_path: str, environment: str | None, status: str) -> None:
    """Write one run record into }Meta_Extraction_Audit.

    Useful for proving the write path end-to-end: it records a row with the current
    extractor version, start/end time, and status. Honours dry-run mode in config.
    """
    cfg = _load(config_path, environment)
    _echo_env(cfg)
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
@_config_option
@_env_option
@click.option(
    "--contains", default="", help="Only show names containing this text (case-insensitive)."
)
def list_processes(config_path: str, environment: str | None, contains: str) -> None:
    """List TI process names in the instance (optionally filtered)."""
    cfg = _load(config_path, environment)
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
@_config_option
@_env_option
def inspect_process(name: str, config_path: str, environment: str | None) -> None:
    """Print a summary of a single TI process (blocks, datasource, variables, parameters)."""
    cfg = _load(config_path, environment)
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
@_config_option
@_env_option
def extract_refs(name: str, config_path: str, environment: str | None) -> None:
    """Extract and print the function references (lineage) from a single TI process.

    Uses const-propagation so variable targets (e.g. cCube) are resolved to their
    literal values (e.g. WeeklySales) where it is safe to do so.
    """
    cfg = _load(config_path, environment)
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
@_config_option
@_env_option
@click.option(
    "--all-assignments",
    is_flag=True,
    default=False,
    help="Show every assignment (not just one summary line per variable).",
)
def show_vars(name: str, config_path: str, environment: str | None, all_assignments: bool) -> None:
    """Show the variable dictionary for a TI: every variable and where its value comes from.

    Complements 'extract-refs': where const-propagation cannot safely resolve a variable
    (e.g. cCube set from a cube read), this shows the raw assignment(s) so a developer can
    trace it by hand.
    """
    cfg = _load(config_path, environment)
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
@_config_option
@_env_option
def extract_cube(name: str, config_path: str, environment: str | None) -> None:
    """Parse a TI's cube lineage and write it into }Meta_Process_Cube.

    Honours dry-run mode in config (parses and reports, but writes nothing).
    """
    cfg = _load(config_path, environment)
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
@_config_option
@_env_option
def extract_chain(name: str, config_path: str, environment: str | None) -> None:
    """Parse a TI's chain dependencies and write them into }Meta_Process_Chain."""
    cfg = _load(config_path, environment)
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
@_config_option
@_env_option
@click.option(
    "--functions",
    "functions_file",
    default=None,
    help="Function watch list (default: functions.txt beside config.yaml).",
)
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Suppress per-process progress lines (show only the summary).",
)
def extract(
    config_path: str,
    environment: str | None,
    functions_file: str | None,
    quiet: bool,
) -> None:
    """Extract cube, chain, datasource, chore, dimension, and function usage for
    EVERY process.

    Also flags, per cube-lineage row, whether the referenced cube exists (CubeExists).
    Applies exclusion rules. One malformed process does not abort the run. Records the
    run (who/when/status) into }Meta_Extraction_Audit. Honours dry-run mode.
    """
    cfg = _load(config_path, environment)
    _echo_env(cfg)

    def _progress(i: int, total: int, name: str, status: str) -> None:
        if not quiet:
            click.echo(f"  [{i:>4}/{total}] {name:<50} {status}")

    start = datetime.now(UTC)
    run_by = f"{getpass.getuser()} via {cfg.connection.user}"
    audit_recorded = False

    # Default the watch list to functions.txt beside config.yaml.
    watchlist = functions_file or (Path(config_path).parent / "functions.txt")

    try:
        with TM1Client(cfg) as client:
            if client.dry_run:
                click.echo("Dry-run: parsing all processes, nothing will be written.")
            click.echo("Extracting lineage for all processes...")
            summary = extract_all(
                client,
                progress=_progress,
                functions_file=watchlist,
            )

            if not client.dry_run:
                status = "Success" if summary.failed == 0 else "CompletedWithFailures"
                warnings = f"{summary.failed} process(es) failed" if summary.failed else ""
                try:
                    AuditWriter(client).record_run(
                        extractor_version=__version__,
                        schema_version=SCHEMA_VERSION,
                        start_time=start,
                        exit_status=status,
                        run_by=run_by,
                        warnings=warnings,
                        metrics={
                            "processes_total": summary.total_processes,
                            "processes_included": summary.included,
                            "processes_excluded": summary.excluded,
                            "processes_failed": summary.failed,
                            "cube_rows": summary.cube_rows_written,
                            "chain_rows": summary.chain_rows_written,
                            "datasource_rows": summary.datasource_rows_written,
                            "chore_rows": summary.chore_rows_written,
                            "dimension_rows": summary.dimension_rows_written,
                            "function_rows": summary.function_rows_written,
                            "unresolved_cube_refs": summary.unresolved_cube_refs,
                            "unresolved_chain_refs": summary.unresolved_chain_refs,
                            "unresolved_dim_refs": summary.unresolved_dim_refs,
                            "missing_cube_refs": summary.missing_cube_refs,
                        },
                    )
                    audit_recorded = True
                except Exception as exc:  # noqa: BLE001
                    click.echo(f"  Audit record not written: {exc}")
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("")
    click.echo("Extraction complete.")
    for line in summary.as_lines():
        click.echo(f"  {line}")
    if audit_recorded:
        click.echo(f"  Run recorded in }}Meta_Extraction_Audit (RunBy: {run_by})")
    elif not summary.dry_run:
        click.echo("  Extraction succeeded, but the audit record was not written.")


@main.command(name="extract-rules")
@_config_option
@_env_option
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Suppress per-cube progress lines (show only the summary).",
)
def extract_rules_cmd(config_path: str, environment: str | None, quiet: bool) -> None:
    """Extract rule facts for EVERY cube.

    Phase 2a: whether each cube has rules and feeders, which pragmas are set (SKIPCHECK,
    FEEDSTRINGS, UNDEFVALS), and rule/feeder statement counts, into }Meta_Rule_Cube.

    Phase 2b: every cross-cube DB() reference in rules and feeders, into
    }Meta_Rule_Dependency (including references to cubes that do not exist).

    Phase 2c: every literal element name in rules and feeders, resolved to its dimension
    and flagged ElementExists, into }Meta_Rule_Element_Reference.

    Phase 2d: every function and keyword (STET, CONTINUE, ISLEAF) used in rules and
    feeders, with a category, into }Meta_Rule_Function.

    Phase 2e: feeder gaps - unfed rules, dead feeders and feeders that feed no rule -
    into }Meta_Rule_Feeder_Finding.

    Applies the rule exclusion list (control cubes by default). One unreadable cube does
    not abort the run. Records the run into }Meta_Extraction_Audit. Honours dry-run mode.

    Deliberately separate from 'extract' so TI and rule extraction succeed or fail
    independently.
    """
    cfg = _load(config_path, environment)
    _echo_env(cfg)

    def _progress(i: int, total: int, name: str, status: str) -> None:
        if not quiet:
            click.echo(f"  [{i:>4}/{total}] {name:<50} {status}")

    start = datetime.now(UTC)
    run_by = f"{getpass.getuser()} via {cfg.connection.user}"
    audit_recorded = False

    try:
        with TM1Client(cfg) as client:
            if client.dry_run:
                click.echo("Dry-run: reading all cube rules, nothing will be written.")
            click.echo("Extracting rule facts for all cubes...")
            summary = extract_all_rules(client, progress=_progress)

            if not client.dry_run:
                status = "Success" if summary.failed == 0 else "CompletedWithFailures"
                warnings = f"{summary.failed} cube(s) failed" if summary.failed else ""
                try:
                    AuditWriter(client).record_run(
                        extractor_version=__version__,
                        schema_version=SCHEMA_VERSION,
                        start_time=start,
                        exit_status=status,
                        run_by=run_by,
                        warnings=warnings,
                        metrics={
                            "cubes_total": summary.total_cubes,
                            "cubes_included": summary.included,
                            "cubes_excluded": summary.excluded,
                            "cubes_failed": summary.failed,
                            "rule_cube_rows": summary.rule_cube_rows_written,
                            "cubes_with_rules": summary.cubes_with_rules,
                            "cubes_with_feeders": summary.cubes_with_feeders,
                            "cubes_with_skipcheck": summary.cubes_with_skipcheck,
                            "rule_dependency_rows": summary.rule_dependency_rows_written,
                            "db_references": summary.db_references,
                            "unresolved_db_references": summary.unresolved_db_references,
                            "dangling_dependencies": summary.dangling_dependencies,
                            "element_reference_rows": summary.element_reference_rows_written,
                            "element_references": summary.element_references,
                            "missing_elements": summary.missing_elements,
                            "ambiguous_elements": summary.ambiguous_elements,
                            "unchecked_elements": summary.unchecked_elements,
                            "elements_resolved_by_tm1": summary.resolved_by_tm1,
                            "rule_function_rows": summary.rule_function_rows_written,
                            "function_uses": summary.function_uses,
                            "distinct_functions": summary.distinct_functions,
                            "cubes_using_hierarchy_functions": (
                                summary.cubes_using_hierarchy_functions
                            ),
                            "feeder_finding_rows": summary.feeder_finding_rows_written,
                            "unfed_rules": summary.unfed_rules,
                            "dead_feeders": summary.dead_feeders,
                            "feeders_feeding_no_rule": summary.feeders_feeding_no_rule,
                            "feeders_without_skipcheck": summary.feeders_without_skipcheck,
                            "unchecked_rules": summary.unchecked_rules,
                            "dynamic_feeder_targets": summary.dynamic_feeder_targets,
                        },
                    )
                    audit_recorded = True
                except Exception as exc:  # noqa: BLE001
                    click.echo(f"  Audit record not written: {exc}")
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("")
    click.echo("Rule extraction complete.")
    for line in summary.as_lines():
        click.echo(f"  {line}")
    if audit_recorded:
        click.echo(f"  Run recorded in }}Meta_Extraction_Audit (RunBy: {run_by})")
    elif not summary.dry_run:
        click.echo("  Extraction succeeded, but the audit record was not written.")


@main.command(name="diagnose-unresolved")
@_config_option
@_env_option
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
    config_path: str,
    environment: str | None,
    top: int,
    process_name: str,
    expression: str | None,
) -> None:
    """Report which cube-target expressions stay unresolved (read-only, no writes).

    Modes:
      * default            - whole-model "top offenders" table;
      * --process NAME     - one process's unresolved references, with line numbers;
      * --expression EXPR  - every process/line where EXPR is the unresolved target
                             (pass --expression "" to find blank-target parse edge cases).
    """
    cfg = _load(config_path, environment)

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
@_config_option
@_env_option
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
def export_graph(
    config_path: str,
    environment: str | None,
    out_path: str,
    title: str,
    vis_js_path: str,
) -> None:
    """Export an interactive HTML data-flow map (processes, cubes, datasources, chores)."""
    cfg = _load(config_path, environment)
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
