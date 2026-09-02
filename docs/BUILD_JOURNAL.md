# TM1 Data Dictionary — Build Journal

> A running log of every step in building the TM1 Data Dictionary (Phase 1), with the
> reasoning behind each decision. This document lives in `docs/` so it is version-controlled
> alongside the code and grows as the project does.
>
> NOTE: This journal uses **generic, anonymised** examples only. No client names or real
> production process names are recorded. Where real client TIs were used to pressure-test the
> design, they are referred to only as "Example Loader 1/2" or by their anonymised local names.
> ("Cubewise Day" is the public IBM/Cubewise demo model, safe to mention.)

**Project owner:** Zameel Arif
**Target:** IBM Planning Analytics / TM1 on-premises (v11.x)
**Repo:** private GitHub repository (`zameelarif/tm1-data-dictionary`)
**Status:** foundation + write path + full parser + whole-model orchestrator + exclusions +
diagnostics + **chain lineage** complete. `tm1dd extract` populates both `}Meta_Process_Cube`
(cube lineage) and `}Meta_Process_Chain` (process dependencies) for an entire instance in one pass.

---

## How to read this journal

Each entry follows the same shape:

- **What** — the action we took.
- **Why** — the reasoning (the part you forget later).
- **How** — the concrete commands or files involved.
- **Result** — what we observed / the state we ended in.

---

# Part A — Design Phase (pre-code)

## A1. Problem definition
- Developers can't easily answer "which TI updates this cube / loads this file / updates this
  element? what's the transformation logic? source and target?" Agreed to build a lineage +
  metadata catalogue for the TM1 model.

## A2. Approach: hybrid (static + runtime)
- Static TI parsing as the backbone, runtime log augmentation for run stats. Static is why the
  parser can analyse a whole instance in seconds and even analyse TIs whose cubes don't exist.

## A3. Portability-first, no LLM in Phase 1
- On-prem only, no cloud/v12, no LLM. Pure Python + TM1py, deployable anywhere.

## A4. Store results inside TM1 itself
- The dictionary is written into native `}Meta_*` cubes; developers slice it in PAfE.

## A5. Two real-world stress tests
- Validated the design against two genuine production TIs (anonymised). Surfaced 16 improvements.

## A6. Specification (v1.0 -> v1.1, anonymised)
- `docs/phase1_spec.docx` (v1.1) is the authoritative design reference.

---

# Part B — Environment & Toolchain

- Python 3.13; plain venv (not Anaconda); VS Code; Git + private GitHub from day one.
- A credential was accidentally shared in chat; password rotated. Clean.
- Full toolchain: black, ruff, mypy, pytest, pytest-cov, pre-commit, GitHub Actions CI.

---

# Part C — Scaffolding

- src-layout package with `parser/` and `writers/` subpackages; 17 core files; `.env` gitignored;
  `pip install -e ".[dev]"` registers the `tm1dd` CLI.

---

# Part D — Environment Verification

- `scripts/check_environment.py` proved all five gates (Python, config, connectivity, write
  permission, log access). "write test OK" proved the full plumbing.

---

# Part E — Foundation & Write Path

- **E1** GitHub + CI. **E2-E3** credential abstraction + validated `config.py`.
- **E4** learning-as-we-build set up. **E5** keyring (encrypted secrets), proven end-to-end -
  zero plaintext password on disk. **E6** `tm1_client.py` (context manager, dry-run guard,
  injectable service). **E7** `schema.py` + `bootstrap.py` (idempotent creation). **E8**
  `audit_writer.py` - first data in TM1 via `tm1dd record-run`.

---

# Part F — The Parser (the heart of the tool)

- **F1** `ti_reader.py` (anti-corruption layer over TM1py's process attributes).
- **F2** `blocks.py` (string-aware comment stripping).
- **F3** `references.py` (whole-word regex + balanced-paren argument extraction).
- **F4** `const_prop.py` (transitive/one-hop variable resolution, cycle guard, correctness over
  coverage).
- **F5** `assignments.py` (the variable dictionary; `tm1dd show-vars`).
- **F6** function-aware target selection (`TARGET_ARG_INDEX`) - writes report the cube, not the
  value. Result on the real loader: complete, correct lineage.

---

# Part G — Landing Lineage in a Cube

- **G1** `rollup.py` - dedupe references into cube-lineage rows.
- **G2** `process_cube_writer.py` - write to `}Meta_Process_Cube`. `tm1dd extract-cube` wrote one
  process's lineage; sliced in PAfE - first queryable lineage inside TM1.

---

# Part H — Whole-Model Orchestrator + Exclusions

- **H1** `exclusions.py` (glob patterns + substrings + explicit include/exclude; reasons recorded).
- **H2** `extract.py` - the orchestrator: loop, exclude, parse, batch-write; per-process error
  isolation; full clear-and-reload; dry-run aware. `tm1dd extract`.
- **H3** first whole-model run: 492 total, 322 included, 271 cube rows, 5,012 unresolved.
- **H4** widened exclusions - scope before depth. Added `}*` (business processes are never
  `}`-prefixed). Included 322 -> 117; unresolved 5,012 -> 133 (97% cut). Escape hatch:
  `explicit_include`.

---

# Part I — Diagnostics (measure before you optimise)

- **I1** `diagnostics.py` + `tm1dd diagnose-unresolved` - a "resolution profiler". The 133 was
  dominated by runtime **parameters** (`pCubeName` 56, `pTargetCube` 46 = 77%) - genuinely
  dynamic (cube chosen by the caller), correctly left unresolved.
- **I2** `--expression` filter (`report.find`) - locate every process+line for an expression
  (`--expression ""` finds blank targets).

---

# Part J — Multi-line Statement Joining

- **What:** the diagnostic's blank-target group (3 refs) traced to multi-line `CellPutN` calls -
  a statement split across physical lines, so the cube argument landed on the next line and came
  out blank. Reworked `blocks.py` `code_lines()` to **join physical lines into logical statements**
  using string-aware parenthesis balancing, keeping the start line number.
- **Why:** a line-based parser must reassemble multi-line statements before analysis. Fixed at the
  *root* layer (`blocks.py`), so references, const-prop, and assignments all benefit at once.
- **Result:** blank targets gone (133 -> 130). And cube rows across the model rose (71 -> 73) once
  chain was added and re-run - the fix improved accuracy wherever developers split calls across
  lines, not just the 3 blanks.

---

# Part K — Chain Lineage (process dependencies)

- **K1** `chain_rollup.py` - dedupe `ExecuteProcess`/`RunProcess` references into caller->callee
  rows (mirrors `rollup.py`). Unresolved (parameterised) callees counted, not written.
- **K2** `process_chain_writer.py` - write to `}Meta_Process_Chain`, dimensioned
  Caller(`}Meta_Process`) x Callee(`}Meta_Process_Callee`) x Measure (TM1 needs distinct dimension
  names, so the callee axis is an alias-style second process dimension). Idempotent, dry-run guarded.
- **K3** folded into the orchestrator: `extract.py` now **parses each process once and rolls up
  twice** (cube + chain), writing both cubes in one pass. Bootstrap updated to create all three
  schemas.
- **Result (whole-model run):** 117 included, 0 failures, **73 cube rows + 105 chain rows**,
  130 unresolved cube + 149 unresolved chain (both mostly runtime parameters). The Sales loader
  shows 13 chain rows (its Epilog chain); `Sys.Cub.All.CopyYear` orchestrates 8; a real dependency
  web. Slicing `}Meta_Process_Chain` answers "what does X trigger?" (caller on rows) and "what
  triggers X? / what breaks if I retire X?" (callee on rows) - process-dependency impact analysis.

---

# Current State

- Foundation, write path, full parser, cube writer, orchestrator, exclusions, diagnostics,
  multi-line joining, and chain lineage all complete. 232 tests, all green; ruff/black/mypy clean;
  committed and pushed. `tm1dd extract` produces cube + chain lineage for the whole instance.

## Ideas / backlog (developer-driven)

The following came out of "what would help a developer dropped into a new model?" and a UI idea:

- **More lineage types (per spec):** dimension/element/attribute lineage
  (`}Meta_Process_Dimension`, `}Meta_Process_Element`, `}Meta_Process_Attribute`), datasource
  facts (`}Meta_Process_Datasource` - file/ODBC/view sources), control flow, runtime log stats.
- **Chain diagnostic:** extend `diagnose-unresolved` to chains (what's behind the 149).
- **Parameterised-target handling:** optionally record "writes to a cube given by parameter
  pCubeName" as honest lineage for generic utility processes.
- **Data-flow view / interactive front-end:** an offline HTML page (generated from the `}Meta_*`
  cubes via TM1py) that draws the cube+chain graph - nodes = processes/cubes, edges = reads/writes/
  chains - so a developer can *see* the flow, click a node, and trace upstream/downstream. Feasible
  with a small static-site export (e.g. vis-network / Cytoscape.js) reading a JSON the tool emits.
- **Deployment packaging:** the single-zip "install + config + bootstrap + extract" story so this
  ships to a client.

## Next planned step
- Update docs (this entry), then choose between: the **data-flow HTML export** (high-impact,
  visual), **more lineage types** (richer dictionary), or **deployment packaging** (shippable).

---

*End of journal (to be appended as we build).*
