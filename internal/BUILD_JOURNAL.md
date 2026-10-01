# TM1 Data Dictionary — Build Journal

The complete record of how `tm1dd` was built: every step, **why** it was done that way,
what was run, and what happened. Read it top to bottom to understand the project's
history, or jump to a part. The companion file, [LEARNING_LOG.md](LEARNING_LOG.md),
explains the concepts behind the code.

> **Confidentiality.** This journal uses generic examples only. No client names, server
> names, user names or client statistics are recorded. Numbers quoted come from the
> public IBM/Cubewise demo model ("dev"), which is safe to mention. Work done on client
> instances is described without figures.

**Owner:** Zameel Arif
**Target:** IBM Planning Analytics / TM1 on-premises (v11.x), via the REST API
**Repository:** private GitHub repository `tm1-data-dictionary`
**Language / stack:** Python 3.13, TM1py 2.x, click, PyYAML, keyring
**Status at last update:** schema 1.6; TI lineage complete; rules analysis Phases 2a–2d complete;
Phase 2e (feeder gaps) next. See [Part Z](#part-z--current-state-and-how-to-resume).

---

## How to read this journal

Each entry has the same shape:

- **What** – the action taken.
- **Why** – the reasoning. This is the part you forget later, so it is never skipped.
- **How** – files, commands or design choices involved.
- **Result** – what was observed, and the state it left the project in.

Short entries collapse these into a few lines. Parts are in the order the work happened.

### Contents

| Part | Topic |
|---|---|
| A | Design phase (before any code) |
| B | Environment and toolchain |
| C | Project scaffolding |
| D | Environment verification |
| E | Foundation and the write path |
| F | The TI parser |
| G | First lineage in a cube |
| H | Whole-model orchestrator and exclusions |
| I | Diagnostics |
| J | Multi-line statement joining |
| K | Chain lineage |
| L | Datasource lineage and the data-flow map |
| M | Chore lineage |
| N | Dimension, unresolved and function lineage |
| O | Deployment to an offline server |
| P | The `CubeExists` flag |
| Q | Rules analysis: design and Phases 2a–2b |
| R | Phase 2c: rule element references |
| S | Test-suite resync and the audit-writer fix |
| T | Phase 2d: rule function usage |
| U | Documentation rewrite |
| V | Cube renames and the public/private split |
| Z | Current state, backlog and how to resume |

---

## Part A — Design phase (before code)

### A1. Problem definition
**What:** Agreed the problem the tool solves.
**Why:** Developers dropped into an unfamiliar TM1 model cannot quickly answer: *which
process updates this cube? where does this data come from? what does this process
trigger? what breaks if I change this?* Answering means reading hundreds of processes by
hand.
**Result:** Goal: a lineage and metadata catalogue for a TM1 model, generated
automatically.

### A2. Approach: static analysis first
**What:** Chose to read process code (static analysis) rather than watch processes run.
**Why:** Static analysis covers a whole instance in seconds, needs no test data, never
executes anything on the server, and can analyse processes whose target cubes do not even
exist. Runtime statistics from server logs were kept as a later add-on, not the backbone.
**Result:** The parser became the heart of the project.

### A3. Portability first, no LLM
**What:** Pure Python plus TM1py, on-premises, no cloud and no AI model in Phase 1.
**Why:** Must be deployable on locked-down client servers with no internet, and results
must be deterministic and explainable.

### A4. Store results inside TM1
**What:** Write results into native TM1 cubes named `}Meta_*`, not a separate database.
**Why:** TM1 developers already know how to slice cubes in PAfE/PAW. No new tool, no new
licence, no new server. The `}` prefix keeps the cubes with other control objects.

### A5. Stress-tested the design on real processes
**What:** Walked the design through two real production processes (anonymised as
"Example Loader 1/2").
**Why:** Paper designs miss real-world patterns.
**Result:** 16 design improvements, folded into the specification.

### A6. Specification
**What:** Wrote the Phase 1 specification (v1.0, then v1.1, anonymised).
**Result:** `docs/phase1_spec.docx` remains the original design reference. Where this
journal and the spec differ, this journal reflects what was actually built.

---

## Part B — Environment and toolchain

- **Python 3.13** with a plain `venv` (not Anaconda) – simplest to reproduce on a server.
- **VS Code** as the editor; **Git** with a private GitHub repository from day one.
- **Quality tools:** black (formatting), ruff (linting, import order), mypy (types),
  pytest + pytest-cov (tests and coverage), pre-commit (runs them all before each
  commit), GitHub Actions CI. Line length 100 everywhere.
- **Incident:** a credential was accidentally pasted into a chat early on. The password was
  rotated immediately. This is why credentials later moved to the OS keyring (E5) and why
  no password is ever stored in a file or cube.

---

## Part C — Scaffolding

**What:** Created the package skeleton.
**How:** `src/` layout (`src/tm1_data_dictionary/`) with `parser/` and `writers/`
subpackages; `tests/unit/`; `pyproject.toml` declaring the `tm1dd` console script;
`.gitignore` covering `.venv/` and `.env`.
**Why the src layout:** it forces tests to import the *installed* package, which catches
packaging mistakes early.
**Result:** `pip install -e ".[dev]"` registers the `tm1dd` command.

---

## Part D — Environment verification

**What:** `scripts/check_environment.py` checks five gates: Python version, config,
connectivity, write permission and log-folder access.
**Result:** "write test OK" proved the full chain from laptop to TM1.
**Later note:** `tm1dd check` imports this script, but `scripts/` is not inside the
package, so the command fails in an installed wheel. It is a known issue (see Part Z).

---

## Part E — Foundation and the write path

| Step | What | Why |
|---|---|---|
| E1 | GitHub repository + CI | Every change checked automatically |
| E2 | Credential abstraction (`credentials.py`) | Code asks for "a password", not "the keyring"; testable with a fake |
| E3 | Validated configuration (`config.py`) | Fail fast with a clear message instead of failing deep inside TM1py |
| E4 | Learning log started | Building and learning together |
| E5 | OS keyring for secrets (`tm1dd set-credential`) | Zero plaintext passwords on disk |
| E6 | `tm1_client.py` | One connection wrapper: context manager, dry-run guard, injectable service for tests |
| E7 | `schema.py` + `bootstrap.py` | Schema described as plain data; creation is idempotent (safe to re-run) |
| E8 | `audit_writer.py` + `tm1dd record-run` | First data written into TM1 – proved the write path |

**Result:** a working, tested path from command line to a cell in a `}Meta_` cube.

---

## Part F — The TI parser

| Step | Module | What it does | Key decision |
|---|---|---|---|
| F1 | `parser/ti_reader.py` | Reads a process via TM1py into a small dataclass | Anti-corruption layer: the rest of the code never touches TM1py objects directly |
| F2 | `parser/blocks.py` | Splits Prolog/Metadata/Data/Epilog; strips comments | String-aware: a `#` inside quotes is not a comment |
| F3 | `parser/references.py` | Finds lineage function calls and their arguments | Whole-word regex + balanced-bracket argument extraction |
| F4 | `parser/const_prop.py` | Replaces variables with literal values (`cCube` → `'Sales'`) | Correctness over coverage: a variable assigned twice or inside IF/WHILE is not resolved |
| F5 | `parser/assignments.py` | Variable dictionary; `tm1dd show-vars` | Capture every assignment; judge later |
| F6 | `TARGET_ARG_INDEX` in `references.py` | Picks the right argument per function | `CellPutN(value, cube, ...)` names the cube second; reads name it first |

**Result:** on a real loader, complete and correct lineage. `tm1dd extract-refs`
shows it per process.

---

## Part G — First lineage in a cube

- **G1 `parser/rollup.py`:** many references collapse to one row per (process, cube,
  role) with `Count`, `FirstBlock`, `FirstLine`.
- **G2 `writers/process_cube_writer.py`:** writes `}Meta_Process_Cube`. `tm1dd
  extract-cube <name>` wrote one process.
- **Result:** the first queryable lineage inside TM1, sliced in PAfE.

---

## Part H — Whole-model orchestrator and exclusions

### H1. Exclusions (`exclusions.py`)
**What:** Rules that skip framework and work-in-progress processes: glob name patterns,
substrings, an explicit exclude list, and an explicit include list that always wins.
**Why:** Bedrock, Arc, Pulse and test/temp processes clutter the dictionary. Every
excluded process is *recorded with its reason*, never silently dropped.

### H2. Orchestrator (`extract.py`, `tm1dd extract`)
**What:** Loop over every process: exclude → read → parse → roll up → batch-write.
**Design:** per-process error isolation (one bad process never aborts the run); full
clear-and-reload (no stale rows); one write per cube; dry-run aware; progress callback.

### H3. First whole-model run (dev demo model)
492 processes, 322 included, 271 cube rows, **5,012 unresolved** references.

### H4. Scope before depth
**What:** Added `}*` to the exclusion patterns.
**Why:** Business processes are never `}`-prefixed; those are system/framework processes.
Before improving the parser, remove what should not be parsed.
**Result:** included 322 → 117; unresolved 5,012 → 133 (a 97% cut) with no parser change.

---

## Part I — Diagnostics

### I1. `diagnostics.py` + `tm1dd diagnose-unresolved`
**What:** A "resolution profiler" that groups unresolved targets by expression.
**Why:** Measure before optimising – know which pattern dominates before writing code.
**Result:** the 133 were dominated by runtime **parameters** (`pCubeName` 56,
`pTargetCube` 46 – 77%). A parameter's value is chosen by the *caller* at run time, so
leaving it unresolved is correct, not a bug.

### I2. `--expression` and `--process` filters
Locate every process and line for one expression (`--expression ""` finds blank targets),
or list one process's unresolved references with line numbers.

---

## Part J — Multi-line statement joining

**What:** Reworked `code_lines()` in `blocks.py` to join physical lines into complete
statements (logical lines), balancing brackets and respecting strings, while keeping the
starting line number.
**Why:** The 3 blank targets came from `CellPutN(...)` calls split across lines – the cube
argument was on the next line. Fixing it at the root layer (blocks) improved references,
constant propagation and assignments at once.
**Result:** blank targets gone (133 → 130); cube rows rose wherever developers split
calls across lines.

---

## Part K — Chain lineage

- **K1 `parser/chain_rollup.py`:** `ExecuteProcess`/`RunProcess` references → caller/callee
  rows. Parameterised callees are counted, not written.
- **K2 `writers/process_chain_writer.py`:** `}Meta_Process_Chain` =
  `}Meta_Process` × `}Meta_Process_Callee` × measures. TM1 needs distinct dimension names,
  so the callee axis is a second process dimension.
- **K3:** the orchestrator now **parses once and rolls up twice**.
- **Bootstrap gotcha:** the first run failed with a 404 because the new schema was defined
  in `schema.py` but not wired into `bootstrap`. Define *and* create.
- **Result (dev):** 117 included, 0 failures, 73 cube rows, 105 chain rows. Callee on rows
  answers "what breaks if I retire this process?"

---

## Part L — Datasource lineage and the data-flow map

### L1. `}Meta_Process_Datasource`
**What:** Records where each process reads from: file path, ODBC DSN (+ query) or view
(+ its cube). `parser/datasource_rollup.py` + `writers/process_datasource_writer.py`.
**Result (dev):** 74 datasource rows – the other processes have no datasource and
correctly produce no row.

### L2. SourceType became a dimension
**What:** First built as a string measure, then changed to its own seeded dimension
`}Meta_SourceType` (`File`, `ODBC`, `View`, `Other`), with a `Count` measure fixed at 1.
**Why:** A dimension can be filtered and put on rows ("every File loader"); a string
measure cannot. `Count = 1` keeps the row visible under zero suppression.

### L3. Interactive map (`graph.py`, `tm1dd export-graph`)
**What:** One offline HTML page: processes, cubes, datasources (and later chores) as
nodes; reads, writes, triggers and feeds as edges; a dropdown to jump to any node.
Uses vis-network; `--vis-js <file>` embeds the library for a fully offline page.
**Gotchas fixed on the way:** mypy loop-variable reuse (`row` used for three types →
`cube_row`/`chain_row`/`ds_row`); style dictionaries needing explicit
`dict[str, dict[str, object]]` types; `data_flow.html` and `vis-network.min.js` blocked by
the large-file hook, so both were added to `.gitignore` and untracked.
**Result (dev):** 138 processes, 31 cubes, 42 datasources, 252 relationships on one page.

---

## Part M — Chore lineage

**What:** `chore_reader.py` + `writers/process_chore_writer.py` → `}Meta_Chore_Process`
(`StepOrder`, `Active`, `Frequency`).
**Why it is different:** chores are structured metadata, so they are *read*, not parsed,
and they are read **once per run**, after the process loop – not inside it.
**Gotcha:** an integration note showed an alternative (try/except) version; both versions
were pasted, so chores were read twice and a list item contained an `.append()` call. The
fix was a clean `extract.py` with a single, isolated chore read.
**Result:** the full flow is now visible: schedule → source → process → cube → trigger.

---

## Part N — Dimension, unresolved and function lineage

- **N1 `}Meta_Process_Dimension`** (`parser/dim_rollup.py`,
  `writers/process_dimension_writer.py`): which processes insert elements (`DimUpdate`) or
  write attributes (`AttrWrite`) in each dimension.
- **N2 `}Meta_Unresolved_Reference`** (`writers/unresolved_writer.py`): dynamic cube
  targets stored per (process, expression) as a manual-review queue, instead of only being
  counted. Blank targets shown as `(blank)`; long expressions truncated to 250 characters.
- **N3 `}Meta_Process_Function`** (`parser/function_scan.py`,
  `writers/process_function_writer.py`): calls to functions on a user-maintained watch
  list (`functions.txt`, one name per line, `#` comments). One row per (process, function)
  with count, first call's block/line/arguments, and every call line.
  **Why a watch list:** TI has hundreds of functions; only a few (e.g. `ExecuteCommand`,
  `ASCIIOutput`) are interesting to an auditor.
- **N4 Self-healing audit writer:** the audit writer creates any missing measure elements
  before writing, so older environments keep working after new measures are added.
- **N5 Exclusion refinement:** prefix-based exclusion was discussed and prototyped; the
  defaults in use are glob patterns (`}*`, `bedrock.*`, `cubewise.*`, `arc.*`, `pulse.*`,
  `pa.tools.*`) plus substrings (`test`, `temp`, `tmp`, `scratch`, `sandbox`, `_dev`,
  `_old`, `_bak`).

---

## Part O — Deployment to an offline server

The first real deployment was to a client server with **no internet access** and several
TM1 instances on one host. Lessons, in order:

1. **Offline bundle:** build the wheel, `pip download` every dependency into `offline\`,
   convert any `.tar.gz` source archive into a wheel, and prove the bundle installs in a
   throwaway venv with `--no-index` before shipping.
2. **Right interpreter:** the server had more than one Python. Create the venv with
   `py -3.13`, because the wheels were built for 3.13.
3. **Config redesign (no `.env`):** with many instances on one server, one `.env` per
   instance was unworkable. `config.py` was changed so connection fields accept **literal
   values** (`address: server01`) as well as environment-variable names (`address_env:`),
   with a `defaults:` block and per-environment overrides – usually just the port.
4. **`*_env` holds a name, not a value.** `address_env: server01` means "read the variable
   called server01", which caused confusing errors until literal fields existed.
5. **Blank `password_env`** produced `Required credential 'None'`. It must name a keyring
   entry and never be empty.
6. **Version must be bumped in two places** (`pyproject.toml` and `__init__.py`), otherwise
   `--upgrade` does nothing or `tm1dd --version` shows the old number.
7. **Editor not saving:** several "fixes that didn't work" were unsaved editor buffers.
   `Get-Content <file> | Select-String <text>` checks what is really on disk.

**Result:** the tool ran successfully against a production-scale instance; the client
results are not recorded here.

---

## Part P — The `CubeExists` flag

**What:** `}Meta_Process_Cube` gained a `CubeExists` measure, written by
`writers/process_cube_exists_writer.py` from the list of all cubes in the instance.
**Why:** a process that writes to a cube that no longer exists fails at run time. The
check includes control cubes (`get_all_names(skip_control_cubes=False)`), so a process
writing to a `}`-cube is not reported as missing. Names are compared ignoring case and
spaces, as TM1 does.
**Result:** "Cube references to missing cubes" in the run summary, and a `CubeExists = No`
filter in PAfE.

---

## Part Q — Rules analysis: design and Phases 2a–2b

### Q1. Why rules, and why a separate command
**What:** Started analysing cube rules with a new command, `tm1dd extract-rules`.
**Why:** Rules are the other half of a model's logic. Separate from `extract` so TI and
rules succeed or fail independently and can be scheduled separately.
**Plan (phases):** 2a rule facts → 2b cube dependencies → 2c element references → 2d
function usage → 2e feeder-gap detection. Ordered by value delivered against complexity:
2e is behavioural analysis and the hardest, so it comes last.

### Q2. Supporting modules
- `rule_exclusions.py` – same idea as TI exclusions; default excludes only control cubes.
- `rule_reader.py` – anti-corruption layer; `CubeRuleInfo` dataclass with dimension
  names, pragmas, statement counts and raw rule text.

### Q3. Phase 2a – `}Meta_Rule_Cube`
One row per included cube, **including cubes with no rules**: `HasRules`, `HasFeeders`,
`SkipCheck`, `FeedStrings`, `UndefVals`, statement counts, `DimensionCount`. Uses TM1py's
own rule properties.

### Q4. Our own rule parser (`parser/rules/rule_text.py`)
**Why not TM1py's:** TM1py upper-cases statements and only strips comment lines starting
with `#`. Exact element names and line numbers need more.
**What it does:** keeps case and line numbers; strips `#` comments anywhere except inside
strings; handles `''`; splits at `FEEDERS;`; splits rules into area / qualifier /
expression and feeders into source / targets; counts malformed statements instead of
dropping them.

### Q5. Phase 2b – `}Meta_Cube_Rule_Dependency`
Every `DB()` call, typed as `RuleRead`, `FeederTarget` (feeds into another cube) or
`FeederLookup` (a `DB()` used inside a feeder), with `RelatedCubeExists`. A `DB()` whose
cube is an expression is counted as unresolved, never guessed.
**Result (dev):** 16 cubes, 61 `DB()` references, 1 unresolved, 1 dangling (a rule reads a
cube that does not exist in the model).

---

## Part R — Phase 2c: rule element references

### R1. Design
**What:** `}Meta_Rule_Element_Reference` = `}Meta_Cube` × `}Meta_Dimension` ×
`}Meta_Element` × `}Meta_RuleElementRefType` × measures.
**Why:** answers "is this element safe to rename or delete?". Reference type is a
dimension (filterable). `}Meta_Dimension` is shared with TI dimension lineage.
**How:** `parser/rules/rule_element_references.py` extracts literal names from areas,
same-cube `[...]` references, feeder sources/targets, `DB()` arguments, and `!Dim @=
'Element'` comparisons (added beyond the original brief, because renaming such an element
silently changes a rule's result). Dimension resolution: `DB()` by position;
`'Dim':'Element'` by name; plain items by searching all the cube's dimensions – one match
wins, several go under `(Ambiguous)` with candidates, none go under `(Unknown)`.
`ElementExists` is `Yes`, `No` or `Unknown` (cube missing or dimension unreadable).

### R2. First dev run: 18 "missing" elements
**Result:** 218 rows from 405 references, 0 ambiguous – but 18 missing.
**Triage:** 17 were `Actual`/`Budget` across four cubes. The same two elements failing
everywhere pointed to a lookup problem, not 17 broken rules. A screenshot showed the
Version dimension's principal names are `1`, `2`, `3`, with `Actual`/`Budget` as alias
values. The alias read was failing and, because all aliases were read inside one `try`,
one failure discarded them all.

### R3. Layered element lookup (`element_index.py`)
1. Principal names, read once per dimension.
2. Aliases, read per alias so one failure does not hide the others; alias list rebuilt
   from attribute types if the alias endpoint fails.
3. **Ask TM1** with a one-member MDX set, which resolves aliases exactly like the rule
   engine. Cached, capped at 500 queries, and only used after a cheap pass over all
   dimensions finds nothing.
**Result:** missing 18 → 1; "Elements resolved by TM1 lookup: 2".

### R4. Root cause of the alias failure
**What:** the new *Aliases not readable* summary line showed a TM1 MDX syntax error. The
Version dimension contained an element whose name had quotes, a backslash and a comma.
TM1py's attribute read builds an MDX query listing element names without escaping them.
**Fix:** read all alias values for a dimension in **one REST call**
(`Elements?$select=Name,Attributes`), which never builds MDX from names. Per-alias reads
and the TM1 lookup remain as fallbacks. The odd elements could not be deleted (their names
begin with `}`); they are ignored.

### R5. A real defect found
The one remaining missing element is a genuine bug in the demo model: a Retail feeder
targets account `Freight` in the General Ledger, but no such account exists – the GL rule
posts freight to account `5020`. The feeder feeds nothing; values only appear because
another feeder happens to feed `5020`. Left in place on purpose, as a regression check.

---

## Part S — Test-suite resync and the audit-writer fix

### S1. 14 failing tests
**What:** the first full `pytest -q` in a while showed 14 failures – none in new code.
**Why:** for several features, dev runs were done without re-running the whole test suite,
so tests drifted from the code: the audit writer had become self-healing, `extract.py`
had started calling `cubes.get_all_names`, and the datasource cube key had changed.
**Fix:** rewrote `test_audit_writer.py`, `test_extract.py`,
`test_process_datasource_writer.py`. Moved test files into `tests/unit/` (two were in
`tests/`, one was inside `src/.../writers/` and would have shipped in the wheel).
**Rule adopted:** every delivery starts its steps with `pytest -q`, and tests change in the
same delivery as the code they test.

### S2. Audit metrics were being dropped
**What:** the audit writer only wrote a fixed list of 12 metrics; anything else passed in
`metrics` – every `extract-rules` metric, `missing_cube_refs`, `function_rows` – was
silently ignored.
**Fix:** metrics are now open-ended: each snake_case key becomes a PascalCase measure
(`missing_elements` → `MissingElements`) created on first write. Bad keys or non-numeric
values are rejected before anything is written.

### S3. Coverage gaps closed
Tests added for `extract_rules`, `rule_reader`, `rule_exclusions`, `rule_cube_writer`,
`rule_dependency_writer`, `element_index`, `function_scan`, `process_function_writer` and
`unresolved_writer`, several of which were at 0–29%. The suite was green at 375 tests
before these additions; each addition was green when delivered.

---

## Part T — Phase 2d: rule function usage

**What:** `}Meta_Rule_Function` = `}Meta_Cube` × `}Meta_Function` ×
`}Meta_RuleFunctionMeasure`, built by `parser/rules/rule_functions.py` and
`writers/rule_function_writer.py`. Schema version 1.5.
**Design:** no watch list (the rules language is small); calls (`NAME(`) plus the
bracket-less keywords `STET`, `CONTINUE`, `ISLEAF`; text in quotes and `!Dim` references
ignored; each function given a category (Lookup, Attribute, Hierarchy, Logic, Control,
Text, Date, Math, Other). `}Meta_Function` is shared with TI function usage.
**Why the categories:** *Hierarchy* functions change behaviour when a hierarchy is
restructured; *Attribute* functions break when an attribute is renamed.
**Measures:** `Count`, `RuleCount`, `FeederCount`, `Category`, `FirstLine`,
`FirstStatement`, `Lines`.
**Checked against dev rules:** General Ledger – `DB` 12, `ATTRS` 3, `IF` 3, `STET` 2;
Employee – `IF` 6, `DB` 7, `DAYNO` 3, `CONTINUE` 2, `ISLEAF` 1 (commented-out `ATTRS`
correctly ignored).
**Status:** unit tests green; first dev run to be confirmed.

---

## Part U — Documentation rewrite

All documentation was rewritten in full, with TI and rules kept apart:
`README.md`, `INSTALLATION_GUIDE.md`, `USER_GUIDE.md`, `TI_LINEAGE.md`,
`RULES_ANALYSIS.md`, `SCHEMA_REFERENCE.md`, plus this journal and the learning log.

---

## Part V — Cube renames and the public/private split

### V1. Cube renames (schema 1.6)
**What:** renamed three cubes so every TI cube starts `}Meta_Process_` and every rules
cube starts `}Meta_Rule_`:

| Before | After |
|---|---|
| `}Meta_Chore_Process` | `}Meta_Process_Chore` |
| `}Meta_Unresolved_Reference` | `}Meta_Process_Unresolved` |
| `}Meta_Cube_Rule_Dependency` | `}Meta_Rule_Dependency` |

**Why:** cube lists sort alphabetically; the two groups were interleaved.
**How:** only the cube-name values changed in `schema.py` (the Python constant names stayed
the same, so writers and orchestrators needed no change). Dimensions are unchanged. Old
names are kept in `LEGACY_CUBES`; `tm1dd bootstrap --drop-legacy` deletes them. Saved
views on old names must be re-pointed.

### V2. Public/private split
**What:** separated documentation for users from private working notes.
**Why:** the project may be made public for users and contributors. The build journal,
learning log and specification describe how it was built, client-adjacent lessons and
plans – not for publication.
**How:**
- `docs/` – public: installation, user guide, TI lineage, rules analysis, schema reference.
- `internal/` – private: this journal, the learning log, the specification, the backlog.
- The private repository stays the working repository, with full history.
- **A public repository is never made by switching this one to public**, because Git
  history contains every past file, including private notes and earlier configs.
  Instead `scripts/export_public.ps1` copies an allow-list of files into a fresh folder,
  scans it for private terms (listed in `internal/private_terms.txt`, which is never
  exported), and that folder becomes a new repository with a single first commit.

---

## Part Z — Current state and how to resume

### What exists

| Area | Command | Cubes |
|---|---|---|
| TI lineage | `tm1dd extract` | `}Meta_Process_Cube`, `_Chain`, `_Chore`, `_Datasource`, `_Dimension`, `_Function`, `_Unresolved` |
| Rules analysis | `tm1dd extract-rules` | `}Meta_Rule_Cube`, `_Dependency`, `_Element_Reference`, `_Function` |
| Both | – | `}Meta_Extraction_Audit` |

### Reference results on the dev demo model (`extract-rules`)
16 cubes included; 12 with rules, 8 with feeders, 11 with SKIPCHECK; 61 `DB()` references,
1 unresolved, 1 dangling; 405 element references in 218 rows, 1 missing (the Freight
feeder), 0 ambiguous, 1 not checked; 31 dimensions read. Use these to spot regressions.

### How to resume after a long break

1. Open the repository and activate the venv:
   ```powershell
   cd C:\TM1_Models\tm1-data-dictionary
   .\.venv\Scripts\Activate.ps1
   git pull
   ```
2. Prove everything still works:
   ```powershell
   pytest -q
   pre-commit run --all-files
   tm1dd extract-rules --env dev
   ```
   Compare the summary with the reference results above.
3. Read [LEARNING_LOG.md – The project in one page](LEARNING_LOG.md#part-0--the-project-in-one-page)
   to reload the architecture, then the backlog below.

### Working agreement (how changes are delivered)
- Complete replacement files, never snippets.
- Short step-by-step instructions; every step list starts with `pytest -q`.
- Tests change in the same delivery as the code.
- Test with `--env dev`.
- No client names or statistics in any output; no passwords or sensitive data stored.

### Backlog

| Item | Notes |
|---|---|
| **Phase 2e – feeder-gap detection** | Rules without feeders (under-feeding), feeders that feed nothing (like the Freight feeder), over-feeding |
| Findings view | One saved view or cube combining missing elements, dangling cubes, missing cubes and feeder gaps |
| Python 3.10 support | `cli.py` imports `datetime.UTC`, which needs Python 3.11+; switch to `timezone.utc` if 3.10 must be supported |
| `tm1dd check` in the wheel | Move the environment check into the package |
| Alternate hierarchies | Element lookup reads only the default hierarchy |
| Parameterised targets | Resolve a called process's `pCubeName` from the caller's `ExecuteProcess` arguments |
| Runtime statistics | Which processes actually run, how long, how often (from server logs) |
| Graph polish | Edge-type filters, neighbour focus, layout tuning |
