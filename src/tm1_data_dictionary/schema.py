"""Definitions of the ``}Meta_*`` schema as plain, TM1-independent data.

This module describes *what* the dictionary schema looks like - which dimensions,
elements, and cubes exist - using simple frozen dataclasses. It contains **no** TM1py
code and touches **no** live server, so it is trivially unit-testable and serves as the
single, readable source of truth for the schema.

A separate module (``bootstrap.py``) is responsible for *how* to create these objects in
a TM1 instance. Keeping "what" and "how" apart means the schema can be reviewed, diffed,
and tested on its own, and the creation logic stays small and focused.
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
# Dimension / cube names
# --------------------------------------------------------------------------- #
# Defined once, reused, so a typo can't drift between definition and use.

# --- }Meta_Extraction_Audit ---
DIM_EXTRACTION_RUN = "}Meta_ExtractionRun"
DIM_AUDIT_MEASURE = "}Meta_AuditMeasure"
CUBE_EXTRACTION_AUDIT = "}Meta_Extraction_Audit"

# --- }Meta_Process_Cube ---
DIM_PROCESS = "}Meta_Process"
DIM_CUBE = "}Meta_Cube"
DIM_ROLE = "}Meta_Role"
DIM_PROCESS_CUBE_MEASURE = "}Meta_ProcessCubeMeasure"
CUBE_PROCESS_CUBE = "}Meta_Process_Cube"

# --- }Meta_Process_Chain ---
DIM_PROCESS_CALLEE = "}Meta_Process_Callee"
DIM_PROCESS_CHAIN_MEASURE = "}Meta_ProcessChainMeasure"
CUBE_PROCESS_CHAIN = "}Meta_Process_Chain"

# --- }Meta_Process_Datasource ---
DIM_DATASOURCE = "}Meta_Datasource"
DIM_DATASOURCE_MEASURE = "}Meta_DatasourceMeasure"
CUBE_PROCESS_DATASOURCE = "}Meta_Process_Datasource"

# --- }Meta_Process_Dimension ---
DIM_DIMENSION = "}Meta_Dimension"
DIM_DIM_ROLE = "}Meta_DimRole"
DIM_PROCESS_DIM_MEASURE = "}Meta_ProcessDimMeasure"
CUBE_PROCESS_DIMENSION = "}Meta_Process_Dimension"

# --- }Meta_Chore_Process ---
DIM_CHORE = "}Meta_Chore"
DIM_CHORE_PROCESS_MEASURE = "}Meta_ChoreProcessMeasure"
CUBE_CHORE_PROCESS = "}Meta_Chore_Process"

# --- }Meta_Unresolved_Reference ---
DIM_UNRESOLVED_EXPRESSION = "}Meta_UnresolvedExpression"
DIM_UNRESOLVED_MEASURE = "}Meta_UnresolvedMeasure"
CUBE_UNRESOLVED_REFERENCE = "}Meta_Unresolved_Reference"

# --- }Meta_Process_Function ---
DIM_FUNCTION = "}Meta_Function"
DIM_FUNCTION_MEASURE = "}Meta_FunctionMeasure"
CUBE_PROCESS_FUNCTION = "}Meta_Process_Function"


# --------------------------------------------------------------------------- #
# Seed element + role / measure element sets
# --------------------------------------------------------------------------- #

# A harmless seed element so a dimension (and therefore its cube) can be created
# before any data has been recorded. Real elements are added by the writers.
SEED_ELEMENT = ElementDef("_Init", NUMERIC)

# The roles the dimension-lineage writer records.
DIM_ROLE_ELEMENTS: tuple[ElementDef, ...] = (
    ElementDef("DimUpdate", STRING),  # inserts/maintains elements
    ElementDef("AttrWrite", STRING),  # writes element attributes
)

# The roles the cube-lineage writer records.
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

# Measures for }Meta_Process_Chain.
PROCESS_CHAIN_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("Count", NUMERIC),
    ElementDef("FirstBlock", STRING),
    ElementDef("FirstLine", NUMERIC),
)

# Measures for }Meta_Process_Datasource.
DATASOURCE_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("SourceType", STRING),  # File | ODBC | View | Other
    ElementDef("Detail", STRING),  # query (ODBC) or owning cube (view)
)

# Measures for }Meta_Process_Dimension.
PROCESS_DIM_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("Count", NUMERIC),
    ElementDef("FirstBlock", STRING),
    ElementDef("FirstLine", NUMERIC),
)

# Measures for }Meta_Chore_Process.
CHORE_PROCESS_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("StepOrder", NUMERIC),  # 0-based execution order within the chore
    ElementDef("Active", STRING),  # Yes | No
    ElementDef("Frequency", STRING),  # e.g. P1DT0H0M0S
)

# Measures for }Meta_Unresolved_Reference.
UNRESOLVED_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("Count", NUMERIC),  # how many occurrences of this expression
    ElementDef("Role", STRING),  # CubeRead | CubeWrite (first occurrence)
    ElementDef("FirstBlock", STRING),  # block of the first occurrence
    ElementDef("FirstLine", NUMERIC),  # line of the first occurrence
)

# Measures for }Meta_Process_Function (one row per process/function).
FUNCTION_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("Count", NUMERIC),  # how many times the process calls it
    ElementDef("FirstBlock", STRING),  # block of the first call
    ElementDef("FirstLine", NUMERIC),  # line of the first call
    ElementDef("FirstArguments", STRING),  # arguments of the first call
    ElementDef("Lines", STRING),  # all line numbers, comma-separated
)

# The measures captured for each extractor run. String where the value is text, Numeric
# where it is a count or duration.
AUDIT_MEASURES: tuple[ElementDef, ...] = (
    ElementDef("ExtractorVersion", STRING),
    ElementDef("SchemaVersion", STRING),
    ElementDef("StartTime", STRING),
    ElementDef("EndTime", STRING),
    ElementDef("DurationSeconds", NUMERIC),
    ElementDef("ExitStatus", STRING),
    ElementDef("RunBy", STRING),
    ElementDef("Warnings", STRING),
)


# --------------------------------------------------------------------------- #
# Schema builders
# --------------------------------------------------------------------------- #
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


def process_dimension_schema() -> SchemaDef:
    """Return the schema for }Meta_Process_Dimension and its key dimensions.

    Dimensioned }Meta_Process x }Meta_Dimension x }Meta_DimRole x }Meta_ProcessDimMeasure.
    }Meta_Dimension holds dimension names (added by the writer); }Meta_DimRole is seeded
    with DimUpdate/AttrWrite.
    """
    process_dim = DimensionDef(DIM_PROCESS, (SEED_ELEMENT,))
    dimension_dim = DimensionDef(DIM_DIMENSION, (SEED_ELEMENT,))
    role_dim = DimensionDef(DIM_DIM_ROLE, DIM_ROLE_ELEMENTS)
    measure_dim = DimensionDef(DIM_PROCESS_DIM_MEASURE, PROCESS_DIM_MEASURES)
    cube = CubeDef(
        CUBE_PROCESS_DIMENSION,
        (DIM_PROCESS, DIM_DIMENSION, DIM_DIM_ROLE, DIM_PROCESS_DIM_MEASURE),
    )
    return SchemaDef(
        dimensions=(process_dim, dimension_dim, role_dim, measure_dim),
        cubes=(cube,),
    )


def unresolved_reference_schema() -> SchemaDef:
    """Return the schema for }Meta_Unresolved_Reference and its key dimensions.

    Dimensioned }Meta_Process x }Meta_UnresolvedExpression x }Meta_UnresolvedMeasure.
    It records the cube reads/writes whose target stayed dynamic (const-propagation could
    not resolve the variable/expression to a concrete cube name), grouped per
    (process, raw target expression). This turns the opaque "unresolved" count into a
    queryable, per-process manual-review work queue.
    """
    process_dim = DimensionDef(DIM_PROCESS, (SEED_ELEMENT,))
    expression_dim = DimensionDef(DIM_UNRESOLVED_EXPRESSION, (SEED_ELEMENT,))
    measure_dim = DimensionDef(DIM_UNRESOLVED_MEASURE, UNRESOLVED_MEASURES)
    cube = CubeDef(
        CUBE_UNRESOLVED_REFERENCE,
        (DIM_PROCESS, DIM_UNRESOLVED_EXPRESSION, DIM_UNRESOLVED_MEASURE),
    )
    return SchemaDef(
        dimensions=(process_dim, expression_dim, measure_dim),
        cubes=(cube,),
    )


def process_function_schema() -> SchemaDef:
    """Return the schema for }Meta_Process_Function and its key dimensions.

    Dimensioned }Meta_Process x }Meta_Function x }Meta_FunctionMeasure.

    It records calls to functions on the user-maintained watch list
    (``functions.txt``), aggregated to **one row per (process, function)**. The
    measures keep the call count, the location and arguments of the first call, and
    the full list of line numbers so every call site can still be found in the TI.

    }Meta_Process and }Meta_Function start with the seed element; the writer adds
    real process and function names as they are discovered.
    """
    process_dim = DimensionDef(DIM_PROCESS, (SEED_ELEMENT,))
    function_dim = DimensionDef(DIM_FUNCTION, (SEED_ELEMENT,))
    measure_dim = DimensionDef(DIM_FUNCTION_MEASURE, FUNCTION_MEASURES)
    cube = CubeDef(
        CUBE_PROCESS_FUNCTION,
        (DIM_PROCESS, DIM_FUNCTION, DIM_FUNCTION_MEASURE),
    )
    return SchemaDef(
        dimensions=(process_dim, function_dim, measure_dim),
        cubes=(cube,),
    )
