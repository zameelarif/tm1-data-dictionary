"""Definitions of the ``}Meta_*`` schema as plain, TM1-independent data.

This module describes *what* the dictionary schema looks like - which dimensions,
elements, and cubes exist - using simple frozen dataclasses. It contains **no** TM1py
code and touches **no** live server, so it is trivially unit-testable and serves as the
single, readable source of truth for the schema.

A separate module (``bootstrap.py``) is responsible for *how* to create these objects in
a TM1 instance. Keeping "what" and "how" apart means the schema can be reviewed, diffed,
and tested on its own, and the creation logic stays small and focused.

Phase 1 begins with the simplest cube in the specification - ``}Meta_Extraction_Audit`` -
which records one row per extractor run. Proving the full create-and-write path on this
one cube de-risks the rest of the schema.
"""

from __future__ import annotations

from dataclasses import dataclass

# All dictionary objects share this prefix so they sit cleanly in the control-object
# space and are easy to find and manage.
META_PREFIX = "}Meta_"

# Element type constants (TM1 element types). Subject-dimension leaf elements are
# conventionally Numeric; measure elements are String or Numeric depending on the value
# they hold.
NUMERIC = "Numeric"
STRING = "String"


@dataclass(frozen=True)
class ElementDef:
    """A single element in a dimension, with its TM1 type."""

    name: str
    element_type: str = NUMERIC


@dataclass(frozen=True)
class DimensionDef:
    """A dimension: a name plus its (leaf) elements."""

    name: str
    elements: tuple[ElementDef, ...] = ()


@dataclass(frozen=True)
class CubeDef:
    """A cube: a name plus the ordered names of its dimensions.

    By convention the last dimension is the measure dimension.
    """

    name: str
    dimensions: tuple[str, ...]


@dataclass(frozen=True)
class SchemaDef:
    """A complete schema: the dimensions and cubes to create."""

    dimensions: tuple[DimensionDef, ...]
    cubes: tuple[CubeDef, ...]


# --------------------------------------------------------------------------- #
# The }Meta_Extraction_Audit schema (the first, simplest cube)
# --------------------------------------------------------------------------- #

# Dimension names (defined once, reused, so a typo can't drift between definition and use).
DIM_EXTRACTION_RUN = "}Meta_ExtractionRun"
DIM_AUDIT_MEASURE = "}Meta_AuditMeasure"
CUBE_EXTRACTION_AUDIT = "}Meta_Extraction_Audit"
DIM_PROCESS = "}Meta_Process"
DIM_CUBE = "}Meta_Cube"
DIM_ROLE = "}Meta_Role"
DIM_PROCESS_CUBE_MEASURE = "}Meta_ProcessCubeMeasure"
CUBE_PROCESS_CUBE = "}Meta_Process_Cube"

DIM_PROCESS_CALLEE = "}Meta_Process_Callee"
DIM_PROCESS_CHAIN_MEASURE = "}Meta_ProcessChainMeasure"
CUBE_PROCESS_CHAIN = "}Meta_Process_Chain"

DIM_DATASOURCE = "}Meta_Datasource"
DIM_DATASOURCE_MEASURE = "}Meta_DatasourceMeasure"
CUBE_PROCESS_DATASOURCE = "}Meta_Process_Datasource"

# Measures for }Meta_Process_Datasource.
DATASOURCE_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("SourceType", STRING),  # File | ODBC | View | Other
    ElementDef("Detail", STRING),  # query (ODBC) or owning cube (view)
)

# Measures for }Meta_Process_Chain.
PROCESS_CHAIN_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("Count", NUMERIC),
    ElementDef("FirstBlock", STRING),
    ElementDef("FirstLine", NUMERIC),
)

DIM_CHORE = "}Meta_Chore"
DIM_CHORE_PROCESS_MEASURE = "}Meta_ChoreProcessMeasure"
CUBE_CHORE_PROCESS = "}Meta_Chore_Process"

# Measures for }Meta_Chore_Process.
CHORE_PROCESS_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("StepOrder", NUMERIC),  # 0-based execution order within the chore
    ElementDef("Active", STRING),  # Yes | No
    ElementDef("Frequency", STRING),  # e.g. P1DT0H0M0S
)

# The roles the cube-lineage writer can record (seed elements for }Meta_Role).
CUBE_ROLE_ELEMENTS: tuple[ElementDef, ...] = (
    ElementDef("CubeRead", STRING),
    ElementDef("CubeWrite", STRING),
)

# Measures for }Meta_Process_Cube.
PROCESS_CUBE_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("Count", NUMERIC),
    ElementDef("FirstBlock", STRING),
    ElementDef("FirstLine", NUMERIC),
)

# A harmless seed element so the run dimension (and therefore the cube) can be created
# before any run has been recorded. Real run timestamps are added by the audit writer.
SEED_ELEMENT = ElementDef("_Init", NUMERIC)

# The measures captured for each extractor run. String where the value is text, Numeric
# where it is a count or duration.
AUDIT_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("ExtractorVersion", STRING),
    ElementDef("SchemaVersion", STRING),
    ElementDef("StartTime", STRING),
    ElementDef("EndTime", STRING),
    ElementDef("DurationSeconds", NUMERIC),
    ElementDef("ExitStatus", STRING),
    ElementDef("Warnings", STRING),
)


def audit_schema() -> SchemaDef:
    """Return the schema for the ``}Meta_Extraction_Audit`` cube and its two dimensions."""
    run_dim = DimensionDef(DIM_EXTRACTION_RUN, (SEED_ELEMENT,))
    measure_dim = DimensionDef(DIM_AUDIT_MEASURE, AUDIT_MEASURES)
    audit_cube = CubeDef(
        CUBE_EXTRACTION_AUDIT,
        (DIM_EXTRACTION_RUN, DIM_AUDIT_MEASURE),
    )
    return SchemaDef(dimensions=(run_dim, measure_dim), cubes=(audit_cube,))


def process_cube_schema() -> SchemaDef:
    """Return the schema for }Meta_Process_Cube and its key dimensions.

    }Meta_Process and }Meta_Cube start empty (elements are added by the writer as
    processes and cubes are discovered). }Meta_Role and the measure dimension are
    seeded with their fixed elements. }Meta_Process_Cube is dimensioned
    Process x Cube x Role x Measure.
    """
    process_dim = DimensionDef(DIM_PROCESS, (SEED_ELEMENT,))
    cube_dim = DimensionDef(DIM_CUBE, (SEED_ELEMENT,))
    role_dim = DimensionDef(DIM_ROLE, CUBE_ROLE_ELEMENTS)
    measure_dim = DimensionDef(DIM_PROCESS_CUBE_MEASURE, PROCESS_CUBE_MEASURES)
    cube = CubeDef(
        CUBE_PROCESS_CUBE,
        (DIM_PROCESS, DIM_CUBE, DIM_ROLE, DIM_PROCESS_CUBE_MEASURE),
    )
    return SchemaDef(
        dimensions=(process_dim, cube_dim, role_dim, measure_dim),
        cubes=(cube,),
    )


def process_chain_schema() -> SchemaDef:
    """Return the schema for }Meta_Process_Chain and its key dimensions.

    }Meta_Process_Chain is dimensioned Caller(}Meta_Process) x Callee(}Meta_Process_Callee)
    x Measure. Caller and callee dimensions start with the seed element; the writer adds
    real process names as chains are discovered.
    """
    caller_dim = DimensionDef(DIM_PROCESS, (SEED_ELEMENT,))
    callee_dim = DimensionDef(DIM_PROCESS_CALLEE, (SEED_ELEMENT,))
    measure_dim = DimensionDef(DIM_PROCESS_CHAIN_MEASURE, PROCESS_CHAIN_MEASURES)
    cube = CubeDef(
        CUBE_PROCESS_CHAIN,
        (DIM_PROCESS, DIM_PROCESS_CALLEE, DIM_PROCESS_CHAIN_MEASURE),
    )
    return SchemaDef(
        dimensions=(caller_dim, callee_dim, measure_dim),
        cubes=(cube,),
    )


def process_datasource_schema() -> SchemaDef:
    """Return the schema for }Meta_Process_Datasource and its key dimensions.

    Dimensioned }Meta_Process x }Meta_Datasource x }Meta_DatasourceMeasure. The datasource
    dimension holds recognisable source names; the writer populates it at run time.
    """
    process_dim = DimensionDef(DIM_PROCESS, (SEED_ELEMENT,))
    source_dim = DimensionDef(DIM_DATASOURCE, (SEED_ELEMENT,))
    measure_dim = DimensionDef(DIM_DATASOURCE_MEASURE, DATASOURCE_MEASURES)
    cube = CubeDef(
        CUBE_PROCESS_DATASOURCE,
        (DIM_PROCESS, DIM_DATASOURCE, DIM_DATASOURCE_MEASURE),
    )
    return SchemaDef(
        dimensions=(process_dim, source_dim, measure_dim),
        cubes=(cube,),
    )


def chore_process_schema() -> SchemaDef:
    """Return the schema for }Meta_Chore_Process and its key dimensions.

    Dimensioned }Meta_Chore x }Meta_Process x }Meta_ChoreProcessMeasure. The chore
    dimension holds chore names; the writer populates it at run time.
    """
    chore_dim = DimensionDef(DIM_CHORE, (SEED_ELEMENT,))
    process_dim = DimensionDef(DIM_PROCESS, (SEED_ELEMENT,))
    measure_dim = DimensionDef(DIM_CHORE_PROCESS_MEASURE, CHORE_PROCESS_MEASURES)
    cube = CubeDef(
        CUBE_CHORE_PROCESS,
        (DIM_CHORE, DIM_PROCESS, DIM_CHORE_PROCESS_MEASURE),
    )
    return SchemaDef(
        dimensions=(chore_dim, process_dim, measure_dim),
        cubes=(cube,),
    )
