"""Answer "where is this element used?" from the stored cubes (``tm1dd where``).

Reads ``}Meta_Process_Element`` (TI) and ``}Meta_Rule_Element_Reference`` (rules) for one
dimension and element, and returns one row per process or cube and role. Nothing is
re-parsed, so the answer is instant - it reflects the last ``extract-elements`` and
``extract-rules`` runs.

The element may be given by its principal name or any alias; the caller resolves it
(see :func:`resolve_element`) so both spellings are looked up.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tm1_data_dictionary import schema as s
from tm1_data_dictionary.element_index import ElementIndex

TI = "TI"
RULE = "Rule"


@dataclass(frozen=True)
class WhereRow:
    """One place an element is used."""

    source: str  # TI | Rule
    name: str  # process (TI) or cube owning the rule (Rule)
    cube: str
    role: str
    count: int
    block: str
    line: int
    function: str
    kind: str
    confidence: str
    exists: str
    statement: str


CSV_HEADER = (
    "Source",
    "Process or rule cube",
    "Cube",
    "Role",
    "Count",
    "Block",
    "Line",
    "Function",
    "Kind",
    "Confidence",
    "ElementExists",
    "Statement",
)


def _b(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _member(dimension: str, element: str) -> str:
    return f"{_b(dimension)}.{_b(dimension)}.{_b(element)}"


def _all(dimension: str) -> str:
    return f"{{TM1SUBSETALL({_b(dimension)}.{_b(dimension)})}}"


def _rows_mdx(cube: str, dims: tuple[str, ...], fixed: dict[str, str], anchor: str) -> str:
    """MDX with every name dimension on rows (fixed ones pinned) and all measures on columns."""
    *name_dims, measure_dim = dims
    sets = [f"{{{_member(d, fixed[d])}}}" if d in fixed else _all(d) for d in name_dims]
    rows = sets[0]
    for nxt in sets[1:]:
        rows = f"CROSSJOIN({rows}, {nxt})"
    rows = f"NONEMPTY({rows}, {{{_member(measure_dim, anchor)}}})"
    return f"SELECT {_all(measure_dim)} ON COLUMNS, NON EMPTY {{{rows}}} ON ROWS FROM {_b(cube)}"


def ti_mdx(dimension: str, element: str) -> str:
    """MDX for every TI row of one element."""
    dims = (
        s.DIM_PROCESS,
        s.DIM_CUBE,
        s.DIM_DIMENSION,
        s.DIM_ELEMENT,
        s.DIM_PROCESS_ELEMENT_ROLE,
        s.DIM_PROCESS_ELEMENT_MEASURE,
    )
    fixed = {s.DIM_DIMENSION: dimension, s.DIM_ELEMENT: element}
    return _rows_mdx(s.CUBE_PROCESS_ELEMENT, dims, fixed, "Count")


def rule_mdx(dimension: str, element: str) -> str:
    """MDX for every rule row of one element."""
    dims = (
        s.DIM_CUBE,
        s.DIM_DIMENSION,
        s.DIM_ELEMENT,
        s.DIM_RULE_ELEMENT_REF_TYPE,
        s.DIM_RULE_ELEMENT_REF_MEASURE,
    )
    fixed = {s.DIM_DIMENSION: dimension, s.DIM_ELEMENT: element}
    return _rows_mdx(s.CUBE_RULE_ELEMENT_REFERENCE, dims, fixed, "Count")


def _parse_unique(name: str) -> tuple[str, str]:
    """``[Dim].[Hier].[Elem]`` -> (Dim, Elem). Brackets inside names are doubled."""
    parts: list[str] = []
    current: list[str] = []
    i = 0
    inside = False
    while i < len(name):
        ch = name[i]
        if not inside:
            if ch == "[":
                inside = True
                current = []
        elif ch == "]":
            if i + 1 < len(name) and name[i + 1] == "]":
                current.append("]")
                i += 1
            else:
                inside = False
                parts.append("".join(current))
        else:
            current.append(ch)
        i += 1
    if not parts:
        return "", name
    return parts[0], parts[-1]


def cells_by_row(raw: dict[Any, Any], measure_dim: str) -> dict[tuple[tuple[str, str], ...], dict]:
    """Pivot TM1py ``execute_mdx`` output to ``{row coordinates: {measure: value}}``."""
    measure_key = measure_dim.lower()
    rows: dict[tuple[tuple[str, str], ...], dict] = {}
    for coordinates, cell in raw.items():
        parsed = [_parse_unique(str(c)) for c in coordinates]
        measure = next((e for d, e in parsed if d.lower() == measure_key), None)
        if measure is None:
            continue
        row_key = tuple(sorted((d.lower(), e) for d, e in parsed if d.lower() != measure_key))
        value = cell.get("Value") if isinstance(cell, dict) else cell
        rows.setdefault(row_key, {})[measure] = value
    return rows


def _int(value: object) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _text(value: object) -> str:
    return "" if value is None else str(value)


def ti_rows(raw: dict[Any, Any]) -> list[WhereRow]:
    """Turn the TI cellset into rows."""
    out: list[WhereRow] = []
    for key, m in cells_by_row(raw, s.DIM_PROCESS_ELEMENT_MEASURE).items():
        coords = dict(key)
        out.append(
            WhereRow(
                source=TI,
                name=coords.get(s.DIM_PROCESS.lower(), ""),
                cube=coords.get(s.DIM_CUBE.lower(), ""),
                role=coords.get(s.DIM_PROCESS_ELEMENT_ROLE.lower(), ""),
                count=_int(m.get("Count")),
                block=_text(m.get("FirstBlock")),
                line=_int(m.get("FirstLine")),
                function=_text(m.get("Function")),
                kind=_text(m.get("Kind")),
                confidence=_text(m.get("Confidence")),
                exists=_text(m.get("ElementExists")),
                statement=_text(m.get("Statement")),
            )
        )
    return out


def rule_rows(raw: dict[Any, Any]) -> list[WhereRow]:
    """Turn the rules cellset into rows."""
    out: list[WhereRow] = []
    for key, m in cells_by_row(raw, s.DIM_RULE_ELEMENT_REF_MEASURE).items():
        coords = dict(key)
        cube = coords.get(s.DIM_CUBE.lower(), "")
        out.append(
            WhereRow(
                source=RULE,
                name=cube,
                cube=cube,
                role=coords.get(s.DIM_RULE_ELEMENT_REF_TYPE.lower(), ""),
                count=_int(m.get("Count")),
                block="Rules",
                line=_int(m.get("FirstLine")),
                function="",
                kind="",
                confidence="Literal",
                exists=_text(m.get("ElementExists")),
                statement=_text(m.get("FirstStatement")),
            )
        )
    return out


def resolve_element(index: ElementIndex, dimension: str, element: str) -> list[str]:
    """Return the names to look up: the principal name (if found) and the name as given."""
    names = [element]
    principal = index.lookup(dimension, element)
    if (
        principal is not None
        and principal.replace(" ", "").lower() != element.replace(" ", "").lower()
    ):
        names.insert(0, principal)
    return names


def find(service: Any, dimension: str, names: list[str]) -> list[WhereRow]:
    """Read both cubes for each name and return all rows (TI first, then rules)."""
    rows: list[WhereRow] = []
    if not service.elements.exists(s.DIM_DIMENSION, s.DIM_DIMENSION, dimension):
        return rows
    for name in names:
        if not service.elements.exists(s.DIM_ELEMENT, s.DIM_ELEMENT, name):
            continue
        if service.cubes.exists(s.CUBE_PROCESS_ELEMENT):
            raw = service.cells.execute_mdx(ti_mdx(dimension, name), cell_properties=["Value"])
            rows.extend(ti_rows(raw))
        if service.cubes.exists(s.CUBE_RULE_ELEMENT_REFERENCE):
            raw = service.cells.execute_mdx(rule_mdx(dimension, name), cell_properties=["Value"])
            rows.extend(rule_rows(raw))
    return sorted(rows, key=lambda r: (r.source != TI, r.name.lower(), r.role, r.line))


def write_csv(path: str | Path, rows: list[WhereRow]) -> None:
    """Write the rows to a CSV file (UTF-8 with a header, opens cleanly in Excel)."""
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for r in rows:
            writer.writerow(
                (
                    r.source,
                    r.name,
                    r.cube,
                    r.role,
                    r.count,
                    r.block,
                    r.line,
                    r.function,
                    r.kind,
                    r.confidence,
                    r.exists,
                    r.statement,
                )
            )
