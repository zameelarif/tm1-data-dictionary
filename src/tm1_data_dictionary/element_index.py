"""Cached, case-insensitive element lookup per dimension (Phase 2c).

The element-reference roll-up needs to answer two questions many times over:
*"does element X exist in dimension D?"* and *"what is its principal name?"*. Dimensions
are shared across cubes (Version, Year, Period ...), so each one is read **once** per run
and cached.

Lookups match the way TM1 does: case- and space-insensitive, and against aliases as well
as principal names, so a rule that writes an alias is not reported as a missing element.
Only the default hierarchy (same name as the dimension) is read.

A dimension that cannot be read (deleted, no access) is cached as unavailable rather than
raising, so one bad dimension never aborts the rule extraction - its references are
reported with ElementExists = Unknown instead.
"""

from __future__ import annotations

from collections.abc import Callable

from tm1_data_dictionary.tm1_client import TM1Client

# Loader: dimension name -> {normalised element name or alias: principal name}.
ElementLoader = Callable[[str], dict[str, str]]


def _normalise(name: str) -> str:
    """TM1 object and element names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


class ElementIndex:
    """Read each dimension's elements once, then answer lookups from the cache."""

    def __init__(self, loader: ElementLoader) -> None:
        self._loader = loader
        self._cache: dict[str, dict[str, str] | None] = {}
        self._failed: dict[str, str] = {}  # dimension -> error text

    def _elements(self, dimension: str) -> dict[str, str] | None:
        key = _normalise(dimension)
        if key not in self._cache:
            try:
                self._cache[key] = self._loader(dimension)
            except Exception as exc:  # noqa: BLE001 - isolate per-dimension failures
                self._cache[key] = None
                self._failed[dimension] = f"{type(exc).__name__}: {exc}"
        return self._cache[key]

    def available(self, dimension: str) -> bool:
        """Return whether the dimension's elements could be read."""
        return self._elements(dimension) is not None

    def lookup(self, dimension: str, element: str) -> str | None:
        """Return the principal element name (matching aliases too), or None."""
        elements = self._elements(dimension)
        if elements is None:
            return None
        return elements.get(_normalise(element))

    @property
    def dimensions_loaded(self) -> int:
        """How many dimensions were read successfully."""
        return sum(1 for v in self._cache.values() if v is not None)

    @property
    def failed_dimensions(self) -> dict[str, str]:
        """Dimensions that could not be read, with the error."""
        return dict(self._failed)


def tm1_element_loader(client: TM1Client) -> ElementLoader:
    """Return a loader that reads element names and alias values via TM1py."""
    service = client.service

    def load(dimension: str) -> dict[str, str]:
        names = service.elements.get_element_names(dimension, dimension)
        mapping: dict[str, str] = {_normalise(name): name for name in names}
        # Aliases are best-effort: a failure here still leaves principal names usable.
        try:
            aliases = service.elements.get_alias_element_attributes(dimension, dimension)
            for alias in aliases:
                values = service.elements.get_attribute_of_elements(
                    dimension,
                    dimension,
                    alias,
                    exclude_empty_cells=True,
                    element_unique_names=False,
                )
                for element, value in values.items():
                    if isinstance(value, str) and value:
                        mapping.setdefault(_normalise(value), element)
        except Exception:  # noqa: BLE001, S110 - aliases are optional
            pass
        return mapping

    return load
