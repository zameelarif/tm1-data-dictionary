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


if __name__ == "__main__":
    main()
