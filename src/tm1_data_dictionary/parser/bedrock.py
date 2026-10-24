"""Read the filter string passed to Bedrock cube processes (element lineage).

Many TI processes do not build their zero-out view by hand. They call a Bedrock process
and describe the slice as one string::

    ExecuteProcess('Bedrock.Cube.View.Create', 'pCube', 'Sales', 'pView', cView,
                   'pFilter', 'Version' | Delim | 'Actual+Budget' ...);

so the cleared elements never appear in the process as separate calls, and the element pass
used to report every dimension of the view as ``(All)``. This module reads the filter so the
real elements can be recorded.

**Filter format** (Bedrock's documented defaults)::

    Dim1¦Elem1+Elem2&Dim2¦Elem3        dimensions joined by '&',
                                        elements by '+', dimension from elements by '¦'

``Dim:Hier¦Elem`` names a hierarchy. The three delimiters can be changed with the
``pDimDelim``, ``pEleStartDelim`` and ``pEleDelim`` parameters, which are honoured when a
process passes them as literals.

The defaults are an assumption about the installed Bedrock version. The scanner checks every
parsed dimension against the cube's real dimensions, and a filter that does not fit is
reported as unresolved (``Runtime``) rather than guessed.

Pure text work - no TM1, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from tm1_data_dictionary.parser.var_trace import unquote

# Process names, normalised the way TM1 compares them (lower case, no spaces).
BEDROCK_VIEW_CREATE = frozenset({"bedrock.cube.view.create", "}bedrock.cube.view.create"})
BEDROCK_DATA_CLEAR = frozenset({"bedrock.cube.data.clear", "}bedrock.cube.data.clear"})

FUNCTION_VIEW_CREATE = "Bedrock.Cube.View.Create"
FUNCTION_DATA_CLEAR = "Bedrock.Cube.Data.Clear"

# Bedrock's default delimiters. The element-start delimiter is U+00A6 (broken bar).
DEFAULT_DIM_DELIM = "&"
DEFAULT_ELE_START_DELIM = "\u00a6"
DEFAULT_ELE_DELIM = "+"

# Marker characters that make an element token an MDX expression, not an element name.
_MDX_CHARS = "{}[]"


@dataclass(frozen=True)
class FilterPart:
    """One dimension's part of a Bedrock filter."""

    dimension: str  # as written; may be ``Dim:Hier``
    elements: tuple[str, ...]  # element tokens, in order
    raw: str  # the part as written, for the Expression measure


def process_arguments(args: list[str]) -> dict[str, str]:
    """Return ``{lower-case parameter name: value expression}`` from ExecuteProcess arguments.

    ``args`` are the arguments *after* the process name: ``'pCube', expr, 'pView', expr ...``.
    A name that is not a plain string literal is skipped.
    """
    out: dict[str, str] = {}
    for position in range(0, len(args) - 1, 2):
        name = unquote(args[position])
        if name:
            out[name.strip().lower()] = args[position + 1].strip()
    return out


def parse_filter(
    text: str,
    dim_delim: str = DEFAULT_DIM_DELIM,
    ele_start_delim: str = DEFAULT_ELE_START_DELIM,
    ele_delim: str = DEFAULT_ELE_DELIM,
) -> list[FilterPart] | None:
    """Split a Bedrock filter into dimension parts.

    Returns ``[]`` for an empty filter (the whole cube) and ``None`` when the text does not
    follow the format: a part without the element-start delimiter, an empty dimension, or an
    empty delimiter.
    """
    if not dim_delim or not ele_start_delim or not ele_delim:
        return None
    if not text.strip():
        return []
    parts: list[FilterPart] = []
    for chunk in text.split(dim_delim):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ele_start_delim not in chunk:
            return None
        dimension, _, rest = chunk.partition(ele_start_delim)
        dimension = dimension.strip()
        if not dimension:
            return None
        elements = tuple(e.strip() for e in rest.split(ele_delim) if e.strip())
        parts.append(FilterPart(dimension=dimension, elements=elements, raw=chunk))
    return parts


def looks_like_mdx(token: str) -> bool:
    """Return whether an element token is an MDX expression rather than an element name."""
    return any(ch in token for ch in _MDX_CHARS) or token.upper().startswith("TM1")
