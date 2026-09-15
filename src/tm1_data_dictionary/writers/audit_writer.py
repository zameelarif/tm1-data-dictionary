"""Write extraction-run records into the }Meta_Extraction_Audit cube.

Each extractor execution creates one element in the }Meta_ExtractionRun
dimension and writes the following measures:

    ExtractorVersion
    SchemaVersion
    StartTime
    EndTime
    DurationSeconds
    ExitStatus
    RunBy
    Warnings

The writer expects the audit cube and its dimensions to have been created by
the bootstrap command.

For backward compatibility, the writer checks the }Meta_AuditMeasure
dimension before writing and creates any missing measure elements. This is
useful when an existing TM1 model was bootstrapped before newer audit
measures, such as RunBy, were added.

TM1py objects are imported lazily so unit tests can inject lightweight fakes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tm1_data_dictionary.tm1_client import TM1Client

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

DIM_EXTRACTION_RUN = "}Meta_ExtractionRun"
DIM_AUDIT_MEASURE = "}Meta_AuditMeasure"
CUBE_EXTRACTION_AUDIT = "}Meta_Extraction_Audit"

NUMERIC = "Numeric"
STRING = "String"

AUDIT_MEASURE_TYPES: dict[str, str] = {
    "ExtractorVersion": STRING,
    "SchemaVersion": STRING,
    "StartTime": STRING,
    "EndTime": STRING,
    "DurationSeconds": NUMERIC,
    "ExitStatus": STRING,
    "RunBy": STRING,
    "Warnings": STRING,
    # --- run metrics (added; auto-created by the self-healing writer) ---
    "ProcessesTotal": NUMERIC,
    "ProcessesIncluded": NUMERIC,
    "ProcessesExcluded": NUMERIC,
    "ProcessesFailed": NUMERIC,
    "CubeRows": NUMERIC,
    "ChainRows": NUMERIC,
    "DatasourceRows": NUMERIC,
    "ChoreRows": NUMERIC,
    "DimensionRows": NUMERIC,
    "UnresolvedCubeRefs": NUMERIC,
    "UnresolvedChainRefs": NUMERIC,
    "UnresolvedDimRefs": NUMERIC,
}


def _utc_now() -> datetime:
    """Return the current time in UTC."""

    return datetime.now(UTC)


def _load_element_class() -> Any:
    """Return the TM1py Element class using a lazy import."""

    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def _as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    A naive datetime is treated as UTC. This keeps the writer tolerant of
    callers and tests that provide a datetime without timezone information.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


@dataclass(frozen=True)
class AuditRecord:
    """Measures captured for one completed extractor run."""

    run_id: str
    extractor_version: str
    schema_version: str
    start_time: str
    end_time: str
    duration_seconds: float
    exit_status: str
    run_by: str = ""
    warnings: str = ""
    processes_total: int = 0
    processes_included: int = 0
    processes_excluded: int = 0
    processes_failed: int = 0
    cube_rows: int = 0
    chain_rows: int = 0
    datasource_rows: int = 0
    chore_rows: int = 0
    dimension_rows: int = 0
    unresolved_cube_refs: int = 0
    unresolved_chain_refs: int = 0
    unresolved_dim_refs: int = 0

    def as_cells(self) -> dict[str, object]:
        """Return audit measure names and their cell values."""

        return {
            "ExtractorVersion": self.extractor_version,
            "SchemaVersion": self.schema_version,
            "StartTime": self.start_time,
            "EndTime": self.end_time,
            "DurationSeconds": self.duration_seconds,
            "ExitStatus": self.exit_status,
            "RunBy": self.run_by,
            "Warnings": self.warnings,
            "ProcessesTotal": self.processes_total,
            "ProcessesIncluded": self.processes_included,
            "ProcessesExcluded": self.processes_excluded,
            "ProcessesFailed": self.processes_failed,
            "CubeRows": self.cube_rows,
            "ChainRows": self.chain_rows,
            "DatasourceRows": self.datasource_rows,
            "ChoreRows": self.chore_rows,
            "DimensionRows": self.dimension_rows,
            "UnresolvedCubeRefs": self.unresolved_cube_refs,
            "UnresolvedChainRefs": self.unresolved_chain_refs,
            "UnresolvedDimRefs": self.unresolved_dim_refs,
        }


@dataclass
class AuditWriter:
    """Write AuditRecord objects to the TM1 audit cube."""

    client: TM1Client
    clock: Callable[[], datetime] = field(default=_utc_now)

    def new_run_id(self, timestamp: datetime | None = None) -> str:
        """Return an ISO-8601 UTC identifier for an extraction run."""

        run_time = _as_utc(timestamp if timestamp is not None else self.clock())
        return run_time.strftime(_ISO_FORMAT)

    def _cube_exists(self) -> bool:
        """Return whether the audit cube exists."""

        service = self.client.service
        cubes = getattr(service, "cubes", None)

        if cubes is None or not hasattr(cubes, "exists"):
            return True

        return bool(cubes.exists(CUBE_EXTRACTION_AUDIT))

    def _dimension_exists(self, dimension_name: str) -> bool:
        """Return whether a dimension exists."""

        service = self.client.service
        dimensions = getattr(service, "dimensions", None)

        if dimensions is None or not hasattr(dimensions, "exists"):
            return True

        return bool(dimensions.exists(dimension_name))

    def _require_audit_schema(self) -> None:
        """Raise a clear error when the base audit schema is missing."""

        missing: list[str] = []

        if not self._cube_exists():
            missing.append(f"cube {CUBE_EXTRACTION_AUDIT}")

        if not self._dimension_exists(DIM_EXTRACTION_RUN):
            missing.append(f"dimension {DIM_EXTRACTION_RUN}")

        if not self._dimension_exists(DIM_AUDIT_MEASURE):
            missing.append(f"dimension {DIM_AUDIT_MEASURE}")

        if missing:
            missing_text = ", ".join(missing)
            raise RuntimeError(
                f"Audit schema is incomplete. Missing: {missing_text}. "
                "Run 'tm1dd bootstrap' before running extraction."
            )

    def _element_exists(self, dimension_name: str, element_name: str) -> bool:
        """Return whether an element exists in a dimension's default hierarchy."""

        return bool(
            self.client.service.elements.exists(
                dimension_name,
                dimension_name,
                element_name,
            )
        )

    def _create_element(
        self,
        dimension_name: str,
        element_name: str,
        element_type: str,
    ) -> None:
        """Create one element in a dimension's default hierarchy."""

        element_class = _load_element_class()

        self.client.service.elements.create(
            dimension_name,
            dimension_name,
            element_class(element_name, element_type),
        )

    def _ensure_run_element(self, run_id: str) -> None:
        """Create the extraction-run element when it does not exist."""

        if not self._element_exists(DIM_EXTRACTION_RUN, run_id):
            self._create_element(
                DIM_EXTRACTION_RUN,
                run_id,
                NUMERIC,
            )

    def _ensure_audit_measure_elements(self) -> None:
        """Create audit measures missing from an older audit schema.

        This supports models bootstrapped before newer measures, such as
        RunBy, were introduced.
        """

        for measure_name, element_type in AUDIT_MEASURE_TYPES.items():
            if not self._element_exists(DIM_AUDIT_MEASURE, measure_name):
                self._create_element(
                    DIM_AUDIT_MEASURE,
                    measure_name,
                    element_type,
                )

    def write(self, record: AuditRecord) -> None:
        """Write one audit record to }Meta_Extraction_Audit."""

        self.client.ensure_writable("write audit record")
        self._require_audit_schema()
        self._ensure_audit_measure_elements()
        self._ensure_run_element(record.run_id)

        cellset = {
            (record.run_id, measure_name): value
            for measure_name, value in record.as_cells().items()
        }

        try:
            self.client.service.cells.write(
                cube_name=CUBE_EXTRACTION_AUDIT,
                cellset_as_dict=cellset,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Unable to write extraction run '{record.run_id}' to "
                f"{CUBE_EXTRACTION_AUDIT}: {exc}"
            ) from exc

    def record_run(
        self,
        *,
        extractor_version: str,
        schema_version: str,
        start_time: datetime,
        exit_status: str = "Success",
        run_by: str = "",
        warnings: str = "",
        metrics: dict[str, int] | None = None,
    ) -> AuditRecord:
        """Create, write, and return a completed extraction audit record."""

        start_dt = _as_utc(start_time)
        end_dt = _as_utc(self.clock())

        duration_seconds = max(0.0, (end_dt - start_dt).total_seconds())

        m = metrics or {}
        record = AuditRecord(
            run_id=self.new_run_id(end_dt),
            extractor_version=str(extractor_version),
            schema_version=str(schema_version),
            start_time=start_dt.strftime(_ISO_FORMAT),
            end_time=end_dt.strftime(_ISO_FORMAT),
            duration_seconds=round(duration_seconds, 3),
            exit_status=str(exit_status),
            run_by=str(run_by),
            warnings=str(warnings),
            processes_total=int(m.get("processes_total", 0)),
            processes_included=int(m.get("processes_included", 0)),
            processes_excluded=int(m.get("processes_excluded", 0)),
            processes_failed=int(m.get("processes_failed", 0)),
            cube_rows=int(m.get("cube_rows", 0)),
            chain_rows=int(m.get("chain_rows", 0)),
            datasource_rows=int(m.get("datasource_rows", 0)),
            chore_rows=int(m.get("chore_rows", 0)),
            dimension_rows=int(m.get("dimension_rows", 0)),
            unresolved_cube_refs=int(m.get("unresolved_cube_refs", 0)),
            unresolved_chain_refs=int(m.get("unresolved_chain_refs", 0)),
            unresolved_dim_refs=int(m.get("unresolved_dim_refs", 0)),
        )

        self.write(record)
        return record
