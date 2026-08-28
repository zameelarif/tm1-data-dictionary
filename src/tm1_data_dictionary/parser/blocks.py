"""Segment a TI process into numbered, comment-stripped lines.

Every later parsing stage walks over *lines* - so this module turns the four raw
procedure blocks (Prolog / Metadata / Data / Epilog) into a flat list of
:class:`CodeLine` records, each carrying:

- which block it came from and its 1-based line number *within that block*,
- the raw text (exactly as written), and
- the "code" text with any trailing comment removed and whitespace trimmed.

TI comment handling is specific: a comment starts at ``#`` and runs to end of line. But a
``#`` inside a **single-quoted string literal** is *not* a comment (e.g.
``sMsg = 'Total # of records';``). So comments are stripped by scanning each line and
tracking whether we are inside a string, honouring TM1's doubled-quote escape (``''``).

This module is pure text processing - no TM1, no parsing of meaning. It is the stable
foundation the reference/const-propagation/control-flow passes build on.
"""

from __future__ import annotations

from dataclasses import dataclass

from tm1_data_dictionary.parser.ti_reader import TIProcess

COMMENT_CHAR = "#"
QUOTE_CHAR = "'"


@dataclass(frozen=True)
class CodeLine:
    """One line of a TI block, with its position and comment-stripped code."""

    block: str  # "Prolog" | "Metadata" | "Data" | "Epilog"
    line_no: int  # 1-based, within the block
    raw: str  # exactly as written
    code: str  # comment stripped, whitespace trimmed

    @property
    def is_blank(self) -> bool:
        """True if there is no code left after stripping the comment and whitespace."""
        return self.code == ""

    @property
    def is_comment(self) -> bool:
        """True if the raw line had content but it was entirely a comment."""
        return self.code == "" and self.raw.strip() != ""


def strip_comment(line: str) -> str:
    """Return ``line`` with any trailing ``#`` comment removed (string-aware).

    A ``#`` only starts a comment when it is *outside* a single-quoted string. TM1 escapes
    a single quote inside a string by doubling it (``''``), which this scanner honours.
    """
    in_string = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_string:
            if ch == QUOTE_CHAR:
                # A doubled '' is an escaped quote, not the end of the string.
                if i + 1 < n and line[i + 1] == QUOTE_CHAR:
                    i += 2
                    continue
                in_string = False
        else:
            if ch == QUOTE_CHAR:
                in_string = True
            elif ch == COMMENT_CHAR:
                return line[:i]
        i += 1
    return line


def _segment_block(block_name: str, text: str) -> list[CodeLine]:
    """Split one block's text into CodeLine records (1-based line numbers)."""
    lines: list[CodeLine] = []
    if not text:
        return lines
    for idx, raw in enumerate(text.splitlines(), start=1):
        code = strip_comment(raw).strip()
        lines.append(CodeLine(block=block_name, line_no=idx, raw=raw, code=code))
    return lines


def segment(process: TIProcess) -> list[CodeLine]:
    """Return all lines of ``process``, across all four blocks, in execution order."""
    result: list[CodeLine] = []
    for block_name, text in process.iter_blocks():
        result.extend(_segment_block(block_name, text))
    return result


def code_lines(process: TIProcess) -> list[CodeLine]:
    """Return only the lines that contain actual code (blanks and comments removed)."""
    return [line for line in segment(process) if not line.is_blank]
