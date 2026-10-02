"""Guard the Python 3.10 floor: no ``datetime.UTC`` (added in Python 3.11) in the package."""

from __future__ import annotations

import re
from pathlib import Path

import tm1_data_dictionary

_PACKAGE = Path(tm1_data_dictionary.__file__).parent
_UTC_311 = re.compile(r"from\s+datetime\s+import\s+[^\n]*\bUTC\b|\bdatetime\.UTC\b")


def test_no_module_uses_datetime_utc() -> None:
    offenders = [
        str(path.relative_to(_PACKAGE))
        for path in sorted(_PACKAGE.rglob("*.py"))
        if _UTC_311.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"use timezone.utc instead of UTC (Python 3.10): {offenders}"
