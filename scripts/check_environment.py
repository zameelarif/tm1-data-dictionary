"""Backwards-compatible entry point: runs ``tm1dd check``.

The diagnostic now lives in the package (``tm1_data_dictionary.env_check``) so it also works
from an installed wheel. Arguments are passed through, e.g.::

    python scripts/check_environment.py --env dev
"""

from __future__ import annotations

import sys

from tm1_data_dictionary.cli import main

if __name__ == "__main__":
    sys.exit(main(["check", *sys.argv[1:]]))
