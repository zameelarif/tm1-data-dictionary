"""Write names and cells to TM1 so that one bad name can never sink a whole run.

TM1 accepts element names with hidden characters (a line break typed inside a quoted name
in a rule, a tab or non-breaking space pasted into TI), but then cannot find them again
when cells are written - and a single unknown member fails the entire cellset write. That
is how one typo in one rule cost every row of ``}Meta_Rule_Element_Reference``.

Three helpers protect every writer that uses names taken from rule or TI text:

- :func:`safe_name` - replaces hidden characters with a visible marker (``<LF>``,
  ``<TAB>``, ``<NBSP>``, ...), so the name is a valid member *and* the problem can be
  seen in PAfE. Clean names are returned unchanged.
- :func:`ensure_elements` - creates missing elements once per TM1-distinct name (TM1
  ignores case and spaces), reading each dimension's names once. A name TM1 refuses is
  recorded, not raised.
- :func:`write_rows` - writes cells in batches; if a batch fails, its rows are retried
  one at a time, and only the rows that still fail are skipped and reported.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

NUMERIC = "Numeric"
BATCH_SIZE = 500

# Characters that are invisible or break TM1 member lookups, and how they are shown.
_NAMED: dict[str, str] = {
    "\n": "<LF>",
    "\r": "<CR>",
    "\t": "<TAB>",
    "\u00a0": "<NBSP>",
    "\u200b": "<ZWSP>",
    "\u200c": "<ZWNJ>",
    "\u200d": "<ZWJ>",
    "\u2060": "<WJ>",
    "\ufeff": "<BOM>",
}
BLANK_NAME = "(blank)"
HIDDEN_CHARACTER_NOTE = "name contains hidden characters ({}) - likely a typo"


def _is_hidden(char: str) -> bool:
    return char in _NAMED or ord(char) < 32 or 127 <= ord(char) < 160


def _marker(char: str) -> str:
    return _NAMED.get(char, f"<U+{ord(char):04X}>")


def hidden_characters(name: str) -> list[str]:
    """Return the markers of the hidden characters in ``name``, in order, without repeats."""
    found: list[str] = []
    for char in name:
        if _is_hidden(char):
            marker = _marker(char)
            if marker not in found:
                found.append(marker)
    return found


def safe_name(name: str) -> str:
    """Return ``name`` with hidden characters shown as markers; blank becomes ``(blank)``."""
    if not any(_is_hidden(c) for c in name):
        return name if name.strip() else BLANK_NAME
    shown = "".join(_marker(c) if _is_hidden(c) else c for c in name)
    return shown if shown.strip() else BLANK_NAME


def hidden_character_note(name: str) -> str:
    """Return a short note for a name with hidden characters, or ``""`` for a clean name."""
    markers = hidden_characters(name)
    return HIDDEN_CHARACTER_NOTE.format(", ".join(markers)) if markers else ""


def norm(name: str) -> str:
    """TM1 names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


@dataclass
class WriteReport:
    """What a writer could not write. Shared across writers in one run."""

    failed_elements: list[tuple[str, str, str]] = field(default_factory=list)  # dim, name, err
    failed_rows: list[tuple[str, str, str]] = field(default_factory=list)  # cube, row, error

    @property
    def rows_not_written(self) -> int:
        return len(self.failed_rows)

    def as_lines(self, limit: int = 10) -> list[str]:
        lines: list[str] = []
        if self.failed_rows:
            lines.append(f"Rows not written: {len(self.failed_rows)}")
            for cube, row, error in self.failed_rows[:limit]:
                lines.append(f"  {cube}: {row} - {error}")
            if len(self.failed_rows) > limit:
                lines.append(f"  ... and {len(self.failed_rows) - limit} more")
        if self.failed_elements:
            lines.append(f"Names TM1 refused: {len(self.failed_elements)}")
            for dim, name, error in self.failed_elements[:limit]:
                lines.append(f"  {dim}: {name} - {error}")
        return lines


def ensure_elements(
    service: Any,
    element_cls: Any,
    dimension: str,
    names: Iterable[str],
    *,
    element_type: str = NUMERIC,
    report: WriteReport | None = None,
) -> None:
    """Create the names missing from ``dimension`` (default hierarchy), once each.

    Reads the dimension's names once (falling back to per-name checks if that fails),
    de-duplicates case- and space-insensitively, tolerates "already exists", and records
    any other refusal in ``report`` instead of raising.
    """
    existing: set[str] | None
    try:
        existing = {norm(n) for n in service.elements.get_element_names(dimension, dimension)}
    except Exception:  # noqa: BLE001 - fake services or older TM1py: check one by one
        existing = None
    done: set[str] = set()
    for name in sorted(set(names)):
        key = norm(name)
        if key in done:
            continue
        done.add(key)
        try:
            if existing is not None:
                if key in existing:
                    continue
            elif service.elements.exists(dimension, dimension, name):
                continue
            service.elements.create(dimension, dimension, element_cls(name, element_type))
        except Exception as exc:  # noqa: BLE001 - one refused name must not stop the run
            if "already exists" not in str(exc).lower():
                if report is not None:
                    report.failed_elements.append((dimension, name, _short(exc)))
                continue
        if existing is not None:
            existing.add(key)


def _short(exc: Exception, limit: int = 160) -> str:
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def write_rows(
    service: Any,
    cube: str,
    rows: list[tuple[tuple[str, ...], dict[str, object]]],
    *,
    batch_size: int = BATCH_SIZE,
    report: WriteReport | None = None,
) -> int:
    """Write ``(key, {measure: value})`` rows; return how many rows were written.

    Rows go in batches. A failed batch is retried row by row; rows that still fail are
    skipped and recorded in ``report`` (cube, row key, error).
    """

    def cellset(chunk: list[tuple[tuple[str, ...], dict[str, object]]]) -> dict:
        cells: dict[tuple[str, ...], object] = {}
        for key, measures in chunk:
            for measure, value in measures.items():
                cells[key + (measure,)] = value
        return cells

    written = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        try:
            service.cells.write(cube_name=cube, cellset_as_dict=cellset(chunk))
            written += len(chunk)
            continue
        except Exception:  # noqa: BLE001 - retry the batch one row at a time
            pass
        for key, measures in chunk:
            try:
                service.cells.write(cube_name=cube, cellset_as_dict=cellset([(key, measures)]))
                written += 1
            except Exception as exc:  # noqa: BLE001 - skip this row, keep the rest
                if report is not None:
                    report.failed_rows.append((cube, " / ".join(key), _short(exc)))
    return written
