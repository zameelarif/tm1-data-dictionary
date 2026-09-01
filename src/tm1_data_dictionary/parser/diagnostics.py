"""Diagnose unresolved cube references, so we can see what to improve.

After the whole-model run, some cube reads/writes have a target that stayed *dynamic* -
const-propagation could not safely resolve the variable/expression to a concrete cube name.
Those references are counted (the "unresolved" number) but not written to the dictionary.

This module turns that opaque number into an actionable list. It collects the unresolved
cube references and groups them by their **raw target expression** (e.g. ``vDestCube``,
``pTargetCube``, ``Expand('...')``), counting how often each appears and in how many
processes, and remembering **each occurrence's location** (process, block, line) so we can
answer "which processes use this expression, and where?".

It works on the same :class:`~tm1_data_dictionary.parser.references.Reference` objects the
extractor already produces, so it needs no re-parsing logic of its own. Pure data - no TM1,
no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tm1_data_dictionary.parser.references import Reference, Role

# Only cube reads/writes are considered here (matching what }Meta_Process_Cube stores).
_CUBE_ROLES = {Role.CUBE_READ, Role.CUBE_WRITE}


def is_unresolved_cube_ref(ref: Reference) -> bool:
    """True if ``ref`` is a cube read/write whose target did not resolve to a name."""
    if ref.role not in _CUBE_ROLES:
        return False
    if ref.target_is_literal:
        return False  # already a concrete cube name
    return ref.resolved_target is None  # a variable/expr that const-prop could not resolve


@dataclass(frozen=True)
class UnresolvedOccurrence:
    """One unresolved cube reference, with where it appeared."""

    process: str
    expression: str  # the raw target expression (e.g. "vDestCube")
    role: Role
    block: str
    line_no: int


@dataclass
class UnresolvedGroup:
    """All occurrences of one raw target expression, aggregated."""

    expression: str
    occurrences: list[UnresolvedOccurrence] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.occurrences)

    @property
    def processes(self) -> set[str]:
        return {o.process for o in self.occurrences}

    @property
    def process_count(self) -> int:
        return len(self.processes)

    @property
    def example(self) -> UnresolvedOccurrence | None:
        return self.occurrences[0] if self.occurrences else None


@dataclass
class DiagnosticReport:
    """The aggregated diagnostic across one or more processes."""

    occurrences: list[UnresolvedOccurrence] = field(default_factory=list)
    groups: dict[str, UnresolvedGroup] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.occurrences)

    def add(self, occ: UnresolvedOccurrence) -> None:
        """Record one unresolved occurrence."""
        self.occurrences.append(occ)
        group = self.groups.get(occ.expression)
        if group is None:
            group = UnresolvedGroup(expression=occ.expression)
            self.groups[occ.expression] = group
        group.occurrences.append(occ)

    def top(self, limit: int | None = None) -> list[UnresolvedGroup]:
        """Return groups sorted by frequency (then by expression), most common first."""
        ordered = sorted(
            self.groups.values(),
            key=lambda g: (-g.count, g.expression),
        )
        return ordered[:limit] if limit is not None else ordered

    def find(self, expression: str) -> list[UnresolvedOccurrence]:
        """Return every occurrence of an exact target ``expression`` (empty string allowed).

        Useful for "which processes use this expression, and on which lines?" - e.g.
        ``find("")`` locates the blank-target parse edge cases, ``find("pCubeName")``
        locates a parameter-driven utility.
        """
        group = self.groups.get(expression)
        return list(group.occurrences) if group is not None else []


def collect_unresolved(process: str, refs: list[Reference]) -> list[UnresolvedOccurrence]:
    """Return the unresolved cube-reference occurrences for a single process."""
    result: list[UnresolvedOccurrence] = []
    for ref in refs:
        if is_unresolved_cube_ref(ref):
            result.append(
                UnresolvedOccurrence(
                    process=process,
                    expression=ref.target,
                    role=ref.role,
                    block=ref.block,
                    line_no=ref.line_no,
                )
            )
    return result


def diagnose(process_refs: dict[str, list[Reference]]) -> DiagnosticReport:
    """Build a :class:`DiagnosticReport` from a mapping of process name -> references.

    This is the whole-model entry point: pass every included process's references and get
    back the aggregated, grouped view of what stayed unresolved.
    """
    report = DiagnosticReport()
    for process, refs in process_refs.items():
        for occ in collect_unresolved(process, refs):
            report.add(occ)
    return report
