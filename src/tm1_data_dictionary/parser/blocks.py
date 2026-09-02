"""Segment a TI process into numbered lines, joining multi-line statements.

Every later parsing stage walks over *lines*. TI code, however, frequently spreads a
single statement across several physical lines for readability, e.g.::

    CellPutN(StringToNumber(vValue),
             cCubTgt,
             vVersion,
             ...);

A naive line-by-line reader would see only ``CellPutN(StringToNumber(vValue),`` on the
first line - its parentheses never close, and the cube argument (``cCubTgt``) lives on the
next line - so it would extract a blank/partial target. To avoid that, this module
distinguishes:

- :func:`segment` - one :class:`CodeLine` per *physical* line (unchanged; used where the
  raw line layout matters), and
- :func:`code_lines` - **logical** lines: physical lines are joined until their parentheses
  are balanced, so a multi-line call becomes one line the parser can analyse whole. Each
  logical line keeps the line number where the statement *started*, so lineage still points
  at the right place.

Comment stripping is string-aware (a ``#`` inside a single-quoted string, honouring the
doubled-quote ``''`` escape, is not a comment). Parenthesis counting for the join is
likewise string-aware, so a ``(`` or ``)`` inside a string literal does not affect balance.

Pure text processing - no TM1, no parsing of meaning.
"""

from __future__ import annotations

from dataclasses import dataclass

from tm1_data_dictionary.parser.ti_reader import TIProcess

COMMENT_CHAR = "#"
QUOTE_CHAR = "'"


@dataclass(frozen=True)
class CodeLine:
    """One line of a TI block, with its position and comment-stripped code.

    For a *logical* line (produced by :func:`code_lines`) that joined several physical
    lines, ``line_no`` is the number of the physical line where the statement started, and
    ``raw``/``code`` hold the joined text.
    """

    block: str  # "Prolog" | "Metadata" | "Data" | "Epilog"
    line_no: int  # 1-based, within the block (start line for a joined statement)
    raw: str  # exactly as written (or the joined text for a logical line)
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


def _paren_delta(code: str) -> int:
    """Return the net change in parenthesis depth for ``code`` (string-aware).

    ``(`` adds 1 and ``)`` subtracts 1, but only outside single-quoted strings (with the
    doubled-quote escape). Used to decide when a multi-line statement is complete.
    """
    depth = 0
    in_string = False
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        if in_string:
            if ch == QUOTE_CHAR:
                if i + 1 < n and code[i + 1] == QUOTE_CHAR:
                    i += 2
                    continue
                in_string = False
        else:
            if ch == QUOTE_CHAR:
                in_string = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        i += 1
    return depth


def _segment_block(block_name: str, text: str) -> list[CodeLine]:
    """Split one block's text into physical CodeLine records (1-based line numbers)."""
    lines: list[CodeLine] = []
    if not text:
        return lines
    for idx, raw in enumerate(text.splitlines(), start=1):
        code = strip_comment(raw).strip()
        lines.append(CodeLine(block=block_name, line_no=idx, raw=raw, code=code))
    return lines


def segment(process: TIProcess) -> list[CodeLine]:
    """Return all *physical* lines of ``process``, across all four blocks, in order."""
    result: list[CodeLine] = []
    for block_name, text in process.iter_blocks():
        result.extend(_segment_block(block_name, text))
    return result


def _logical_lines_for_block(block_name: str, text: str) -> list[CodeLine]:
    """Return the *logical* code lines for one block (multi-line statements joined).

    Physical lines are accumulated until their cumulative parenthesis depth returns to
    zero (or below), at which point the joined statement is emitted as a single
    :class:`CodeLine` carrying the *start* line number. Blank/comment-only lines outside a
    statement are skipped; inside a pending statement they are ignored (the join spans them).
    """
    result: list[CodeLine] = []
    if not text:
        return result

    pending: list[str] = []
    start_line = 0
    depth = 0

    for idx, raw in enumerate(text.splitlines(), start=1):
        code = strip_comment(raw).strip()

        if not code:
            # A blank/comment-only physical line: skip it. If we are mid-statement, the
            # join simply continues across it.
            continue

        if not pending:
            start_line = idx

        pending.append(code)
        depth += _paren_delta(code)

        if depth <= 0:
            depth = 0
            joined = " ".join(pending)
            result.append(CodeLine(block=block_name, line_no=start_line, raw=joined, code=joined))
            pending = []

    # Flush any dangling (unbalanced) statement at end of block, so nothing is dropped.
    if pending:
        joined = " ".join(pending)
        result.append(CodeLine(block=block_name, line_no=start_line, raw=joined, code=joined))

    return result


def code_lines(process: TIProcess) -> list[CodeLine]:
    """Return the *logical* code lines of ``process`` (multi-line statements joined).

    This is what the reference, const-propagation, and assignment passes consume, so a
    statement split across physical lines is analysed as one unit. Blank and comment-only
    lines are excluded.
    """
    result: list[CodeLine] = []
    for block_name, text in process.iter_blocks():
        result.extend(_logical_lines_for_block(block_name, text))
    return result
