"""A rule element that lives only in an alternate hierarchy is not reported as missing."""

from __future__ import annotations

from tm1_data_dictionary.element_index import ElementIndex
from tm1_data_dictionary.parser.rules.rule_element_references import (
    AMBIGUOUS_DIMENSION,
    EXISTS_ALTERNATE,
    EXISTS_NO,
    EXISTS_YES,
    ElementReference,
    ReferenceType,
    rollup_element_references,
)

DIMS: dict[str, tuple[str, ...]] = {"Cost": ("Version", "Account", "Measure")}
ELEMENTS = {
    "Version": {"actual": "Actual"},
    "Account": {"a100": "A100"},
    "Measure": {"amount": "Amount"},
}
ALTERNATES = {"Account": {"BI Rollup": {"totalbi": "Total BI", "bi1": "BI1"}}}


def _index(alternates: dict | None = ALTERNATES) -> ElementIndex:
    return ElementIndex(
        lambda d: dict(ELEMENTS[d]),
        hierarchy_loader=lambda d: {h: dict(n) for h, n in (alternates or {}).get(d, {}).items()},
    )


def _ref(element: str, **kwargs: object) -> ElementReference:
    return ElementReference(
        cube="Cost",
        target_cube="Cost",
        element=element,
        reference_type=kwargs.pop("reference_type", ReferenceType.RULE_REFERENCE),  # type: ignore[arg-type]
        line_no=3,
        statement="['x'] = ['" + element + "'];",
        **kwargs,  # type: ignore[arg-type]
    )


def _row(refs: list[ElementReference], index: ElementIndex):  # noqa: ANN202
    rollup = rollup_element_references(refs, DIMS, index)
    return rollup, rollup.rows[0]


def test_unqualified_element_only_in_an_alternate_hierarchy() -> None:
    rollup, row = _row([_ref("total bi")], _index())
    assert (row.dimension, row.element, row.element_exists) == (
        "Account",
        "Total BI",
        EXISTS_ALTERNATE,
    )
    assert row.candidates == "found only in hierarchy 'BI Rollup'"
    assert row.written_as == "total bi"
    assert (rollup.alternate_count, rollup.missing_count) == (1, 0)


def test_a_really_missing_element_is_still_missing() -> None:
    rollup, row = _row([_ref("Gone")], _index())
    assert row.element_exists == EXISTS_NO
    assert (rollup.alternate_count, rollup.missing_count) == (0, 1)


def test_default_hierarchy_elements_are_unchanged() -> None:
    _, row = _row([_ref("A100")], _index())
    assert (row.dimension, row.element_exists, row.candidates) == ("Account", EXISTS_YES, "")


def test_db_argument_by_position() -> None:
    ref = _ref("BI1", reference_type=ReferenceType.DB_ARGUMENT, position=1)
    _, row = _row([ref], _index())
    assert (row.dimension, row.element_exists) == ("Account", EXISTS_ALTERNATE)


def test_dimension_qualified_item() -> None:
    _, row = _row([_ref("Total BI", dimension_hint="Account")], _index())
    assert row.element_exists == EXISTS_ALTERNATE


def test_found_in_alternates_of_two_dimensions_is_ambiguous() -> None:
    alternates = {"Account": {"H": {"shared": "Shared"}}, "Measure": {"H": {"shared": "Shared"}}}
    _, row = _row([_ref("Shared")], _index(alternates))
    assert (row.dimension, row.element_exists) == (AMBIGUOUS_DIMENSION, EXISTS_ALTERNATE)
    assert row.candidates == "Account:H, Measure:H"


def test_an_index_that_cannot_read_alternates_behaves_as_before() -> None:
    class _Plain:
        def available(self, dimension: str) -> bool:
            return True

        def lookup(self, dimension: str, element: str, *, fallback: bool = True) -> str | None:
            return None

    rollup = rollup_element_references([_ref("Total BI")], DIMS, _Plain())
    assert rollup.rows[0].element_exists == EXISTS_NO


def test_alternate_found_even_when_another_dimension_is_unreadable() -> None:
    def loader(dimension: str) -> dict[str, str]:
        if dimension == "Measure":
            raise RuntimeError("no access")
        return dict(ELEMENTS[dimension])

    index = ElementIndex(
        loader, hierarchy_loader=lambda d: {"H": {"totalbi": "Total BI"}} if d == "Account" else {}
    )
    _, row = _row([_ref("Total BI")], index)
    assert row.element_exists == EXISTS_ALTERNATE
