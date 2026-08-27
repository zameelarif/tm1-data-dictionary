"""Write a run record into the ``}Meta_Extraction_Audit`` cube.

Each extractor run is one *row* in the audit cube, identified by a unique element in the
``}Meta_ExtractionRun`` dimension (an ISO-8601 UTC timestamp). Writing a record therefore
has two steps:

1. **Add the run element** to ``}Meta_ExtractionRun`` (the row key), if it does not exist.
2. **Write the measure cells** for that run into ``}Meta_Extraction_Audit`` (version,
   start/end time, duration, status, warnings).

The writer is deliberately small and testable: the clock is injectable (so tests get
deterministic timestamps), TM1 access goes through the shared ``TM1Client`` (so dry-run
is honoured and connections are managed), and the TM1py ``Element`` class is imported
lazily (so this module loads without TM1py and tests can inject a fake). It contains no
parsing or schema-creation logic - it assumes the schema already exists (created by
``bootstrap``).

The TM1py methods used mirror the public API:
``elements.exists``, ``elements.create``, and ``cells.write(cube_name, cellset_as_dict)``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tm1_data_dictionary.schema import (
    CUBE_EXTRACTION_AUDIT,
    DIM_EXTRACTION_RUN,
    NUMERIC,
)
from tm1_data_dictionary.tm1_client import TM1Client

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _utc_now() -> datetime:
    """Return the current time in UTC (default clock; overridable in tests)."""
    return datetime.now(UTC)


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415 - deliberate lazy import

    return Element


@dataclass(frozen=True)
class AuditRecord:
    """The measures captured for a single extractor run.

    ``run_id`` is the element key in ``}Meta_ExtractionRun``; the remaining fields are the
    values written against the corresponding elements of ``}Meta_AuditMeasure``.
    """

    run_id: str
    extractor_version: str
    schema_version: str
    start_time: str
    end_time: str
    duration_seconds: float
    exit_status: str
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
            "Warnings": self.warnings,
        }


@dataclass
class AuditWriter:
    """Writes :class:`AuditRecord`s into the audit cube via a :class:`TM1Client`.

    Args:
        client: a connected TM1 client.
        clock: a zero-argument callable returning the current ``datetime``. Injectable so
            tests can pin the timestamp; defaults to UTC now.
    """

    client: TM1Client
    clock: Callable[[], datetime] = field(default=_utc_now)

    def new_run_id(self) -> str:
        """Return a unique run identifier: an ISO-8601 UTC timestamp to the second."""
        return self.clock().strftime(_ISO_FORMAT)

    def write(self, record: AuditRecord) -> None:
        """Add the run element (if needed) and write the record's measure cells.

        Raises:
            TM1ClientError: if the client is in dry-run mode (nothing is written).
        """
        self.client.ensure_writable("write audit record")
        service = self.client.service

        # 1. Ensure the run element exists as the row key.
        if not service.elements.exists(DIM_EXTRACTION_RUN, DIM_EXTRACTION_RUN, record.run_id):
            element_cls = _load_element_class()
            service.elements.create(
                DIM_EXTRACTION_RUN,
                DIM_EXTRACTION_RUN,
                element_cls(record.run_id, NUMERIC),
            )

        # 2. Write the measure cells: {(run_id, measure): value}.
        cellset = {(record.run_id, measure): value for measure, value in record.as_cells().items()}
        service.cells.write(cube_name=CUBE_EXTRACTION_AUDIT, cellset_as_dict=cellset)

    def record_run(
        self,
        *,
        extractor_version: str,
        schema_version: str,
        start_time: datetime,
        exit_status: str = "Success",
        warnings: str = "",
    ) -> AuditRecord:
        """Build a record for a completed run, write it, and return it.

        Computes ``end_time`` and ``duration_seconds`` from ``start_time`` and the clock,
        so callers only supply what they know.
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
            warnings=warnings,
        )
        self.write(record)
        return record
