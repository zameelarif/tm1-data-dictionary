# TI lineage (`tm1dd extract`)

`tm1dd extract` reads every included TurboIntegrator process and every chore, and records
how data moves through the model: where it enters, which cubes it lands in, which
processes trigger which, what schedules them, and which dimensions they maintain.

Rules are covered separately, by `tm1dd extract-rules` – see
[Rules analysis](RULES_ANALYSIS.md).

```powershell
tm1dd extract --env dev
tm1dd extract --env dev --quiet                      # summary only
tm1dd extract --env dev --functions D:\tm1dd\functions.txt
```

---

## What it captures

| Cube | Question it answers |
|---|---|
| `}Meta_Process_Cube` | Which cubes does each process read and write? Does each cube still exist? |
| `}Meta_Process_Chain` | Which processes does each process trigger? |
| `}Meta_Process_Datasource` | Where does each process get its data (file, ODBC, view)? |
| `}Meta_Chore_Process` | Which chores run which processes, in what order, and are they active? |
| `}Meta_Process_Dimension` | Which processes insert elements or write attributes in each dimension? |
| `}Meta_Unresolved_Reference` | Which cube targets could not be determined statically? |
| `}Meta_Process_Function` | Where are watched functions (e.g. `ExecuteCommand`) called, and with what arguments? |

Full measure lists are in the [schema reference](SCHEMA_REFERENCE.md#ti-lineage-cubes).

### Cube lineage – `}Meta_Process_Cube`

One row per (process, cube, role), where role is `CubeRead` or `CubeWrite`.

- **Writes:** `CellPutN`, `CellPutS`, `CellIncrementN`.
- **Reads:** `CellGetN`, `CellGetS`, `DB`, `DBRW`.
- `Count` is how many calls were rolled into the row; `FirstBlock`/`FirstLine` locate the
  first one.
- `CubeExists` is `No` when the process references a cube that is not in the instance – a
  dangling reference that will fail at run time. The check includes control cubes, so a
  process writing to a `}`-cube is not reported as missing.

### Chain lineage – `}Meta_Process_Chain`

One row per (caller, callee) for `ExecuteProcess` and `RunProcess`. Put a process on the
callee axis to see everything that triggers it.

### Datasource lineage – `}Meta_Process_Datasource`

One row per process that has a datasource. `}Meta_SourceType` is its own dimension
(`File`, `ODBC`, `View`, `Other`) so it can be filtered or put on rows. `}Meta_Datasource`
holds the literal source (file path, DSN or view), so processes sharing a source line up.
`Detail` holds the ODBC query or the view's cube. `Count` is always 1, which keeps rows
visible under zero suppression. Processes with no datasource produce no row.

### Chore lineage – `}Meta_Chore_Process`

Read directly from chore metadata (no parsing). `StepOrder` is the 0-based position of the
process in the chore; `Active` and `Frequency` come from the chore schedule. Chores are
read once per run.

### Dimension lineage – `}Meta_Process_Dimension`

One row per (process, dimension, role):

- `DimUpdate` – element and component inserts (`DimensionElementInsert`,
  `HierarchyElementInsert`, `...ComponentAdd`).
- `AttrWrite` – attribute writes (`AttrPutS`, `AttrPutN`, ...).

### Unresolved references – `}Meta_Unresolved_Reference`

A cube target that stays an expression (for example a parameter such as `pCubeName`) is
not written to `}Meta_Process_Cube`, because guessing would create false lineage. Instead
it is recorded here, grouped per (process, expression), with the role and first location.
Treat it as a manual-review queue. A blank target is shown as `(blank)`.

### Function usage – `}Meta_Process_Function`

Only functions on the **watch list** are recorded. The list is a text file, by default
`functions.txt` beside `config.yaml`:

```text
# One function per line. Blank lines and # comments are ignored.
ASCIIOutput
ExecuteCommand
CubeSetLogChanges
```

Matching is case-insensitive. One row per (process, function) with the call `Count`, the
first call's block, line and arguments, and every call line in `Lines`. If the file is
missing, function capture is skipped and the cube is not cleared.

---

## How targets are resolved

Each process is split into its Prolog, Metadata, Data and Epilog blocks, comments are
removed, and statements split across lines are joined. Then:

1. **Reference extraction** – each recognised function's target argument is taken from
   the right position (a write names the value first and the cube second).
2. **Constant propagation** – a variable assigned a literal (`cCube = 'Sales';`) is
   replaced by its value, following chains of variables. A variable assigned more than one
   value, or assigned inside `IF`/`WHILE`, is left unresolved rather than guessed.
3. Anything still not a literal goes to `}Meta_Unresolved_Reference`.

To see why a target was not resolved, run:

```powershell
tm1dd extract-refs "<process>" --env dev
tm1dd show-vars "<process>" --all-assignments --env dev
tm1dd diagnose-unresolved --env dev
```

---

## Exclusions

Framework, utility and work-in-progress processes are excluded before parsing. Defaults:

| Rule | Values |
|---|---|
| Name patterns (glob, case-insensitive) | `}*`, `bedrock.*`, `cubewise.*`, `arc.*`, `pulse.*`, `pa.tools.*` |
| Substrings (case-insensitive) | `test`, `temp`, `tmp`, `scratch`, `sandbox`, `_dev`, `_old`, `_bak` |

An explicit include list always wins, and an explicit exclude list names exact processes.
Every excluded process is counted in the summary with its reason.

---

## Run summary

| Line | Meaning |
|---|---|
| Processes: total / included / excluded | After exclusions |
| Parsed OK / failed | Failed processes are listed with the error; the run continues |
| Cube-lineage, chain, datasource, chore, dimension, function rows | Rows written to each cube |
| Unresolved cube / chain / dimension references | Targets that stayed dynamic |
| Cube references to missing cubes | Rows with `CubeExists = No` |

---

## Data-flow map

```powershell
tm1dd export-graph --env dev --out data_flow.html --title "Model data flow"
```

Produces a single HTML page with processes, cubes, datasources and chores as nodes, and
reads, writes, triggers and feeds as edges, with a dropdown to jump to any node. The page
loads the `vis-network` library from a CDN on first open; for a fully offline file,
download `vis-network.min.js` once and pass `--vis-js <path>` so it is embedded.
`data_flow.html` and `vis-network.min.js` are generated or third-party files and are
excluded from Git.
