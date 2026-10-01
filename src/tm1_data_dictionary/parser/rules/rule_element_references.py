"""Extract literal element references from parsed rule text (Phase 2c).

A rule names elements in several places. Each one is a place where renaming or deleting
that element would silently break the rule:

- **Area** - the left-hand side of a rule, e.g. ``['Local', 'Salaries', {'Actual','Budget'}]``.
- **RuleReference** - a same-cube ``[...]`` reference inside a rule expression,
  e.g. ``['Salaries']`` or ``['Amount', 'Corporate', 'Total Europe']``.
- **FeederSource** - the left-hand side of a feeder.
- **FeederTarget** - a ``[...]`` feeder target, or a literal argument of a ``DB()`` that
  *is* a feeder target (the cell being fed in the other cube).
- **DBArgument** - a literal argument of any other ``DB()``, mapped to a dimension of the
  referenced cube by its position.
- **Comparison** - ``!Dim @= 'Element'`` / ``!Dim @<> 'Element'``. Not a cell reference,
  but renaming the element still changes the rule's behaviour, so it is captured too.

Extraction (:func:`extract_element_references`) is pure text work. Resolution
(:func:`rollup_element_references`) decides *which dimension* each element belongs to:

- ``DB()`` arguments: by position, using the referenced cube's dimension order.
- ``'Dim':'Element'`` area items and ``!Dim`` comparisons: the named dimension.
- Plain area / ``[...]`` items: looked up in every dimension of the cube. Exactly one
  match gives the dimension; more than one is flagged **ambiguous** rather than guessed;
  none means the element does not exist.

Only plain string literals are recorded. Arguments that are expressions (``!Version``,
``ATTRS(...)``, nested ``DB(...)``) are skipped - the same "never guess" principle as
unresolved TI references and dynamic ``DB()`` cube names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from tm1_data_dictionary.parser.references import (
    _extract_arg_string,
    _split_top_level_args,
)
from tm1_data_dictionary.parser.rules.rule_dependencies import _string_mask, literal_value
from tm1_data_dictionary.parser.rules.rule_text import (
    ParsedFeeder,
    ParsedRuleText,
    _match_bracket,
    split_top_level,
)

# Matches the DB function name followed by "(" - not ATTRS(, not xDB(.
_DB_CALL = re.compile(r"(?<![A-Za-z0-9_.])DB\s*\(", re.IGNORECASE)

# !Dim @= 'Element'  /  !Dim @<> 'Element'  (dimension names may contain spaces).
_COMPARISON = re.compile(
    r"!\s*([A-Za-z0-9_][^!@=<>()\[\]{},;'*\\/+\-&%|^~]*?)\s*@\s*(?:=|<>)\s*'((?:[^']|'')*)'"
)

# Long statements are truncated when stored as a TM1 string measure.
_MAX_STATEMENT_LENGTH = 250

# Placeholder dimension names used when a dimension cannot be determined.
UNKNOWN_DIMENSION = "(Unknown)"
AMBIGUOUS_DIMENSION = "(Ambiguous)"

# ElementExists values.
EXISTS_YES = "Yes"
EXISTS_NO = "No"
EXISTS_UNKNOWN = "Unknown"  # the cube or dimension could not be checked


class ReferenceType(str, Enum):
    """Where in the rule text the element is named."""

    AREA = "Area"
    RULE_REFERENCE = "RuleReference"
    FEEDER_SOURCE = "FeederSource"
    FEEDER_TARGET = "FeederTarget"
    DB_ARGUMENT = "DBArgument"
    COMPARISON = "Comparison"


@dataclass(frozen=True)
class ElementReference:
    """One literal element name found in a cube's rules or feeders."""

    cube: str  # cube that owns the rule
    target_cube: str  # cube whose dimensions the element belongs to
    element: str  # as written in the rule
    reference_type: ReferenceType
    line_no: int
    statement: str
    position: int | None = None  # 0-based dimension position (DB() arguments only)
    dimension_hint: str | None = None  # explicitly named dimension, if any


# --------------------------------------------------------------------------- #
# Extraction helpers
# --------------------------------------------------------------------------- #


def _truncate(text: str) -> str:
    if len(text) <= _MAX_STATEMENT_LENGTH:
        return text
    return text[: _MAX_STATEMENT_LENGTH - 3] + "..."


def _strip_quotes(text: str) -> str:
    """Return a dimension name with surrounding quotes removed, if quoted."""
    value = literal_value(text)
    return value if value is not None else text.strip()


def parse_area_items(area: str) -> list[tuple[str | None, str]]:
    """Return (dimension hint, element) for every literal element in an area.

    Handles plain items (``'Local'``), sets (``{'Actual','Budget'}``) and
    dimension-qualified items (``'Region':'Total Europe'``; with a hierarchy,
    ``'Dim':'Hier':'Element'``, the first part is taken as the dimension).
    """
    items: list[tuple[str | None, str]] = []
    if not area.strip():
        return items
    for item in split_top_level(area, ","):
        parts = split_top_level(item, ":")
        hint: str | None = None
        element_part = parts[-1]
        if len(parts) >= 2:
            hint = _strip_quotes(parts[0]) or None
        if element_part.startswith("{") and element_part.endswith("}"):
            candidates = split_top_level(element_part[1:-1], ",")
        else:
            candidates = [element_part]
        for candidate in candidates:
            value = literal_value(candidate)
            if value:
                items.append((hint, value))
    return items


def find_bracket_areas(text: str) -> list[str]:
    """Return the inner text of every ``[...]`` outside string literals."""
    mask = _string_mask(text)
    areas: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "[" and not mask[i]:
            close = _match_bracket(text, i)
            if close < 0:
                break
            areas.append(text[i + 1 : close].strip())
            i = close + 1
            continue
        i += 1
    return areas


def _db_calls(text: str) -> list[tuple[int, list[str]]]:
    """Return (start index, arguments) for every DB(...) call, nested ones included."""
    mask = _string_mask(text)
    calls: list[tuple[int, list[str]]] = []
    for match in _DB_CALL.finditer(text):
        start = match.start()
        if mask[start]:
            continue
        inner, _ = _extract_arg_string(text, match.end() - 1)
        calls.append((start, _split_top_level_args(inner)))
    return calls


def _feeder_target_starts(feeder: ParsedFeeder) -> set[int]:
    """Return the index in the statement text where each feeder target starts."""
    text = feeder.statement.text
    arrow = text.find("=>")
    starts: set[int] = set()
    search_from = arrow + 2 if arrow >= 0 else 0
    for target in feeder.targets:
        idx = text.find(target, search_from)
        if idx >= 0:
            starts.add(idx)
            search_from = idx + len(target)
    return starts


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def _area_refs(
    cube: str,
    area: str,
    reference_type: ReferenceType,
    line_no: int,
    statement: str,
) -> list[ElementReference]:
    return [
        ElementReference(
            cube=cube,
            target_cube=cube,
            element=element,
            reference_type=reference_type,
            line_no=line_no,
            statement=statement,
            dimension_hint=hint,
        )
        for hint, element in parse_area_items(area)
    ]


def _db_refs(
    cube: str,
    text: str,
    line_no: int,
    statement: str,
    target_starts: set[int],
) -> list[ElementReference]:
    refs: list[ElementReference] = []
    for start, args in _db_calls(text):
        if not args:
            continue
        target_cube = literal_value(args[0])
        if not target_cube:
            continue  # dynamic cube name - already counted as unresolved in Phase 2b
        reference_type = (
            ReferenceType.FEEDER_TARGET if start in target_starts else ReferenceType.DB_ARGUMENT
        )
        for position, arg in enumerate(args[1:]):
            element = literal_value(arg)
            if element:
                refs.append(
                    ElementReference(
                        cube=cube,
                        target_cube=target_cube,
                        element=element,
                        reference_type=reference_type,
                        line_no=line_no,
                        statement=statement,
                        position=position,
                    )
                )
    return refs


def _comparison_refs(cube: str, text: str, line_no: int, statement: str) -> list[ElementReference]:
    mask = _string_mask(text)
    refs: list[ElementReference] = []
    for match in _COMPARISON.finditer(text):
        if mask[match.start()]:
            continue
        element = match.group(2).replace("''", "'")
        if element:
            refs.append(
                ElementReference(
                    cube=cube,
                    target_cube=cube,
                    element=element,
                    reference_type=ReferenceType.COMPARISON,
                    line_no=line_no,
                    statement=statement,
                    dimension_hint=match.group(1).strip(),
                )
            )
    return refs


def extract_element_references(cube: str, parsed: ParsedRuleText) -> list[ElementReference]:
    """Return every literal element reference in a cube's parsed rule text."""
    refs: list[ElementReference] = []

    for rule in parsed.rules:
        st = rule.statement
        statement = _truncate(st.text)
        if rule.well_formed:
            refs.extend(_area_refs(cube, rule.area, ReferenceType.AREA, st.line_no, statement))
            for area in find_bracket_areas(rule.expression):
                refs.extend(
                    _area_refs(cube, area, ReferenceType.RULE_REFERENCE, st.line_no, statement)
                )
        refs.extend(_db_refs(cube, st.text, st.line_no, statement, set()))
        refs.extend(_comparison_refs(cube, st.text, st.line_no, statement))

    for feeder in parsed.feeders:
        st = feeder.statement
        statement = _truncate(st.text)
        if feeder.well_formed:
            refs.extend(
                _area_refs(
                    cube, feeder.source_area, ReferenceType.FEEDER_SOURCE, st.line_no, statement
                )
            )
            for target in feeder.targets:
                if target.startswith("["):
                    for area in find_bracket_areas(target):
                        refs.extend(
                            _area_refs(
                                cube, area, ReferenceType.FEEDER_TARGET, st.line_no, statement
                            )
                        )
        refs.extend(_db_refs(cube, st.text, st.line_no, statement, _feeder_target_starts(feeder)))
        refs.extend(_comparison_refs(cube, st.text, st.line_no, statement))

    return refs


# --------------------------------------------------------------------------- #
# Resolution and roll-up
# --------------------------------------------------------------------------- #


class ElementLookup(Protocol):
    """What the roll-up needs to know about dimension elements."""

    def available(self, dimension: str) -> bool:
        """Return whether the dimension's elements could be read."""
        ...

    def lookup(self, dimension: str, element: str) -> str | None:
        """Return the principal element name (matching aliases too), or None."""
        ...


@dataclass
class ElementReferenceRow:
    """One aggregated (cube, dimension, element, reference type) row."""

    cube: str
    dimension: str
    element: str
    reference_type: ReferenceType
    count: int
    first_line: int
    first_statement: str
    element_exists: str  # Yes | No | Unknown
    candidates: str  # candidate dimensions when ambiguous, comma-separated
    written_as: str  # how the first reference wrote the element
    target_cubes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ElementReferenceRollup:
    """Aggregated element-reference rows."""

    rows: tuple[ElementReferenceRow, ...]

    @property
    def missing_count(self) -> int:
        return sum(1 for r in self.rows if r.element_exists == EXISTS_NO)

    @property
    def ambiguous_count(self) -> int:
        return sum(1 for r in self.rows if r.dimension == AMBIGUOUS_DIMENSION)

    @property
    def unknown_count(self) -> int:
        return sum(1 for r in self.rows if r.element_exists == EXISTS_UNKNOWN)


def _normalise(name: str) -> str:
    """TM1 object and element names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


def _check(index: ElementLookup, dimension: str, element: str) -> tuple[str, str, str, str]:
    """Resolve an element against one known dimension."""
    if not index.available(dimension):
        return dimension, element, EXISTS_UNKNOWN, ""
    principal = index.lookup(dimension, element)
    if principal is None:
        return dimension, element, EXISTS_NO, ""
    return dimension, principal, EXISTS_YES, ""


def resolve_reference(
    ref: ElementReference,
    dims: tuple[str, ...] | None,
    index: ElementLookup,
) -> tuple[str, str, str, str]:
    """Return (dimension, element, exists, candidates) for one reference.

    ``dims`` is the target cube's dimension order, or None if the cube does not exist.
    """
    if dims is None:
        return UNKNOWN_DIMENSION, ref.element, EXISTS_UNKNOWN, ""

    if ref.position is not None:
        if ref.position >= len(dims):
            return UNKNOWN_DIMENSION, ref.element, EXISTS_UNKNOWN, ""
        return _check(index, dims[ref.position], ref.element)

    if ref.dimension_hint:
        hint = _normalise(ref.dimension_hint)
        dimension = next((d for d in dims if _normalise(d) == hint), ref.dimension_hint)
        return _check(index, dimension, ref.element)

    matches: list[tuple[str, str]] = []
    for dimension in dims:
        principal = index.lookup(dimension, ref.element)
        if principal is not None:
            matches.append((dimension, principal))
    if len(matches) == 1:
        dimension, principal = matches[0]
        return dimension, principal, EXISTS_YES, ""
    if len(matches) > 1:
        candidates = ", ".join(d for d, _ in matches)
        return AMBIGUOUS_DIMENSION, ref.element, EXISTS_YES, candidates
    if any(not index.available(d) for d in dims):
        return UNKNOWN_DIMENSION, ref.element, EXISTS_UNKNOWN, ""
    return UNKNOWN_DIMENSION, ref.element, EXISTS_NO, ""


def rollup_element_references(
    refs: list[ElementReference],
    cube_dimensions: dict[str, tuple[str, ...]],
    index: ElementLookup,
) -> ElementReferenceRollup:
    """Resolve and aggregate references per (cube, dimension, element, reference type).

    ``cube_dimensions`` maps every existing cube that may be referenced to its dimension
    names in order. A target cube missing from the map is treated as non-existent, so its
    elements are recorded with dimension ``(Unknown)`` and ElementExists ``Unknown``.
    """
    dims_by_cube = {_normalise(name): dims for name, dims in cube_dimensions.items()}
    canonical_cube = {_normalise(name): name for name in cube_dimensions}
    grouped: dict[tuple[str, str, str, ReferenceType], ElementReferenceRow] = {}

    for ref in refs:
        target_key = _normalise(ref.target_cube)
        dimension, element, exists, candidates = resolve_reference(
            ref, dims_by_cube.get(target_key), index
        )
        target_cube = canonical_cube.get(target_key, ref.target_cube)
        key = (ref.cube, dimension, _normalise(element), ref.reference_type)
        row = grouped.get(key)
        if row is None:
            grouped[key] = ElementReferenceRow(
                cube=ref.cube,
                dimension=dimension,
                element=element,
                reference_type=ref.reference_type,
                count=1,
                first_line=ref.line_no,
                first_statement=ref.statement,
                element_exists=exists,
                candidates=candidates,
                written_as=ref.element,
                target_cubes=[target_cube],
            )
        else:
            row.count += 1
            if target_cube not in row.target_cubes:
                row.target_cubes.append(target_cube)

    return ElementReferenceRollup(rows=tuple(grouped.values()))
