"""tm1dd command-line interface."""

from __future__ import annotations

import click

from tm1_data_dictionary import __version__


@click.group()
@click.version_option(version=__version__, prog_name="tm1dd")
def main() -> None:
    """TM1 Data Dictionary command-line tool."""


@main.command()
def check() -> None:
    """Run the environment diagnostic."""
    from scripts.check_environment import main as check_main  # noqa: PLC0415

    check_main()


if __name__ == "__main__":
    main()
