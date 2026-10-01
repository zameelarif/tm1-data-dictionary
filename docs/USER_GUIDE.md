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
| `tm1dd bootstrap [--drop-legacy]` | Create every `}Meta_*` dimension and cube. Safe to re-run; existing objects are left untouched. `--drop-legacy` deletes cubes renamed in schema 1.6 |
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
and report but clear and write nothing. Summaries show *(dry-run: not written)*. Use it
on a new environment first, and on production whenever you only need the counts.

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
| Run metrics | Every count in the run summary, as its own measure (`CubeRows`, `MissingElements`, ...) |

Run metrics are created on first use, so a new release can add metrics without a
re-bootstrap. `extract` and `extract-rules` record different metrics; each run leaves the
other command's metrics empty. Comparing runs shows trends, e.g. a rise in
`MissingElements` means someone renamed or deleted an element that rules still use.

---

## Common workflows

**"Which process loads this cube?"** – `}Meta_Process_Cube`, filter the cube, role
`CubeWrite`. Then `}Meta_Process_Datasource` for where that process reads from.

**"What breaks if I retire this process?"** – `}Meta_Process_Chain` with the process on
the callee axis, and `}Meta_Process_Chore` for chores that run it.

**"Is this element safe to rename or delete?"** – `}Meta_Rule_Element_Reference`, filter
the element. Any row means a rule or feeder names it.

**"What is broken right now?"** – `}Meta_Rule_Element_Reference` with
`ElementExists = No`, and `}Meta_Rule_Dependency` with `RelatedCubeExists = No`, and
`}Meta_Process_Cube` with `CubeExists = No`.

**"Why are some cube targets missing from the lineage?"** – `tm1dd diagnose-unresolved`,
or `}Meta_Process_Unresolved`.

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
| A new cube is missing after upgrading | Run `tm1dd bootstrap --env <name>` |
| `tm1dd --version` shows the old version | Bump `__version__` in `__init__.py` as well as `pyproject.toml` |
| Config edits seem ignored | Check the file on disk (`Get-Content config.yaml`) – the editor may not have saved |
| Summary lists *Aliases not readable* | See [Rules analysis – Element lookup](RULES_ANALYSIS.md#how-elements-are-looked-up) |
