"""Orchestrate element-level lineage across every process (``tm1dd extract-elements``).

For every included process:

1. read it once and split it into logical lines;
2. run the static element pass (:mod:`element_scan`): literal and variable element
   arguments, plus the views and subsets the process builds for zero-outs and source views;
3. search it for the watch-listed elements (:mod:`element_watchlist`), adding only the
   hits the static pass did not already explain.

Then check every recorded element against its dimension (aliases included, using the
same lookup as the rules pass), clear ``}Meta_Process_Element`` and write the rows.

Deliberately a separate command from ``tm1dd extract``, in the same way rules are, so
element lineage can fail or be skipped without affecting the proven TI lineage cubes.

Design principles (same as ``extract``): exclusions apply first; one malformed process
never aborts the run; each cube's dimension list is read once; dry-run parses and reports
without clearing or writing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tm1_data_dictionary.element_index import (
    ElementIndex,
    tm1_element_loader,
    tm1_element_resolver,
)
from tm1_data_dictionary.exclusions import ExclusionRules, partition
from tm1_data_dictionary.parser.blocks import code_lines
from tm1_data_dictionary.parser.element_scan import (
    CONF_LITERAL,
    CONF_RUNTIME,
    ELEMENT_ALL,
    ELEMENT_MDX,
    ELEMENT_RUNTIME,
    NO_CUBE,
    UNKNOWN,
    ElementScanner,
)
from tm1_data_dictionary.parser.element_watchlist import (
    ElementWatchlist,
    WatchHit,
    load_element_watchlist,
    scan_watchlist,
)
from tm1_data_dictionary.parser.ti_reader import TIReader
from tm1_data_dictionary.parser.ti_signatures import load_signatures
from tm1_data_dictionary.parser.var_trace import normalise
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.process_element_writer import (
    ElementRow,
    clear_process_element,
    write_element_lineage,
)

ProgressFn = Callable[[int, int, str, str], None]

SPECIAL_ELEMENTS = frozenset({ELEMENT_RUNTIME, ELEMENT_MDX, ELEMENT_ALL})
KIND_WATCHLIST = "Watchlist"


@dataclass
class ElementSummary:
    """Summary of one element-lineage run."""

    total_processes: int = 0
    included: int = 0
    excluded: int = 0
    parsed_ok: int = 0
    failed: int = 0
    rows_written: int = 0
    element_references: int = 0  # before aggregation
    literal: int = 0
    resolved: int = 0
    runtime: int = 0
    missing_elements: int = 0  # rows whose element does not exist
    unchecked_elements: int = 0  # rows whose dimension could not be read
    watched_elements: int = 0
    watch_hits: int = 0  # rows added by the watch list
    watch_unexplained: int = 0
    watch_not_found: list[str] = field(default_factory=list)  # "Dim / Element"
    watch_unknown: list[str] = field(default_factory=list)  # listed but not in the dimension
    failed_names: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False

    def as_lines(self) -> list[str]:
        written = " (dry-run: not written)" if self.dry_run else " written"
        lines = [
            (
                f"Processes: {self.total_processes} total, "
                f"{self.included} included, {self.excluded} excluded"
            ),
            f"Parsed OK: {self.parsed_ok}, failed: {self.failed}",
            f"Element rows: {self.rows_written}{written}",
            (
                f"Element references: {self.element_references} "
                f"({self.literal} literal, {self.resolved} resolved, {self.runtime} runtime)"
            ),
            f"Element rows - missing element: {self.missing_elements}",
            f"Element rows - not checked: {self.unchecked_elements}",
        ]
        if self.watched_elements:
            lines.append(
                f"Watch list: {self.watched_elements} element(s), {self.watch_hits} extra "
                f"row(s), {self.watch_unexplained} unexplained"
            )
            for item in self.watch_unknown:
                lines.append(f"  Not in its dimension: {item}")
            for item in self.watch_not_found:
                lines.append(f"  No TI reference found: {item}")
        else:
            lines.append("Watch list: none (elements.txt missing or empty)")
        if self.failed_names:
            lines.append("Failures:")
            lines.extend(f"  {name}: {error}" for name, error in self.failed_names)
        return lines


class CubeDimensionCache:
    """Read each cube's dimension names once (None if the cube cannot be read)."""

    def __init__(self, service: object) -> None:
        self._service = service
        self._cache: dict[str, tuple[str, ...] | None] = {}

    def __call__(self, cube: str) -> tuple[str, ...] | None:
        key = normalise(cube)
        if key not in self._cache:
            try:
                names = self._service.cubes.get_dimension_names(cube)  # type: ignore[attr-defined]
                self._cache[key] = tuple(names)
            except Exception:  # noqa: BLE001 - missing or unreadable cube
                self._cache[key] = None
        return self._cache[key]


def _watch_lookup(
    watchlist: ElementWatchlist, index: ElementIndex, summary: ElementSummary
) -> dict[str, tuple[str, str]]:
    """Return ``{normalised name or alias: (dimension, principal)}`` for the watch list."""
    lookup: dict[str, tuple[str, str]] = {}
    for dimension, elements in watchlist.entries.items():
        mapping = index.names_and_aliases(dimension)
        for element in elements:
            principal = index.lookup(dimension, element) if mapping is not None else None
            if mapping is not None and principal is None:
                summary.watch_unknown.append(f"{dimension} / {element}")
            target = principal or element
            lookup[normalise(element)] = (dimension, target)
            lookup[normalise(target)] = (dimension, target)
            if mapping is not None:
                for written, name in mapping.items():
                    if normalise(name) == normalise(target):
                        lookup[written] = (dimension, target)
    return lookup


def _exists(index: ElementIndex, dimension: str, element: str) -> str:
    if element in SPECIAL_ELEMENTS or dimension == UNKNOWN:
        return "Unknown"
    if not index.available(dimension):
        return "Unknown"
    return "Yes" if index.lookup(dimension, element) is not None else "No"


def _watch_row(hit: WatchHit) -> ElementRow:
    return ElementRow(
        process=hit.process,
        cube=NO_CUBE,
        dimension=hit.dimension,
        element=hit.element,
        role=hit.role,
        block=hit.block,
        line_no=hit.line_no,
        function=hit.function,
        kind=KIND_WATCHLIST,
        confidence=CONF_LITERAL,
        expression=f"'{hit.written_as}'",
        statement=hit.statement,
    )


def extract_all_elements(
    client: TM1Client,
    *,
    rules: ExclusionRules | None = None,
    progress: ProgressFn | None = None,
    watchlist_file: str | Path | None = None,
    signatures_file: str | Path | None = None,
    index: ElementIndex | None = None,
) -> ElementSummary:
    """Extract element-level lineage for every included process."""
    rules = rules or ExclusionRules.default()
    reader = TIReader(client)
    signatures = load_signatures(signatures_file)
    watchlist = load_element_watchlist(watchlist_file)
    summary = ElementSummary(dry_run=client.dry_run, watched_elements=watchlist.element_count)
    index = index or ElementIndex(tm1_element_loader(client), tm1_element_resolver(client))
    cube_dims = CubeDimensionCache(client.service)
    lookup = _watch_lookup(watchlist, index, summary) if watchlist else {}

    names = reader.list_process_names()
    summary.total_processes = len(names)
    part = partition(names, rules)
    summary.included = part.included_count
    summary.excluded = part.excluded_count

    rows: list[ElementRow] = []
    watch_seen: set[tuple[str, str]] = set()
    total = len(part.included)
    for i, name in enumerate(part.included, start=1):
        try:
            ti = reader.read(name)
            lines = code_lines(ti)
            scanner = ElementScanner(
                ti.name,
                signatures,
                cube_dims,
                source_variables=tuple(v.name for v in ti.variables),
                parameters=tuple(p.name for p in ti.parameters),
            )
            hits = scanner.scan(lines)
            for hit in hits:
                rows.append(
                    ElementRow(
                        process=hit.process,
                        cube=hit.cube,
                        dimension=hit.dimension,
                        element=hit.element,
                        role=hit.role,
                        block=hit.block,
                        line_no=hit.line_no,
                        function=hit.function,
                        kind=hit.kind,
                        confidence=hit.confidence,
                        expression=hit.expression,
                        statement=hit.statement,
                    )
                )
            watch_rows: list[ElementRow] = []
            if lookup:
                explained = {(normalise(h.dimension), normalise(h.element)) for h in hits}
                found, seen = scan_watchlist(ti.name, lines, lookup, explained)
                watch_seen |= seen
                watch_rows = [_watch_row(h) for h in found]
                rows.extend(watch_rows)
                summary.watch_hits += len(watch_rows)
                summary.watch_unexplained += sum(1 for h in found if h.role == "Unexplained")
            summary.parsed_ok += 1
            status = f"{len(hits)} element refs"
            if watch_rows:
                status += f", {len(watch_rows)} watch-list"
        except Exception as exc:  # noqa: BLE001 - isolate per-process failures
            summary.failed += 1
            summary.failed_names.append((name, f"{type(exc).__name__}: {exc}"))
            status = "FAILED"
        if progress is not None:
            progress(i, total, name, status)

    for dimension, elements in watchlist.entries.items():
        for element in elements:
            target = lookup.get(normalise(element), (dimension, element))[1]
            if (normalise(dimension), normalise(target)) not in watch_seen:
                summary.watch_not_found.append(f"{dimension} / {element}")

    checked: list[ElementRow] = []
    for row in rows:
        exists = _exists(index, row.dimension, row.element)
        checked.append(ElementRow(**{**row.__dict__, "element_exists": exists}))  # frozen: rebuild
    rows = checked
    summary.element_references = sum(1 for r in rows if r.kind != KIND_WATCHLIST)
    for row in rows:
        if row.kind == KIND_WATCHLIST:
            continue
        if row.confidence == CONF_LITERAL:
            summary.literal += 1
        elif row.confidence == CONF_RUNTIME:
            summary.runtime += 1
        else:
            summary.resolved += 1

    if not client.dry_run:
        clear_process_element(client)
    summary.rows_written = write_element_lineage(client, rows)
    # Count after aggregation, matching what lands in the cube.
    keys_missing = {r.key for r in rows if r.element_exists == "No"}
    keys_unchecked = {
        r.key for r in rows if r.element_exists == "Unknown" and r.element not in SPECIAL_ELEMENTS
    }
    summary.missing_elements = len(keys_missing)
    summary.unchecked_elements = len(keys_unchecked)
    return summary
