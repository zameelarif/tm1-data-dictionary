"""Write extraction-run records into the }Meta_Extraction_Audit cube.

Each extractor execution creates one element in the }Meta_ExtractionRun dimension and
writes two kinds of measure:

Base measures (every run):

    ExtractorVersion, SchemaVersion, StartTime, EndTime, DurationSeconds,
    ExitStatus, RunBy, Warnings

Run metrics (whatever the command reports):

    Any numeric metric passed to ``record_run(metrics=...)``. The snake_case key becomes
    a PascalCase measure, e.g. ``processes_total`` -> ``ProcessesTotal`` and
    ``missing_elements`` -> ``MissingElements``. 'extract' and 'extract-rules' report
    different metrics; a run simply leaves the other command's metric cells empty.

Self-healing: before writing, any missing measure element (base or metric) is created in
}Meta_AuditMeasure, so a model bootstrapped before a measure existed keeps working
without a re-bootstrap. New metrics therefore need no change to this module or the
schema - add them to the metrics dict in the CLI and they appear on the next run.

The writer expects the audit cube and its dimensions to have been created by the
bootstrap command.

TM1py objects are imported lazily so unit tests can inject lightweight fakes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from tm1_data_dictionary.tm1_client import TM1Client

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

DIM_EXTRACTION_RUN = "}Meta_ExtractionRun"
DIM_AUDIT_MEASURE = "}Meta_AuditMeasure"
CUBE_EXTRACTION_AUDIT = "}Meta_Extraction_Audit"

NUMERIC = "Numeric"
STRING = "String"

# Measures written on every run, with their TM1 element type.
BASE_MEASURE_TYPES: dict[str, str] = {
    "ExtractorVersion": STRING,
    "SchemaVersion": STRING,
    "StartTime": STRING,
    "EndTime": STRING,
    "DurationSeconds": NUMERIC,
    "ExitStatus": STRING,
    "RunBy": STRING,
    "Warnings": STRING,
}

# A metric key must be snake_case: lowercase letters, digits and underscores.
_METRIC_KEY = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def metric_measure_name(key: str) -> str:
    """Return the audit measure name for a snake_case metric key.

    ``processes_total`` -> ``ProcessesTotal``; ``db_references`` -> ``DbReferences``.
    Raises ValueError for a key that is not snake_case or that would clash with a base
    measure (e.g. ``run_by``).
    """
    if not _METRIC_KEY.match(key):
        raise ValueError(f"Audit metric key must be snake_case, got {key!r}.")
    name = "".join(part.capitalize() for part in key.split("_"))
    if name in BASE_MEASURE_TYPES:
        raise ValueError(f"Audit metric {key!r} clashes with base measure {name!r}.")
    return name


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


def _load_element_class() -> Any:
    """Return the TM1py Element class using a lazy import."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def _as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    A naive datetime is treated as UTC. This keeps the writer tolerant of callers and
    tests that provide a datetime without timezone information.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _numeric(key: str, value: object) -> int | float:
    """Return a metric value as a number, rejecting bools and non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Audit metric {key!r} must be numeric, got {value!r}.")
    return value


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
    metrics: Mapping[str, int | float] = field(default_factory=dict)

    def base_cells(self) -> dict[str, object]:
        """Return the base measures and their cell values."""
        return {
            "ExtractorVersion": self.extractor_version,
            "SchemaVersion": self.schema_version,
            "StartTime": self.start_time,
            "EndTime": self.end_time,
            "DurationSeconds": self.duration_seconds,
            "ExitStatus": self.exit_status,
            "RunBy": self.run_by,
            "Warnings": self.warnings,
        }

    def metric_cells(self) -> dict[str, object]:
        """Return the run-metric measures and their cell values."""
        return {
            metric_measure_name(key): _numeric(key, value) for key, value in self.metrics.items()
        }

    def as_cells(self) -> dict[str, object]:
        """Return every measure name (base and metric) and its cell value."""
        return {**self.base_cells(), **self.metric_cells()}

    def measure_types(self) -> dict[str, str]:
        """Return every measure this record writes, with its TM1 element type."""
        types = dict(BASE_MEASURE_TYPES)
        types.update({name: NUMERIC for name in self.metric_cells()})
        return types


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
        cubes = getattr(self.client.service, "cubes", None)
        if cubes is None or not hasattr(cubes, "exists"):
            return True
        return bool(cubes.exists(CUBE_EXTRACTION_AUDIT))

    def _dimension_exists(self, dimension_name: str) -> bool:
        """Return whether a dimension exists."""
        dimensions = getattr(self.client.service, "dimensions", None)
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
            self.client.service.elements.exists(dimension_name, dimension_name, element_name)
        )

    def _create_element(self, dimension_name: str, element_name: str, element_type: str) -> None:
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
            self._create_element(DIM_EXTRACTION_RUN, run_id, NUMERIC)

    def _ensure_measure_elements(self, measure_types: Mapping[str, str]) -> None:
        """Create any measure this record writes that }Meta_AuditMeasure lacks."""
        for measure_name, element_type in measure_types.items():
            if not self._element_exists(DIM_AUDIT_MEASURE, measure_name):
                self._create_element(DIM_AUDIT_MEASURE, measure_name, element_type)

    def write(self, record: AuditRecord) -> None:
        """Write one audit record to }Meta_Extraction_Audit."""
        self.client.ensure_writable("write audit record")
        # Validate metrics before touching TM1, so a bad key never half-writes a run.
        cells = record.as_cells()
        self._require_audit_schema()
        self._ensure_measure_elements(record.measure_types())
        self._ensure_run_element(record.run_id)

        cellset = {(record.run_id, measure_name): value for measure_name, value in cells.items()}
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
        metrics: Mapping[str, int | float] | None = None,
    ) -> AuditRecord:
        """Create, write, and return a completed extraction audit record.

        Every key in ``metrics`` is written as its own measure (see module docstring).
        """
        start_dt = _as_utc(start_time)
        end_dt = _as_utc(self.clock())
        duration_seconds = max(0.0, (end_dt - start_dt).total_seconds())

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
            metrics=dict(metrics or {}),
        )
        self.write(record)
        return record
