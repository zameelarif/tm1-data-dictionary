"""Write a run record into the ``}Meta_Extraction_Audit`` cube.

Each extractor run is one *row* in the audit cube, identified by a unique element in the
``}Meta_ExtractionRun`` dimension (an ISO-8601 UTC timestamp). Writing a record has two
steps: add the run element (the row key), then write the measure cells for that run
(version, start/end time, duration, status, who ran it, warnings).

The clock is injectable (so tests get deterministic timestamps), TM1 access goes through
the shared ``TM1Client`` (so dry-run is honoured), and the TM1py ``Element`` class is
imported lazily. It assumes the schema already exists (created by ``bootstrap``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tm1_data_dictionary.tm1_client import TM1Client

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

DIM_EXTRACTION_RUN = "}Meta_ExtractionRun"
CUBE_EXTRACTION_AUDIT = "}Meta_Extraction_Audit"
NUMERIC = "Numeric"


def _utc_now() -> datetime:
    """Return the current time in UTC (default clock; overridable in tests)."""
    return datetime.now(UTC)


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


@dataclass(frozen=True)
class AuditRecord:
    """The measures captured for a single extractor run."""

    run_id: str
    extractor_version: str
    schema_version: str
    start_time: str
    end_time: str
    duration_seconds: float
    exit_status: str
    run_by: str = ""
    warnings: str = ""

    def as_cells(self) -> dict[str, object]:
        """Return a mapping of measure-element name -> value for this record."""
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


@dataclass
class AuditWriter:
    """Writes :class:`AuditRecord`s into the audit cube via a :class:`TM1Client`."""

    client: TM1Client
    clock: Callable[[], datetime] = field(default=_utc_now)

    def new_run_id(self) -> str:
        """Return a unique run identifier: an ISO-8601 UTC timestamp to the second."""
        return self.clock().strftime(_ISO_FORMAT)

    def write(self, record: AuditRecord) -> None:
        """Add the run element (if needed) and write the record's measure cells."""
        self.client.ensure_writable("write audit record")
        service = self.client.service

        if not service.elements.exists(DIM_EXTRACTION_RUN, DIM_EXTRACTION_RUN, record.run_id):
            element_cls = _load_element_class()
            service.elements.create(
                DIM_EXTRACTION_RUN,
                DIM_EXTRACTION_RUN,
                element_cls(record.run_id, NUMERIC),
            )

        cellset = {(record.run_id, measure): value for measure, value in record.as_cells().items()}
        service.cells.write(cube_name=CUBE_EXTRACTION_AUDIT, cellset_as_dict=cellset)

    def record_run(
        self,
        *,
        extractor_version: str,
        schema_version: str,
        start_time: datetime,
        exit_status: str = "Success",
        run_by: str = "",
        warnings: str = "",
    ) -> AuditRecord:
        """Build a record for a completed run, write it, and return it.

        Computes ``end_time`` and ``duration_seconds`` from ``start_time`` and the clock.
        ``run_by`` records who ran the extraction (e.g. ``os_user via tm1_user``).
        """
        end_dt = self.clock()
        duration = max(0.0, (end_dt - start_time).total_seconds())
        record = AuditRecord(
            run_id=self.new_run_id(),
            extractor_version=extractor_version,
            schema_version=schema_version,
            start_time=start_time.strftime(_ISO_FORMAT),
            end_time=end_dt.strftime(_ISO_FORMAT),
            duration_seconds=round(duration, 3),
            exit_status=exit_status,
            run_by=run_by,
            warnings=warnings,
        )
        self.write(record)
        return record
