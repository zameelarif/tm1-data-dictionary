# User guide

How to run `tm1dd` day to day. For what each extraction captures, see
[TI lineage](TI_LINEAGE.md) and [Rules analysis](RULES_ANALYSIS.md).

Every command that talks to TM1 accepts `--env <name>` and `--config <path>`.

---

## Commands

### Setup

| Command | Purpose |
|---|---|
| `tm1dd set-credential --name <entry>` | Store a password in the OS keyring |
| `tm1dd bootstrap` | Create missing `}Meta_*` dimensions and cubes, and add missing elements (e.g. new measures) to existing ones. Never deletes anything |
| `tm1dd bootstrap --check` | Read-only: report what is missing, outdated or needs a rebuild (see [Upgrading the schema](#upgrading-the-schema)) |
| `tm1dd bootstrap --rebuild-cube <name> [--yes]` | Delete one tm1dd cube and create it again – only when `--check` says REBUILD |
| `tm1dd bootstrap --drop-legacy` | Delete cubes renamed in schema 1.6 |
| `tm1dd create-views [--prefix <text>]` | Create or replace public views on every `}Meta_*` cube (see [Saved views](#saved-views)) |
| `tm1dd record-run --status <text>` | Write a test row to `}Meta_Extraction_Audit` to prove the write path |

### Whole-model extraction

| Command | Purpose |
|---|---|
| `tm1dd extract [--functions <file>] [--quiet]` | TI lineage for every included process |
| `tm1dd extract-rules [--quiet]` | Rules analysis for every included cube |
| `tm1dd export-graph [--out <file>] [--title <text>] [--vis-js <file>]` | Interactive HTML map of processes, cubes, datasources and chores |

### Single-process investigation (read-only)

| Command | Purpose |
|---|---|
| `tm1dd list-processes [--contains <text>]` | List process names |
| `tm1dd inspect-process <name>` | Blocks, datasource, variables, parameters |
| `tm1dd extract-refs <name>` | Every lineage reference in one process, with resolved targets |
| `tm1dd show-vars <name> [--all-assignments]` | Where each variable gets its value |
| `tm1dd extract-cube <name>` | Write one process's cube lineage |
| `tm1dd extract-chain <name>` | Write one process's chain dependencies |
| `tm1dd diagnose-unresolved [--top N] [--process <name>] [--expression <text>]` | Report cube targets that stayed dynamic |

---

## Dry-run

`dry_run: true` in `config.yaml` (the default in the template) makes every command read
and report but clear, delete and write nothing (`bootstrap --check` is read-only anyway). Summaries show *(dry-run: not written)*.
Use it on a new environment first, and on production whenever you only need the counts.

---

## How a full run behaves

Both `extract` and `extract-rules`:

1. Apply the exclusion list, and record every excluded object with a reason.
2. Clear their own target cubes once (full clear-and-reload, so stale rows never linger).
3. Read each object once; one unreadable process or cube is counted as failed and the
   run continues.
4. Write each cube in one batch.
5. Record the run in `}Meta_Extraction_Audit`.
6. Print a summary.

Neither command touches the other's cubes, so they can run separately.

---

## Upgrading the schema

New releases sometimes add measures, dimensions or cubes. On an instance that is in use,
with data and views built on the `}Meta_*` cubes, nothing should be deleted unless it has
to be. The upgrade therefore always starts with a read-only check:

```powershell
tm1dd bootstrap --env prod --check
```

It compares every tm1dd dimension and cube with the current schema and lists anything that
is not OK:

| Status | Meaning | What fixes it |
|---|---|---|
| `missing` | The dimension or cube does not exist | `tm1dd bootstrap` creates it |
| `outdated` | Elements are missing, e.g. a measure added by the release | `tm1dd bootstrap` adds them; data and views are kept |
| `REBUILD` | Cannot be fixed in place: the cube's dimensions changed, or an element's type changed | `tm1dd bootstrap --rebuild-cube <name>` |

`--check` ends with the exact commands to run. In most upgrades only `tm1dd bootstrap` is
needed; it never deletes or changes existing data, elements or views. Extra elements that a
newer schema no longer uses are left in place.

### Rebuilding a cube (only when `--check` says REBUILD)

```powershell
tm1dd bootstrap --env prod --rebuild-cube "}Meta_Process_Datasource"
```

1. Lists the cube and the public views on it – TM1 deletes a cube's views with the cube.
2. Asks for confirmation (`--yes` skips this for scripts).
3. Deletes the cube, creates the whole schema up to date, and prints the refill commands.

Only tm1dd cubes can be named; model cubes are refused. Name the option more than once to
rebuild several cubes. If an element's type changed, its dimension is deleted and created
again only when every tm1dd cube using it is being rebuilt in the same command – `--check`
lists those cubes together. Dry-run lists what would be deleted and deletes nothing.

Then refill:

```powershell
tm1dd extract --env prod
tm1dd extract-rules --env prod
tm1dd create-views --env prod
```

Re-create any of your own views that were listed in step 1. The audit cube is never
rebuilt by accident: it is only touched if you name it.

---

## Audit trail: `}Meta_Extraction_Audit`

Every run of `extract` and `extract-rules` adds one element to `}Meta_ExtractionRun`,
named by its UTC end time (e.g. `2026-10-01T09:15:00Z`).

| Measure | Meaning |
|---|---|
| `ExtractorVersion` / `SchemaVersion` | Tool and schema version that produced the data |
| `StartTime` / `EndTime` / `DurationSeconds` | Timing |
| `ExitStatus` | `Success` or `CompletedWithFailures` |
| `RunBy` | Windows user and TM1 user, e.g. `jsmith via TM1_SERVICE_USER` |
| `Warnings` | e.g. how many objects failed |
| Run metrics | Every count in the run summary, as its own measure (`CubeRows`, `MissingElements`, `DeadFeeders`, ...) |

Run metrics are created on first use, so a new release can add metrics without a
re-bootstrap. `extract` and `extract-rules` record different metrics; each run leaves the
other command's metrics empty. Comparing runs shows trends, e.g. a rise in
`MissingElements` means someone renamed or deleted an element that rules still use.

---

## Common workflows

**"Which process loads this cube?"** – `}Meta_Process_Cube`, filter the cube, role
`CubeWrite` (view `tm1dd Cube Writers`). Then `}Meta_Process_Datasource` for where that
process reads from.

**"What breaks if I retire this process?"** – `}Meta_Process_Chain` with the process on
the callee axis, and `}Meta_Process_Chore` for chores that run it.

**"Is this element safe to rename or delete?"** – `}Meta_Rule_Element_Reference`, filter
the element. Any row means a rule or feeder names it.

**"What is broken right now?"** – `}Meta_Rule_Feeder_Finding` view `tm1dd Errors`,
`}Meta_Rule_Element_Reference` with `ElementExists = No` (`tm1dd Broken References`),
`}Meta_Rule_Dependency` with `RelatedCubeExists = No` (`tm1dd Dangling Cubes`), and
`}Meta_Process_Cube` with `CubeExists = No` (`tm1dd Missing Cubes`).

**"Why are some cube targets missing from the lineage?"** – `tm1dd diagnose-unresolved`,
or `}Meta_Process_Unresolved`.

---

## Saved views

`tm1dd create-views --env <name>` creates public MDX views on every `}Meta_*` cube, so
everyone starts from the same views in PAfE, PAW or Architect. Every view name starts with
`tm1dd` (change it with `--prefix`), so they sort together. Re-running replaces them, which
is how they pick up changes after an upgrade. Cubes not yet bootstrapped are skipped, and so
are views that need an element which does not exist yet (for example `tm1dd Ambiguous`
until an ambiguous reference is found) – re-run later to pick them up.

Each view puts the cube's name dimensions on rows and all its measures on columns, with
zero suppression, so only populated rows appear.

### Default views

Every cube has `tm1dd All`, which shows every row.

### Case views

| Cube | View | Shows |
|---|---|---|
| `}Meta_Process_Cube` | `tm1dd Cube Writers` | Which process writes to which cube |
| `}Meta_Process_Cube` | `tm1dd Missing Cubes` | Processes referencing a cube that does not exist |
| `}Meta_Process_Datasource` | `tm1dd File Loaders` | Processes loading from files |
| `}Meta_Process_Datasource` | `tm1dd ODBC Loaders` | Processes loading from ODBC |
| `}Meta_Process_Dimension` | `tm1dd Dimension Builders` | Processes inserting elements into dimensions |
| `}Meta_Rule_Cube` | `tm1dd SkipCheck Cubes` | Cubes with SKIPCHECK, which rely on feeders |
| `}Meta_Rule_Dependency` | `tm1dd Dangling Cubes` | Rules reading or feeding a cube that does not exist |
| `}Meta_Rule_Dependency` | `tm1dd Feeds Into` | Feeders that feed other cubes |
| `}Meta_Rule_Element_Reference` | `tm1dd Broken References` | Rules and feeders naming an element that does not exist |
| `}Meta_Rule_Element_Reference` | `tm1dd Not Checked` | References that could not be checked |
| `}Meta_Rule_Element_Reference` | `tm1dd Ambiguous` | Elements found in more than one dimension |
| `}Meta_Rule_Element_Reference` | `tm1dd Feeder Targets` | Every element a feeder feeds |
| `}Meta_Rule_Feeder_Finding` | `tm1dd Errors` | Feeder findings with severity Error |
| `}Meta_Rule_Feeder_Finding` | `tm1dd Unfed Rules` | Leaf rules no feeder can reach |
| `}Meta_Rule_Feeder_Finding` | `tm1dd Dead Feeders` | Feeders whose target does not exist |
| `}Meta_Rule_Feeder_Finding` | `tm1dd Over-feeding` | Feeders whose target overlaps no rule |
| `}Meta_Rule_Function` | `tm1dd Hierarchy Functions` | Rules affected by hierarchy changes |
| `}Meta_Rule_Function` | `tm1dd Attribute Functions` | Rules affected by attribute renames |

Views are defined in `src/tm1_data_dictionary/views.py` as plain data; add a `ViewDef` to
create a new one. A view can fix a dimension to one element (`pin`) or keep only rows where
a string measure has a value (`where`).

To check one element before renaming it, open `tm1dd All` on
`}Meta_Rule_Element_Reference` and filter `}Meta_Element` to that element.

---

## Scheduling

Both extractions are safe to schedule (for example with Windows Task Scheduler) once
`dry_run: false` is set. Run them as the Windows user who stored the keyring credential.
A daily or post-deployment run keeps the dictionary current and builds the audit trail.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Environment variable 'X' ... is not set` | A `*_env` field holds a value instead of a variable name. Use the literal field (`address:`) or set the variable |
| `Required credential 'None' could not be resolved` | `password_env` is missing or blank for that environment |
| Credential not found under a scheduled task | Store it with `set-credential` while logged in as the task's Windows user |
| A new cube or measure is missing after upgrading | Run `tm1dd bootstrap --env <name>` |
| An extraction fails writing to a `}Meta_*` cube after upgrading | Run `tm1dd bootstrap --env <name> --check` and follow what it says |
| `tm1dd --version` shows the old version | Bump `__version__` in `__init__.py` as well as `pyproject.toml` |
| Config edits seem ignored | Check the file on disk (`Get-Content config.yaml`) – the editor may not have saved |
| Summary lists *Aliases not readable* | See [Rules analysis – How elements are looked up](RULES_ANALYSIS.md#how-elements-are-looked-up) |
