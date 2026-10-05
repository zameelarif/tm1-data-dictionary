"""Cached, case-insensitive element lookup per dimension (Phase 2c).

The element-reference roll-up needs to answer, many times over, *"does element X exist in
dimension D, and what is its principal name?"*. Rules may name an element by its
principal name **or by any alias** (e.g. ``'Actual'`` for element ``1``), in any case and
spacing. The lookup is layered so that one failing TM1 call never turns into a false
"missing element":

1. **Principal names** - read once per dimension.
2. **Aliases** - read once per dimension. The alias list comes from
   ``get_alias_element_attributes`` (rebuilt from ``get_element_attributes`` if that
   fails). Values are read with **one REST call** for the whole dimension
   (``Elements?$select=Name,Attributes``), which does not build MDX from element names,
   so an element whose name contains quotes, brackets or backslashes cannot break it.
   If that call fails, each alias is read separately through TM1py, so one failing alias
   does not hide the others.
3. **TM1 itself (MDX fallback)** - an element still not found is resolved by asking TM1
   for ``{[Dim].[Dim].[Element]}``. TM1's MDX resolves aliases exactly as the rule engine
   does, so this catches anything layers 1-2 missed (unusual alias setups, API
   differences between TM1/PA versions). Results are cached per (dimension, element),
   and the number of queries per run is capped so a model with many genuinely missing
   elements cannot slow the run down.

Every problem is recorded rather than raised: an unreadable dimension is reported as
unavailable (references become ElementExists = Unknown), and alias read failures are
listed in the run summary. Only the default hierarchy (same name as the dimension) is read.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from tm1_data_dictionary.tm1_client import TM1Client

# Loader: dimension name -> {normalised element name or alias value: principal name}.
ElementLoader = Callable[[str], dict[str, str]]

# Resolver: (dimension, element as written) -> principal name, or None if not found.
ElementResolver = Callable[[str, str], "str | None"]

# Maximum fallback queries per run (protects large models with many missing elements).
DEFAULT_MAX_FALLBACK_QUERIES = 500

ALIAS_ATTRIBUTE_TYPES = frozenset({"alias", "aa"})


def _normalise(name: str) -> str:
    """TM1 object and element names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


def _element_from_key(key: str) -> str:
    """Return the element name from a plain or unique name (``[Dim].[Hier].[Elem]``)."""
    if key.startswith("[") and key.endswith("]"):
        return key[key.rfind("[") + 1 : -1].replace("]]", "]")
    return key


def _mdx_name(name: str) -> str:
    """Escape a name for use inside MDX square brackets."""
    return name.replace("]", "]]")


# --------------------------------------------------------------------------- #
# Layers 1-2: bulk loader
# --------------------------------------------------------------------------- #


class TM1ElementLoader:
    """Read element names and alias values for one dimension at a time via TM1py.

    Callable as an :data:`ElementLoader`. Alias read problems are collected in
    :attr:`alias_errors` rather than raised.
    """

    def __init__(self, service: Any) -> None:
        self._service = service
        self.alias_errors: dict[str, list[str]] = {}  # dimension -> ["alias: error", ...]
        self.aliases_read: dict[str, list[str]] = {}  # dimension -> alias names loaded

    def _record(self, dimension: str, message: str) -> None:
        self.alias_errors.setdefault(dimension, []).append(message)

    def _alias_names(self, dimension: str) -> list[str]:
        elements = self._service.elements
        try:
            return list(elements.get_alias_element_attributes(dimension, dimension))
        except Exception as primary:  # noqa: BLE001 - fall back to the attribute list
            try:
                attributes = elements.get_element_attributes(dimension, dimension)
                return [
                    attr.name
                    for attr in attributes
                    if str(getattr(attr, "attribute_type", "")).lower() in ALIAS_ATTRIBUTE_TYPES
                ]
            except Exception as secondary:  # noqa: BLE001
                self._record(
                    dimension,
                    f"(alias list): {type(primary).__name__}: {primary}; "
                    f"fallback {type(secondary).__name__}: {secondary}",
                )
                return []

    def _alias_values(self, dimension: str, alias: str) -> dict[str, object]:
        return dict(
            self._service.elements.get_attribute_of_elements(
                dimension,
                dimension,
                alias,
                exclude_empty_cells=True,
                element_unique_names=False,
            )
        )

    def _all_alias_values_rest(
        self, dimension: str, aliases: list[str]
    ) -> dict[str, dict[str, object]] | None:
        """Read every alias for every element in one REST call; None if unavailable.

        Returns ``{alias: {element: value}}``. Uses the REST connection TM1py already
        holds, so no MDX is built from element names.
        """
        rest = getattr(self._service.elements, "_rest", None)
        if rest is None or not hasattr(rest, "GET"):
            return None
        quoted = quote(dimension.replace("'", "''"), safe="")
        url = f"/Dimensions('{quoted}')/Hierarchies('{quoted}')/Elements" "?$select=Name,Attributes"
        try:
            payload = rest.GET(url).json()
        except Exception:  # noqa: BLE001 - fall back to per-alias reads
            return None
        wanted = {_normalise(a): a for a in aliases}
        result: dict[str, dict[str, object]] = {alias: {} for alias in aliases}
        for element in payload.get("value", []):
            name = element.get("Name")
            attributes = element.get("Attributes") or {}
            if not isinstance(name, str) or not isinstance(attributes, dict):
                continue
            for attr_name, value in attributes.items():
                alias = wanted.get(_normalise(str(attr_name)))
                if alias is not None:
                    result[alias][name] = value
        return result

    @staticmethod
    def _add_values(mapping: dict[str, str], values: dict[str, object]) -> None:
        for key, value in values.items():
            if isinstance(value, str) and value:
                # A principal name always wins over an alias of another element.
                mapping.setdefault(_normalise(value), _element_from_key(str(key)))

    def __call__(self, dimension: str) -> dict[str, str]:
        names = self._service.elements.get_element_names(dimension, dimension)
        mapping: dict[str, str] = {_normalise(name): name for name in names}
        aliases = self._alias_names(dimension)
        if not aliases:
            return mapping
        bulk = self._all_alias_values_rest(dimension, aliases)
        if bulk is not None:
            for alias in aliases:
                self.aliases_read.setdefault(dimension, []).append(alias)
                self._add_values(mapping, bulk[alias])
            return mapping
        for alias in aliases:
            try:
                values = self._alias_values(dimension, alias)
            except Exception as exc:  # noqa: BLE001 - one alias must not hide the others
                self._record(dimension, f"{alias}: {type(exc).__name__}: {exc}")
                continue
            self.aliases_read.setdefault(dimension, []).append(alias)
            self._add_values(mapping, values)
        return mapping


# --------------------------------------------------------------------------- #
# Layer 3: TM1 MDX fallback
# --------------------------------------------------------------------------- #


def _first_name(result: object) -> str | None:
    """Return the first member name from any TM1py set-MDX result shape."""
    if isinstance(result, str):
        return result or None
    if isinstance(result, dict):
        name = result.get("Name")
        return name if isinstance(name, str) and name else None
    if isinstance(result, list | tuple):
        for item in result:
            name = _first_name(item)
            if name:
                return name
    return None


class TM1MdxResolver:
    """Ask TM1 to resolve an element name or alias via a one-member MDX set.

    TM1 raises when the member does not exist, so any exception means "not found". The
    first error per dimension is kept in :attr:`errors` for diagnostics.
    """

    def __init__(self, service: Any) -> None:
        self._service = service
        self.errors: dict[str, str] = {}

    def __call__(self, dimension: str, element: str) -> str | None:
        dim = _mdx_name(dimension)
        mdx = f"{{[{dim}].[{dim}].[{_mdx_name(element)}]}}"
        elements = self._service.elements
        try:
            names_only = getattr(elements, "execute_set_mdx_element_names", None)
            if callable(names_only):
                return _first_name(list(names_only(mdx)))
            return _first_name(elements.execute_set_mdx(mdx, member_properties=["Name"]))
        except Exception as exc:  # noqa: BLE001 - not found (or not resolvable)
            self.errors.setdefault(dimension, f"{type(exc).__name__}: {exc}")
            return None


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #


class ElementIndex:
    """Read each dimension's elements once, then answer lookups from the cache."""

    def __init__(
        self,
        loader: ElementLoader,
        resolver: ElementResolver | None = None,
        *,
        max_fallback_queries: int = DEFAULT_MAX_FALLBACK_QUERIES,
    ) -> None:
        self._loader = loader
        self._resolver = resolver
        self._max_fallback = max_fallback_queries
        self._cache: dict[str, dict[str, str] | None] = {}
        self._fallback_cache: dict[tuple[str, str], str | None] = {}
        self._failed: dict[str, str] = {}  # dimension -> error text
        self.fallback_queries = 0
        self.fallback_resolved = 0
        self.fallback_limit_reached = False

    def _elements(self, dimension: str) -> dict[str, str] | None:
        key = _normalise(dimension)
        if key not in self._cache:
            try:
                self._cache[key] = self._loader(dimension)
            except Exception as exc:  # noqa: BLE001 - isolate per-dimension failures
                self._cache[key] = None
                self._failed[dimension] = f"{type(exc).__name__}: {exc}"
        return self._cache[key]

    def _fallback(self, dimension: str, element: str) -> str | None:
        key = (_normalise(dimension), _normalise(element))
        if key in self._fallback_cache:
            return self._fallback_cache[key]
        if self._resolver is None:
            return None
        if self.fallback_queries >= self._max_fallback:
            self.fallback_limit_reached = True
            return None
        self.fallback_queries += 1
        principal = self._resolver(dimension, element)
        self._fallback_cache[key] = principal
        if principal is not None:
            self.fallback_resolved += 1
            # Remember it in the bulk cache too, so later lookups are free.
            elements = self._cache.get(_normalise(dimension))
            if elements is not None:
                elements.setdefault(key[1], principal)
        return principal

    def names_and_aliases(self, dimension: str) -> dict[str, str] | None:
        """Return ``{normalised name or alias: principal name}``, or None if unreadable."""
        elements = self._elements(dimension)
        return None if elements is None else dict(elements)

    def available(self, dimension: str) -> bool:
        """Return whether the dimension's elements could be read."""
        return self._elements(dimension) is not None

    def lookup(self, dimension: str, element: str, *, fallback: bool = True) -> str | None:
        """Return the principal element name (matching aliases too), or None.

        With ``fallback=False`` only the bulk-loaded names and aliases are checked (no
        TM1 query) - used when scanning every dimension of a cube for an unqualified
        element, so a principal-name hit in one dimension does not cost a query in
        every other dimension.
        """
        elements = self._elements(dimension)
        if elements is None:
            return None
        principal = elements.get(_normalise(element))
        if principal is not None or not fallback:
            return principal
        return self._fallback(dimension, element)

    @property
    def dimensions_loaded(self) -> int:
        """How many dimensions were read successfully."""
        return sum(1 for v in self._cache.values() if v is not None)

    @property
    def failed_dimensions(self) -> dict[str, str]:
        """Dimensions that could not be read, with the error."""
        return dict(self._failed)

    @property
    def alias_errors(self) -> dict[str, list[str]]:
        """Aliases that could not be read, per dimension (empty for custom loaders)."""
        errors = getattr(self._loader, "alias_errors", {})
        return {dim: list(msgs) for dim, msgs in errors.items()}


def tm1_element_loader(client: TM1Client) -> TM1ElementLoader:
    """Return a loader that reads element names and alias values via TM1py."""
    return TM1ElementLoader(client.service)


def tm1_element_resolver(client: TM1Client) -> TM1MdxResolver:
    """Return a resolver that asks TM1 (via MDX) for an element's principal name."""
    return TM1MdxResolver(client.service)
