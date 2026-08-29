"""tm1dd command-line interface."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click

from tm1_data_dictionary import __version__
from tm1_data_dictionary.bootstrap import ensure_schema
from tm1_data_dictionary.config import ConfigError, load_config
from tm1_data_dictionary.credentials import (
    KEYRING_SERVICE_NAME,
    CredentialError,
    get_keyring_secret,
    set_keyring_secret,
)
from tm1_data_dictionary.parser.assignments import summarize_variables
from tm1_data_dictionary.parser.blocks import code_lines
from tm1_data_dictionary.parser.const_prop import build_const_table
from tm1_data_dictionary.parser.references import extract_references
from tm1_data_dictionary.parser.ti_reader import TIReader
from tm1_data_dictionary.schema import audit_schema
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError
from tm1_data_dictionary.writers.audit_writer import AuditWriter


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

    Idempotent: objects that already exist are left untouched. Honours dry-run mode in
    config (no changes are made if run.dry_run is true).
    """
    try:
        cfg = load_config(Path(config_path))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    schema = audit_schema()
    try:
        with TM1Client(cfg) as client:
            result = ensure_schema(client, schema)
    except TM1ClientError as exc:
        raise click.ClickException(str(exc)) from exc

    # Report what happened.
    for name in result.dimensions_created:
        click.echo(f"  created dimension  {name}")
    for name in result.dimensions_skipped:
        click.echo(f"  exists  dimension  {name}")
    for name in result.cubes_created:
        click.echo(f"  created cube       {name}")
    for name in result.cubes_skipped:
        click.echo(f"  exists  cube       {name}")

    if result.created_anything:
        click.echo("Bootstrap complete: schema created.")
    else:
        click.echo("Bootstrap complete: schema already present, nothing to do.")


SCHEMA_VERSION = "1.1"


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


if __name__ == "__main__":
    main()
