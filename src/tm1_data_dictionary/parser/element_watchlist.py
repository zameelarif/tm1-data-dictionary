"""Search every TI process for a user-supplied list of elements (element lineage).

The static pass (:mod:`element_scan`) explains most element references. The watch list is
the safety net: *every* place a listed element appears in TI code, explained or not -
the same answer a text search gives, but classified and stored.

The file (default ``elements.txt`` beside ``config.yaml``) lists dimensions in square
brackets, each followed by its elements, one per line::

    # Elements to trace. Blank lines and #-comments are ignored.
    [Version]
    Budget
    [Account]
    4000
    Opening Balance

Matching is case- and space-insensitive, and a listed element also matches its aliases
(the caller supplies them). Comments in TI code are ignored.

Each hit is classified:

- **explained** - the static pass already recorded this element in this process (the
  hit is the literal that a recorded call, subset or variable uses);
- **Compare** - the literal is compared with ``@=`` / ``@<>`` (filters, skips);
- **Reference** - the literal is an argument of a function tm1dd does not catalogue
  (``DIMIX``, ``CellIsUpdateable`` ...), named in ``Function``;
- **Unexplained** - anything else, e.g. the element inside a longer string or an
  assignment whose value tm1dd could not follow. Each one shows a parser gap.

Pure text analysis - no TM1, no I/O beyond reading the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.var_trace import QUOTE, normalise

DEFAULT_ELEMENT_WATCHLIST_FILENAME = "elements.txt"

ROLE_COMPARE = "Compare"
ROLE_REFERENCE = "Reference"
ROLE_UNEXPLAINED = "Unexplained"

_SECTION = re.compile(r"^\[(.+)\]$")
_NAME_BEFORE_PAREN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\($")
_COMPARE_BEFORE = re.compile(r"@(=|<>)\s*$")
_COMPARE_AFTER = re.compile(r"^\s*@(=|<>)")


class WatchlistError(ValueError):
    """The element watch list could not be read."""


@dataclass
class ElementWatchlist:
    """Dimensions and the elements to trace in each (as written in the file)."""

    entries: dict[str, list[str]] = field(default_factory=dict)  # dimension -> elements

    @property
    def element_count(self) -> int:
        return sum(len(v) for v in self.entries.values())

    def __bool__(self) -> bool:
        return self.element_count > 0


def load_element_watchlist(path: str | Path | None) -> ElementWatchlist:
    """Read the watch list. A missing file gives an empty list (no error)."""
    result = ElementWatchlist()
    if path is None:
        return result
    file_path = Path(path)
    if not file_path.exists():
        return result
    current: str | None = None
    text = file_path.read_text(encoding="utf-8-sig")
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        section = _SECTION.match(line)
        if section:
            current = section.group(1).strip()
            result.entries.setdefault(current, [])
            continue
        if current is None:
            raise WatchlistError(
                f"{file_path.name} line {number}: '{line}' comes before any [Dimension] line"
            )
        if line not in result.entries[current]:
            result.entries[current].append(line)
    return result


@dataclass(frozen=True)
class LiteralSpan:
    """A single-quoted string literal in a code line."""

    value: str
    start: int  # index of the opening quote
    end: int  # index just after the closing quote


def string_literals(code: str) -> list[LiteralSpan]:
    """Return every single-quoted literal in ``code`` (honouring the '' escape)."""
    spans: list[LiteralSpan] = []
    i = 0
    n = len(code)
    while i < n:
        if code[i] != QUOTE:
            i += 1
            continue
        start = i
        i += 1
        chars: list[str] = []
        while i < n:
            if code[i] == QUOTE:
                if i + 1 < n and code[i + 1] == QUOTE:
                    chars.append(QUOTE)
                    i += 2
                    continue
                break
            chars.append(code[i])
            i += 1
        spans.append(LiteralSpan("".join(chars), start, min(i + 1, n)))
        i += 1
    return spans


def enclosing_function(code: str, position: int) -> str | None:
    """Return the name of the innermost function call whose parentheses contain ``position``."""
    depth = 0
    in_string = False
    i = position - 1
    # Walk backwards to the unmatched '(' (quotes are balanced, so toggling is safe).
    while i >= 0:
        ch = code[i]
        if ch == QUOTE:
            in_string = not in_string
        elif not in_string:
            if ch == ")":
                depth += 1
            elif ch == "(":
                if depth == 0:
                    match = _NAME_BEFORE_PAREN.search(code[: i + 1])
                    if match:
                        return match.group(1)
                    return None
                depth -= 1
        i -= 1
    return None


@dataclass(frozen=True)
class WatchHit:
    """One appearance of a watched element in a process."""

    process: str
    dimension: str  # as listed in the watch list
    element: str  # principal name (or as listed, if the dimension could not be read)
    written_as: str  # the literal as it appears in the code
    role: str  # Compare | Reference | Unexplained (explained hits are not returned)
    block: str
    line_no: int
    function: str
    statement: str


def scan_watchlist(
    process: str,
    lines: list[CodeLine],
    lookup: dict[str, tuple[str, str]],
    explained: set[tuple[str, str]],
) -> tuple[list[WatchHit], set[tuple[str, str]]]:
    """Find watched elements in one process.

    Args:
        process: the process name.
        lines: logical code lines (comments already stripped).
        lookup: ``{normalised name or alias: (dimension, principal element)}``.
        explained: ``{(normalised dimension, normalised element)}`` the static pass
            already recorded for this process.

    Returns:
        The hits that need their own row, and every (dimension, element) seen at all
        (explained or not), so the caller can report watched elements with no hits.
    """
    hits: list[WatchHit] = []
    seen: set[tuple[str, str]] = set()
    if not lookup:
        return hits, seen
    for line in lines:
        code = line.code
        if not code:
            continue
        for span in string_literals(code):
            key = normalise(span.value)
            match = lookup.get(key)
            inside = None
            if match is None:
                # Element embedded in a longer string, e.g. 'Product Type:' | x.
                for candidate, target in lookup.items():
                    if candidate and len(candidate) > 2 and candidate in key:
                        inside = target
                        break
                if inside is None:
                    continue
            dimension, element = match or inside  # type: ignore[misc]
            seen.add((normalise(dimension), normalise(element)))
            if match is not None and (normalise(dimension), normalise(element)) in explained:
                continue
            function = enclosing_function(code, span.start) or ""
            if inside is not None:
                role = ROLE_UNEXPLAINED
                function = function or "(inside a longer string)"
            elif _COMPARE_BEFORE.search(code[: span.start]) or _COMPARE_AFTER.match(
                code[span.end :]
            ):
                role = ROLE_COMPARE
            elif function:
                role = ROLE_REFERENCE
            else:
                role = ROLE_UNEXPLAINED
                function = "(assignment)" if "=" in code[: span.start] else "(none)"
            hits.append(
                WatchHit(
                    process=process,
                    dimension=dimension,
                    element=element,
                    written_as=span.value,
                    role=role,
                    block=line.block,
                    line_no=line.line_no,
                    function=function,
                    statement=" ".join(code.split())[:250],
                )
            )
    return hits, seen
