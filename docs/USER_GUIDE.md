# User Guide — TM1 Data Dictionary (`tm1dd`)

This guide explains what the tool does and how to use it day to day. If you have
not installed it yet, start with **INSTALLATION_GUIDE.md**.

---

## 1. What problem this solves

A TM1 model is a web of processes, cubes, dimensions, datasources, and chores.
The relationships between them are hidden inside TurboIntegrator (TI) code.
`tm1dd` reads that code and turns the hidden relationships into **queryable
lineage cubes**, so you can answer questions like:

- Where does the data in this cube come from?
- What runs automatically, and in what order?
- What will break if I change this process or cube?
- Which processes maintain this dimension and its attributes?
- What calls this process?

---

## 2. Core concepts

### Lineage
A recorded relationship, for example *process X writes cube Y* or *chore A runs
process B*. Each relationship type is stored in its own `}Meta_*` cube.

### Roles
For cube lineage, a process is recorded as **reading** or **writing** a cube.
For dimension lineage, a process is recorded as **DimUpdate** (builds/maintains
elements) or **AttrWrite** (sets attributes).

### Const-propagation
TI often targets cubes/dimensions through variables (e.g. `cCube`). Where the
variable's value can be traced to a literal safely, `tm1dd` resolves it. Where
it can't (e.g. the value comes from a cube read), the reference is left
**unresolved** and reported separately.

### Exclusions
Utility/Bedrock, control (`}`-prefixed), and test/temp processes are excluded so
the dictionary reflects your real model. Excluded processes are counted and
recorded, never silently dropped.

### Dry-run
When `dry_run: true`, the whole pipeline runs and reports counts but writes
nothing. Great for a first look at a new environment.

---

## 3. The lineage cubes

| Cube | Dimensions (simplified) | Answers |
|------|-------------------------|---------|
| `}Meta_Process_Cube` | Process × Cube × Role × Measure | Who reads/writes this cube? |
| `}Meta_Process_Chain` | Caller × Callee × Measure | Who triggers whom? |
| `}Meta_Process_Datasource` | Process × Datasource × Measure | Where does data enter? |
| `}Meta_Chore_Process` | Chore × Process × Measure | What runs on a schedule? |
| `}Meta_Process_Dimension` | Process × Dimension × Role × Measure | Who maintains this dimension/attributes? |
| `}Meta_Extraction_Audit` | ExtractionRun × Measure | When/who/what happened in each run? |

---

## 4. Everyday commands

All commands take `--env <name>` to select an environment from `config.yaml`
(defaults to `default_environment`).

### Build the whole dictionary
```powershell
tm1dd extract --env dev
```
Add `--quiet` to show only the summary.

### Create/refresh the schema
```powershell
tm1dd bootstrap --env dev
```

### List processes
```powershell
tm1dd list-processes --env dev --contains sales
```

### Inspect one process
```powershell
tm1dd inspect-process "Cube.Sales.Load" --env dev
```

### See the references in one process
```powershell
tm1dd extract-refs "Cube.Sales.Load" --env dev
```

### See where a variable's value comes from
```powershell
tm1dd show-vars "Cube.Sales.Load" --env dev
tm1dd show-vars "Cube.Sales.Load" --env dev --all-assignments
```

### Extract a single cube's or chain's lineage
```powershell
tm1dd extract-cube "Cube.Sales.Load" --env dev
tm1dd extract-chain "Cube.Sales.Load" --env dev
```

### Record an audit run manually
```powershell
tm1dd record-run --env dev --status Success
```

---

## 5. Understanding the extraction summary

A run ends with something like:

```
  Processes: 492 total, 117 included, 375 excluded
  Parsed OK: 117, failed: 0
  Cube-lineage rows: 73 written
  Chain-lineage rows: 105 written
  Datasource rows: 74 written
  Chore rows: 13 written
  Dimension rows: 58 written
  Unresolved cube references: 130
  Unresolved chain references: 149
  Unresolved dimension references: 9
```

- **included/excluded** — how exclusion rules split your processes.
- **Parsed OK / failed** — per-process parse results (failures are isolated).
- **rows written** — how many lineage facts landed in each cube.
- **Unresolved references** — targets that stayed dynamic and were not written.
  These are candidates for manual review (see next section).

---

## 6. Diagnosing unresolved references

Some cube/dimension targets can't be resolved by static parsing (for example,
a cube name read from another cube at run time). Use `diagnose-unresolved` to
investigate.

### Whole-model "top offenders"
```powershell
tm1dd diagnose-unresolved --env dev --top 25
```

### One process in detail
```powershell
tm1dd diagnose-unresolved --env dev --process "Cube.Sales.Load"
```

### Every place a specific expression appears
```powershell
tm1dd diagnose-unresolved --env dev --expression "cCube"
# Use "" to find blank-target parse edge cases:
tm1dd diagnose-unresolved --env dev --expression ""
```

---

## 7. The interactive data-flow map

Export an HTML map of processes, cubes, datasources, and chores:

```powershell
tm1dd export-graph --env dev --out data_flow.html
```

Open the file in a browser. For a fully offline file (no CDN), pass a local copy
of `vis-network.min.js`:

```powershell
tm1dd export-graph --env dev --out data_flow.html --vis-js .\vis-network.min.js
```

---

## 8. Common use cases

### "I've just inherited this model."
1. `tm1dd extract --env dev`
2. `tm1dd export-graph --env dev --out data_flow.html`
3. Open the map, then slice `}Meta_Chore_Process` to see what runs automatically.

### "Where does this cube's data come from?"
Slice `}Meta_Process_Datasource` and `}Meta_Process_Cube` (writing role) for the
cube.

### "What breaks if I change this process?"
Slice `}Meta_Process_Chain` for the process as a **callee** to see who triggers
it, and as a **caller** to see what it triggers.

### "Which processes maintain this dimension?"
Slice `}Meta_Process_Dimension` filtered to that dimension; the **DimUpdate**
role shows element maintenance, **AttrWrite** shows attribute updates.

---

## 9. Good practice

- Re-run `tm1dd extract` after significant model changes so the dictionary stays
  current.
- Use `dry_run: true` when pointing at an unfamiliar environment for the first
  time.
- Check the audit cube (`}Meta_Extraction_Audit`) to confirm the last successful
  run and watch for unexpected drops in row counts.
- Keep unresolved-reference counts in view — a rising count usually means new
  dynamic code that a human should review.

---

## 10. Getting help

- Every command supports `--help`, e.g. `tm1dd extract --help`.
- For setup and troubleshooting, see **INSTALLATION_GUIDE.md**.
- For development history and the backlog, see **BUILD_JOURNAL.md**.
