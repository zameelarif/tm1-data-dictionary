"""Scan TI code for a user-supplied watch-list of functions.

The main reference pass (``references.py``) only recognises the fixed set of
functions it needs to derive lineage. This module is deliberately independent of
that whitelist: it reads a plain-text **watch list** of function names and records
every call to them, together with the arguments actually passed.

That answers questions the lineage cubes cannot, for example:

- *"Which processes still call ``ASCIIOutput``, and to which file paths?"*
- *"Where do we use ``ExecuteCommand``, and what command is run?"*
- *"Which processes disable cube logging with ``CubeSetLogChanges``?"*

The watch list is a text file (default ``functions.txt``, alongside
``config.yaml``), one function per line:

.. code-block:: text

    # Functions to capture. Blank lines and #-comments are ignored.
    ASCIIOutput
    ExecuteCommand
    CubeSetLogChanges

Matching is case-insensitive. A missing watch-list file is not an error - the
scan simply returns nothing, so the extractor keeps working without it.

Scanning runs over the *logical* lines produced by
:func:`~tm1_data_dictionary.parser.blocks.code_lines`, so calls split across
several physical lines are handled, and the argument parsing reuses the balanced
-parenthesis helpers from ``references.py`` rather than duplicating them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.references import (
    _extract_arg_string,
    _split_top_level_args,
)

# Matches an identifier immediately followed by "(" - the same shape used by the
# main reference pass.
_NAME_BEFORE_PAREN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(")

DEFAULT_WATCHLIST_FILENAME = "functions.txt"

# Arguments are stored as a single string measure; keep it inside sane TM1 limits.
_MAX_ARGUMENT_LENGTH = 500


@dataclass(frozen=True)
class FunctionCall:
    """One call to a watched function, with the arguments as written."""

    process: str
    function: str  # canonical name, as spelled in the watch list
    occurrence: int  # 1-based, per (process, function)
    block: str
    line_no: int
    arguments: str  # top-level arguments, comma-joined, as written
    arg_count: int

    @property
    def call_key(self) -> str:
        """Return the element name identifying this call, e.g. ``ASCIIOutput#2``."""
        return f"{self.function}#{self.occurrence}"


def load_watchlist(path: str | Path | None) -> dict[str, str]:
    """Return ``{lower_case_name: canonical_name}`` for the watched functions.

    A missing file yields an empty mapping, so function capture is simply skipped
    rather than failing the extraction. Blank lines and ``#`` comments are ignored.
    """
    if path is None:
        return {}

    file_path = Path(path)
    if not file_path.exists():
        return {}

    watched: dict[str, str] = {}
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        name = raw_line.split("#", 1)[0].strip()
        if not name:
            continue
        watched[name.lower()] = name

    return watched


def _truncate(text: str) -> str:
    """Trim an argument string to a length TM1 will happily store."""
    if len(text) <= _MAX_ARGUMENT_LENGTH:
        return text
    return text[: _MAX_ARGUMENT_LENGTH - 3] + "..."


def scan_functions(
    process: str,
    lines: list[CodeLine],
    watched: dict[str, str],
) -> list[FunctionCall]:
    """Return every call to a watched function within one process.

    Args:
        process: the process name, recorded on each call.
        lines: logical code lines from ``blocks.code_lines``.
        watched: mapping from :func:`load_watchlist`.

    Calls are returned in source order. The occurrence number restarts per
    function within the process, so a process calling ``ASCIIOutput`` three times
    yields ``ASCIIOutput#1``, ``#2``, and ``#3``.
    """
    if not watched:
        return []

    calls: list[FunctionCall] = []
    counters: dict[str, int] = {}

    for code_line in lines:
        text = code_line.code
        search_from = 0

        while True:
            match = _NAME_BEFORE_PAREN.search(text, search_from)
            if match is None:
                break

            name = match.group(1)
            canonical = watched.get(name.lower())
            open_paren_idx = match.end() - 1

            if canonical is None:
                # Not a watched function; continue after this name.
                search_from = match.end()
                continue

            inner, end_idx = _extract_arg_string(text, open_paren_idx)
            args = [arg for arg in _split_top_level_args(inner) if arg != ""]

            counters[canonical] = counters.get(canonical, 0) + 1

            calls.append(
                FunctionCall(
                    process=process,
                    function=canonical,
                    occurrence=counters[canonical],
                    block=code_line.block,
                    line_no=code_line.line_no,
                    arguments=_truncate(", ".join(args)),
                    arg_count=len(args),
                )
            )

            search_from = end_idx + 1

    return calls
