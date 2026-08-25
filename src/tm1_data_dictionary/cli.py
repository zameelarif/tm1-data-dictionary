"""tm1dd command-line interface."""

from __future__ import annotations

import click

from tm1_data_dictionary import __version__
from tm1_data_dictionary.credentials import (
    KEYRING_SERVICE_NAME,
    CredentialError,
    get_keyring_secret,
    set_keyring_secret,
)


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


if __name__ == "__main__":
    main()
