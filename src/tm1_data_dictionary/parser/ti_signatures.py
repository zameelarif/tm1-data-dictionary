"""Which TI function arguments hold cubes, dimensions and elements (element lineage).

The element pass needs to know, for every TI function it cares about, *which* argument is
the cube, which is the dimension and which are elements. For example::

    CellPutN(value, cube, e1, e2, ...)     cube = arg 2, elements = arg 3 onwards
    AttrPutS(value, dim, element, attr)    dim = arg 2, element = arg 3
    DimensionElementComponentAdd(dim, parent, child, weight)
                                           dim = arg 1, elements = args 2 and 3

That knowledge lives here as **data**, not code. The built-in catalogue below covers the
common functions. A site can add or override entries without a new release, with an
optional ``ti_functions.txt`` beside ``config.yaml``::

    # name = Role, cube=N, elements=N+          (cell functions; N+ means "N onwards")
    # name = Role, dim=N, element=N[, element=N] (dimension/attribute functions)
    # Positions are 1-based, as in the IBM documentation.
    MyCompany.CellPut = Write, cube=2, elements=3+

Roles used: ``Write``, ``Read``, ``AttrWrite``, ``AttrRead``, ``DimMaintain`` and
``Subset``. Views and subsets used for zero-outs and source views are followed separately
by :mod:`tm1_data_dictionary.parser.element_scan`.

Pure data and parsing - no TM1, no I/O beyond reading the optional file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SIGNATURE_FILENAME = "ti_functions.txt"

ROLE_WRITE = "Write"
ROLE_READ = "Read"
ROLE_ATTR_WRITE = "AttrWrite"
ROLE_ATTR_READ = "AttrRead"
ROLE_DIM_MAINTAIN = "DimMaintain"
ROLE_SUBSET = "Subset"

KNOWN_ROLES = frozenset(
    {ROLE_WRITE, ROLE_READ, ROLE_ATTR_WRITE, ROLE_ATTR_READ, ROLE_DIM_MAINTAIN, ROLE_SUBSET}
)


@dataclass(frozen=True)
class Signature:
    """Argument layout of one TI function. All positions are 0-based."""

    name: str
    role: str
    cube: int | None = None  # cell functions: which argument is the cube
    elements_from: int | None = None  # cell functions: elements start here, one per dim
    dim: int | None = None  # dimension functions: which argument is the dimension
    elements: tuple[int, ...] = ()  # dimension functions: which arguments are elements
    subset: int | None = None  # SubsetElementInsert-style: which argument is the subset

    @property
    def is_cell_function(self) -> bool:
        return self.cube is not None and self.elements_from is not None


def _cell(name: str, role: str, cube: int, first: int) -> Signature:
    return Signature(name=name, role=role, cube=cube - 1, elements_from=first - 1)


def _dim(name: str, role: str, dim: int, *elements: int, subset: int | None = None) -> Signature:
    return Signature(
        name=name,
        role=role,
        dim=dim - 1,
        elements=tuple(e - 1 for e in elements),
        subset=None if subset is None else subset - 1,
    )


# Built-in catalogue (1-based positions in the helper calls, as in the IBM docs).
BUILTIN_SIGNATURES: tuple[Signature, ...] = (
    # Cube cells
    _cell("CellPutN", ROLE_WRITE, 2, 3),
    _cell("CellPutS", ROLE_WRITE, 2, 3),
    _cell("CellIncrementN", ROLE_WRITE, 2, 3),
    _cell("CellPutProportionalSpread", ROLE_WRITE, 2, 3),
    _cell("CellGetN", ROLE_READ, 1, 2),
    _cell("CellGetS", ROLE_READ, 1, 2),
    # Attributes
    _dim("AttrPutS", ROLE_ATTR_WRITE, 2, 3),
    _dim("AttrPutN", ROLE_ATTR_WRITE, 2, 3),
    _dim("ElementAttrPutS", ROLE_ATTR_WRITE, 2, 4),
    _dim("ElementAttrPutN", ROLE_ATTR_WRITE, 2, 4),
    _dim("AttrS", ROLE_ATTR_READ, 1, 2),
    _dim("AttrN", ROLE_ATTR_READ, 1, 2),
    _dim("ElementAttrS", ROLE_ATTR_READ, 1, 3),
    _dim("ElementAttrN", ROLE_ATTR_READ, 1, 3),
    # Dimension maintenance
    _dim("DimensionElementInsert", ROLE_DIM_MAINTAIN, 1, 3),
    _dim("DimensionElementInsertDirect", ROLE_DIM_MAINTAIN, 1, 3),
    _dim("DimensionElementDelete", ROLE_DIM_MAINTAIN, 1, 2),
    _dim("DimensionElementComponentAdd", ROLE_DIM_MAINTAIN, 1, 2, 3),
    _dim("DimensionElementComponentAddDirect", ROLE_DIM_MAINTAIN, 1, 2, 3),
    _dim("DimensionElementComponentDelete", ROLE_DIM_MAINTAIN, 1, 2, 3),
    _dim("HierarchyElementInsert", ROLE_DIM_MAINTAIN, 1, 4),
    _dim("HierarchyElementDelete", ROLE_DIM_MAINTAIN, 1, 3),
    _dim("HierarchyElementComponentAdd", ROLE_DIM_MAINTAIN, 1, 3, 4),
    _dim("HierarchyElementComponentDelete", ROLE_DIM_MAINTAIN, 1, 3, 4),
    # Subsets (followed into zero-out and source views by element_scan)
    _dim("SubsetElementInsert", ROLE_SUBSET, 1, 3, subset=2),
    _dim("HierarchySubsetElementInsert", ROLE_SUBSET, 1, 4, subset=3),
)


class SignatureError(ValueError):
    """A line in ti_functions.txt could not be understood."""


_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.+?)\s*$")
_FIELD = re.compile(r"^\s*([A-Za-z]+)\s*=\s*(\d+)(\+?)\s*$")


def parse_signature_line(line: str) -> Signature:
    """Parse one ``name = Role, key=N, ...`` line (1-based positions)."""
    match = _LINE.match(line)
    if not match:
        raise SignatureError(f"expected 'Name = Role, ...': {line.strip()}")
    name, rest = match.group(1), match.group(2)
    parts = [p.strip() for p in rest.split(",") if p.strip()]
    role = parts[0]
    if role not in KNOWN_ROLES:
        raise SignatureError(f"unknown role '{role}' in: {line.strip()}")
    cube = elements_from = dim = subset = None
    elements: list[int] = []
    for part in parts[1:]:
        field_match = _FIELD.match(part)
        if not field_match:
            raise SignatureError(f"expected key=N in '{part}': {line.strip()}")
        key, value, plus = (
            field_match.group(1).lower(),
            int(field_match.group(2)),
            field_match.group(3),
        )
        if value < 1:
            raise SignatureError(f"positions start at 1: {line.strip()}")
        if key == "cube":
            cube = value - 1
        elif key == "elements" and plus:
            elements_from = value - 1
        elif key in ("element", "elements"):
            elements.append(value - 1)
        elif key == "dim":
            dim = value - 1
        elif key == "subset":
            subset = value - 1
        else:
            raise SignatureError(f"unknown key '{key}': {line.strip()}")
    sig = Signature(
        name=name,
        role=role,
        cube=cube,
        elements_from=elements_from,
        dim=dim,
        elements=tuple(elements),
        subset=subset,
    )
    if not sig.is_cell_function and sig.dim is None:
        raise SignatureError(f"needs cube=N and elements=N+, or dim=N: {line.strip()}")
    return sig


def load_signatures(path: str | Path | None = None) -> dict[str, Signature]:
    """Return ``{lower-case name: Signature}``: built-ins, then the optional file on top.

    A missing file is not an error. Blank lines and ``#`` comments are ignored.

    Raises:
        SignatureError: if a line in the file cannot be parsed (with its line number).
    """
    catalogue = {sig.name.lower(): sig for sig in BUILTIN_SIGNATURES}
    if path is None:
        return catalogue
    file_path = Path(path)
    if not file_path.exists():
        return catalogue
    for number, raw in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        try:
            sig = parse_signature_line(text)
        except SignatureError as exc:
            raise SignatureError(f"{file_path.name} line {number}: {exc}") from exc
        catalogue[sig.name.lower()] = sig
    return catalogue
