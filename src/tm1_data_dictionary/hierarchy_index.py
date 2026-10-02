"""Cached parent/child lookup per dimension (Phase 2e).

Feeder-gap detection needs two hierarchy facts:

- *"Is this element a consolidation?"* - a rule on consolidated cells only does not need
  feeders.
- *"Is element A an ancestor of element B?"* - feeding a consolidation feeds every leaf
  beneath it, so a feeder targeting ``'Total Expenses'`` does feed a rule on ``'Salaries'``.

Each dimension's edges (parent -> child) are read **once** per run, from the default
hierarchy, with one REST call (``Edges?$select=ParentName,ComponentName``). If that fails,
TM1py's ``hierarchies.get`` is used instead. A dimension whose edges cannot be read is
recorded in :attr:`HierarchyIndex.failed_dimensions`; lookups on it fall back to "same
element only", which can only make the analysis *more* cautious (more possible gaps
reported as possible, never a real gap hidden).

Names are compared ignoring case and spaces, as TM1 does.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from tm1_data_dictionary.tm1_client import TM1Client

# Loader: dimension name -> list of (parent, child) edges.
EdgeLoader = Callable[[str], list[tuple[str, str]]]


def _normalise(name: str) -> str:
    """TM1 object and element names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


class HierarchyIndex:
    """Read each dimension's edges once, then answer ancestry questions from the cache."""

    def __init__(self, loader: EdgeLoader) -> None:
        self._loader = loader
        # normalised dimension -> {normalised child: {normalised parents}} (None = unreadable)
        self._parents: dict[str, dict[str, set[str]] | None] = {}
        self._consolidations: dict[str, set[str]] = {}
        self._ancestors: dict[tuple[str, str], frozenset[str]] = {}
        self._failed: dict[str, str] = {}

    def _load(self, dimension: str) -> dict[str, set[str]] | None:
        key = _normalise(dimension)
        if key not in self._parents:
            try:
                edges = self._loader(dimension)
            except Exception as exc:  # noqa: BLE001 - isolate per-dimension failures
                self._parents[key] = None
                self._consolidations[key] = set()
                self._failed[dimension] = f"{type(exc).__name__}: {exc}"
                return None
            parents: dict[str, set[str]] = {}
            consolidations: set[str] = set()
            for parent, child in edges:
                p, c = _normalise(parent), _normalise(child)
                parents.setdefault(c, set()).add(p)
                consolidations.add(p)
            self._parents[key] = parents
            self._consolidations[key] = consolidations
        return self._parents[key]

    def available(self, dimension: str) -> bool:
        """Return whether the dimension's edges could be read."""
        return self._load(dimension) is not None

    def is_consolidated(self, dimension: str, element: str) -> bool:
        """Return whether the element has children (False if the dimension is unreadable)."""
        self._load(dimension)
        return _normalise(element) in self._consolidations.get(_normalise(dimension), set())

    def ancestors(self, dimension: str, element: str) -> frozenset[str]:
        """Return every ancestor of an element (normalised names), across all parents."""
        dim_key, el_key = _normalise(dimension), _normalise(element)
        cached = self._ancestors.get((dim_key, el_key))
        if cached is not None:
            return cached
        parents = self._load(dimension) or {}
        seen: set[str] = set()
        stack = list(parents.get(el_key, ()))
        while stack:
            node = stack.pop()
            if node in seen:
                continue  # guards against cycles in malformed hierarchies
            seen.add(node)
            stack.extend(parents.get(node, ()))
        result = frozenset(seen)
        self._ancestors[(dim_key, el_key)] = result
        return result

    def related(self, dimension: str, a: str, b: str) -> bool:
        """Return whether a and b are the same element, or one is an ancestor of the other."""
        na, nb = _normalise(a), _normalise(b)
        if na == nb:
            return True
        return na in self.ancestors(dimension, b) or nb in self.ancestors(dimension, a)

    @property
    def dimensions_loaded(self) -> int:
        """How many dimensions' edges were read successfully."""
        return sum(1 for v in self._parents.values() if v is not None)

    @property
    def failed_dimensions(self) -> dict[str, str]:
        """Dimensions whose edges could not be read, with the error."""
        return dict(self._failed)


class TM1EdgeLoader:
    """Read a dimension's default-hierarchy edges via TM1's REST API (TM1py fallback)."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def _rest_edges(self, dimension: str) -> list[tuple[str, str]] | None:
        rest = getattr(self._service.elements, "_rest", None)
        if rest is None or not hasattr(rest, "GET"):
            return None
        quoted = quote(dimension.replace("'", "''"), safe="")
        url = (
            f"/Dimensions('{quoted}')/Hierarchies('{quoted}')/Edges"
            "?$select=ParentName,ComponentName"
        )
        try:
            payload = rest.GET(url).json()
        except Exception:  # noqa: BLE001 - fall back to TM1py
            return None
        return [
            (str(edge["ParentName"]), str(edge["ComponentName"]))
            for edge in payload.get("value", [])
            if edge.get("ParentName") and edge.get("ComponentName")
        ]

    def __call__(self, dimension: str) -> list[tuple[str, str]]:
        edges = self._rest_edges(dimension)
        if edges is not None:
            return edges
        hierarchy = self._service.hierarchies.get(dimension, dimension)
        return [(str(parent), str(child)) for parent, child in hierarchy.edges]


def tm1_edge_loader(client: TM1Client) -> TM1EdgeLoader:
    """Return a loader that reads hierarchy edges via TM1."""
    return TM1EdgeLoader(client.service)
