# Schema reference

Every object `tm1dd bootstrap` creates. All names start with `}Meta_`, so they sit with the
other control objects and are hidden from normal users unless *Display Control Objects*
is on. Schema version: **1.7**.

Naming: every TI lineage cube starts `}Meta_Process_` and every rules cube starts
`}Meta_Rule_`, so each group sorts together. The audit cube is `}Meta_Extraction_Audit`.

Shared dimensions (one dimension, used by several cubes, so views can pivot between them):

| Dimension | Holds | Used by |
|---|---|---|
| `}Meta_Process` | Process names | All TI lineage cubes |
| `}Meta_Cube` | Cube names | `}Meta_Process_Cube` and all rules cubes |
| `}Meta_Dimension` | Dimension names | `}Meta_Process_Dimension`, `}Meta_Rule_Element_Reference` |
| `}Meta_Function` | Function names | `}Meta_Process_Function`, `}Meta_Rule_Function` |

Name dimensions are seeded with `_Init` and filled by the writers. Every extraction fully
clears and reloads its own cubes.

---

## TI lineage cubes

Written by `tm1dd extract`. See [TI lineage](TI_LINEAGE.md).

### `}Meta_Process_Cube`

`}Meta_Process` × `}Meta_Cube` × `}Meta_Role` × `}Meta_ProcessCubeMeasure`

| Element | Values |
|---|---|
| `}Meta_Role` | `CubeRead`, `CubeWrite` |

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | References rolled into the row |
| `FirstBlock` | S | Block of the first reference |
| `FirstLine` | N | Line of the first reference |
| `CubeExists` | S | `Yes` / `No` |

### `}Meta_Process_Chain`

`}Meta_Process` (caller) × `}Meta_Process_Callee` × `}Meta_ProcessChainMeasure`

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | Calls rolled into the row |
| `FirstBlock` | S | Block of the first call |
| `FirstLine` | N | Line of the first call |

### `}Meta_Process_Datasource`

`}Meta_Process` × `}Meta_SourceType` × `}Meta_Datasource` × `}Meta_DatasourceMeasure`

| Element | Values |
|---|---|
| `}Meta_SourceType` | `File`, `ODBC`, `View`, `Other` |

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | Always 1 (keeps rows visible under zero suppression) |
| `Detail` | S | ODBC query or view's cube |

### `}Meta_Process_Chore`

*Named `}Meta_Chore_Process` before schema 1.6.*


`}Meta_Chore` × `}Meta_Process` × `}Meta_ChoreProcessMeasure`

| Measure | Type | Meaning |
|---|---|---|
| `StepOrder` | N | 0-based execution order in the chore |
| `Active` | S | `Yes` / `No` |
| `Frequency` | S | e.g. `P1DT0H0M0S` |

### `}Meta_Process_Dimension`

`}Meta_Process` × `}Meta_Dimension` × `}Meta_DimRole` × `}Meta_ProcessDimMeasure`

| Element | Values |
|---|---|
| `}Meta_DimRole` | `DimUpdate`, `AttrWrite` |

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | References rolled into the row |
| `FirstBlock` | S | Block of the first reference |
| `FirstLine` | N | Line of the first reference |

### `}Meta_Process_Unresolved`

*Named `}Meta_Unresolved_Reference` before schema 1.6.*


`}Meta_Process` × `}Meta_UnresolvedExpression` × `}Meta_UnresolvedMeasure`

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | Occurrences of the expression |
| `Role` | S | `CubeRead` / `CubeWrite` of the first occurrence |
| `FirstBlock` | S | Block of the first occurrence |
| `FirstLine` | N | Line of the first occurrence |

### `}Meta_Process_Function`

`}Meta_Process` × `}Meta_Function` × `}Meta_FunctionMeasure`

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | Calls in the process |
| `FirstBlock` | S | Block of the first call |
| `FirstLine` | N | Line of the first call |
| `FirstArguments` | S | Arguments of the first call |
| `Lines` | S | All call lines, comma-separated |

---

## Rules cubes

Written by `tm1dd extract-rules`. See [Rules analysis](RULES_ANALYSIS.md).

### `}Meta_Rule_Cube` (2a)

`}Meta_Cube` × `}Meta_RuleCubeMeasure`

| Measure | Type | Meaning |
|---|---|---|
| `HasRules` | S | `Yes` / `No` |
| `HasFeeders` | S | `Yes` / `No` |
| `SkipCheck` | S | `Yes` / `No` |
| `FeedStrings` | S | `Yes` / `No` |
| `UndefVals` | S | `Yes` / `No` |
| `RuleStatementCount` | N | Statements before `FEEDERS;` |
| `FeederStatementCount` | N | Statements after `FEEDERS;` |
| `DimensionCount` | N | Dimensions on the cube |

### `}Meta_Rule_Dependency` (2b)

*Named `}Meta_Cube_Rule_Dependency` before schema 1.6.*


`}Meta_Cube` × `}Meta_Rule_RelatedCube` × `}Meta_RuleDependencyType` × `}Meta_RuleDependencyMeasure`

| Element | Values |
|---|---|
| `}Meta_RuleDependencyType` | `RuleRead`, `FeederTarget`, `FeederLookup` |

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | `DB()` references rolled into the row |
| `FirstLine` | N | Line of the first reference |
| `FirstStatement` | S | First referencing statement (truncated) |
| `RelatedCubeExists` | S | `Yes` / `No` |

### `}Meta_Rule_Element_Reference` (2c)

`}Meta_Cube` × `}Meta_Dimension` × `}Meta_Element` × `}Meta_RuleElementRefType` × `}Meta_RuleElementRefMeasure`

| Element | Values |
|---|---|
| `}Meta_RuleElementRefType` | `Area`, `RuleReference`, `FeederSource`, `FeederTarget`, `DBArgument`, `Comparison` |
| `}Meta_Dimension` placeholders | `(Ambiguous)`, `(Unknown)` |

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | References rolled into the row |
| `FirstLine` | N | Line of the first reference |
| `FirstStatement` | S | First referencing statement (truncated) |
| `ElementExists` | S | `Yes` / `No` / `Unknown` |
| `Candidates` | S | Candidate dimensions when ambiguous |
| `TargetCubes` | S | Cube(s) whose dimension holds the element |
| `WrittenAs` | S | Element as written in the rule (alias or case) |

### `}Meta_Rule_Function` (2d)

`}Meta_Cube` × `}Meta_Function` × `}Meta_RuleFunctionMeasure`

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | Uses in rules and feeders |
| `RuleCount` | N | Uses in rule statements |
| `FeederCount` | N | Uses in feeder statements |
| `Category` | S | Lookup, Attribute, Hierarchy, Logic, Control, Text, Date, Math, Other |
| `FirstLine` | N | Line of the first statement using it |
| `FirstStatement` | S | That statement (truncated) |
| `Lines` | S | Every statement line using it |

### `}Meta_Rule_Feeder_Finding` (2e)

`}Meta_Cube` × `}Meta_RuleStatement` × `}Meta_RuleFeederFindingType` × `}Meta_RuleFeederFindingMeasure`

| Element | Values |
|---|---|
| `}Meta_RuleStatement` | `Line 00001` … (statement start line), `Cube` |
| `}Meta_RuleFeederFindingType` | `UnfedRule`, `DeadFeeder`, `FeederFeedsNoRule`, `FeedersWithoutSkipCheck`, `UncheckedRule` |

| Measure | Type | Meaning |
|---|---|---|
| `Count` | N | Occurrences rolled into the row |
| `Severity` | S | `Error`, `Warning` or `Info` |
| `Section` | S | `Rules`, `Feeders` or `Cube` |
| `Line` | N | Statement start line (0 for cube-level) |
| `Statement` | S | The statement (truncated) |
| `Detail` | S | Which target or element caused it |
| `RelatedCube` | S | Target cube(s) for feeder findings |

---

## Audit cube

### `}Meta_Extraction_Audit`

`}Meta_ExtractionRun` × `}Meta_AuditMeasure`

One run element per execution, named by UTC end time. Base measures: `ExtractorVersion`,
`SchemaVersion`, `StartTime`, `EndTime`, `DurationSeconds`, `ExitStatus`, `RunBy`,
`Warnings`. Every count a command reports is added as its own numeric measure
(`processes_total` → `ProcessesTotal`, `missing_elements` → `MissingElements`), created
automatically the first time it is written.
