# TM1 Data Dictionary — Build Journal

> A running log of every step in building the TM1 Data Dictionary (Phase 1), with the
> reasoning behind each decision. This document lives in `docs/` so it is version-controlled
> alongside the code and grows as the project does.
>
> NOTE: This journal uses **generic, anonymised** examples only. No client names or real
> production process names are recorded. Where real client TIs were used to pressure-test the
> design, they are referred to only as "Example Loader 1/2" or by their anonymised local names.

**Project owner:** Zameel Arif
**Target:** IBM Planning Analytics / TM1 on-premises (v11.x)
**Repo:** private GitHub repository
**Status:** foundation + write path + full parser core complete; the `}Meta_Process_Cube` writer is next.

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
- **What:** Defined the core problem — TM1 developers can't easily answer "which TI updates this
  cube / loads this file / updates this element? what's the transformation logic? source and target?"
- **Why:** These questions are asked constantly during maintenance and handovers; answering them
  today means manually reading TI code.
- **Result:** Agreed to build a lineage + metadata catalogue for the TM1 model.

## A2. Approach: hybrid (static + runtime)
- **What:** Chose static TI parsing as the backbone, with runtime log augmentation for run stats.
- **Why:** Static parsing captures intent and works offline; log mining adds real-run facts. Static
  is why the parser can analyse a 467-process instance in seconds and even analyse TIs whose cubes
  don't exist.
- **Result:** Static parser is the heart of Phase 1.

## A3. Portability-first, no LLM in Phase 1
- **What:** Constrained Phase 1 to TM1 on-prem, no cloud/v12, no LLM.
- **Why:** Corporate environments rarely allow outbound LLM calls; portability lets us deploy anywhere.
- **Result:** Pure Python + TM1py, deployable to any on-prem instance.

## A4. Store results inside TM1 itself
- **What:** The dictionary is written into native `}Meta_*` cubes and dimensions.
- **Why:** Makes it part of the model; developers slice it in PAfE; inherits TM1 security and backup.
- **Result:** The `}Meta_*` schema is the core of the design.

## A5. Two real-world stress tests
- **What:** Validated the design against two genuine production TIs (anonymised).
- **Why:** Synthetic examples never expose the messy patterns real code uses.
- **Result:** Surfaced 16 improvements (Gaps 1-9 and A-G), all additive.

## A6. Specification (v1.0 -> v1.1, anonymised)
- **What:** Produced a consolidated Phase 1 spec, revised to v1.1, then anonymised.
- **Result:** `docs/phase1_spec.docx` (v1.1) is the authoritative design reference.

---

# Part B — Environment & Toolchain

## B1-B7 (summary)
- Python 3.13 for dev, shipping compatible with 3.10+.
- Plain Python + venv (not Anaconda), to match locked-down client servers.
- VS Code, Git from day one, private GitHub repo.
- A credential was accidentally shared in chat; password rotated, no tokens existed. Clean.
- Full toolchain adopted: black, ruff, mypy, pytest, pytest-cov, pre-commit, GitHub Actions CI, all
  via `pyproject.toml`. One-time setup, compounding daily savings.

---

# Part C — Scaffolding

- src-layout package (`src/tm1_data_dictionary/`) with `parser/` and `writers/` subpackages.
- 17 core files created; `.gitignore` excludes `.env` so secrets never get committed.
- `pip install -e ".[dev]"` registers the `tm1dd` CLI and pulls in the toolchain.
- `.env` loaded explicitly by our code (python-dotenv), so it works identically everywhere.

---

# Part D — Environment Verification

- `scripts/check_environment.py` proved all five gates: Python, config loading, TM1 connectivity,
  TM1 write permission, log access. Connected to the local PA (467 processes, 187 cubes); scratch
  dimension write succeeded. The "write test OK" line proved the full plumbing.

---

# Part E — Foundation & Write Path

## E1. GitHub + CI
- Pushed `main` to the private repo; GitHub Actions CI runs lint + tests on every push.

## E2-E3. Credentials & config backbone
- `CredentialProvider` abstraction + `EnvCredentialProvider`; then `config.py` (validated loader,
  frozen dataclasses, fail-fast). mypy caught an `int()` edge case, fixed before ship.

## E4. Learning-as-we-build
- Set up `LEARNING_LOG.md`, explain-as-we-go notes, and mini-exercises.

## E5. Keyring (encrypted secrets) — proven end-to-end
- `KeyringCredentialProvider` + `ChainedCredentialProvider` + keyring->env `default_provider()`, and a
  `tm1dd set-credential` command. Stored the PA password in Windows Credential Manager, deleted it from
  `.env`, and the diagnostic still connected — zero plaintext password on disk. `config.py` unchanged,
  thanks to the abstraction.

## E6. tm1_client.py — the TM1 connection wrapper
- `TM1Client`: built from `AppConfig`, a context manager (always logs out, even on error), a dry-run
  guard (`ensure_writable`), lazy TM1py import + injectable service for tests, ownership tracking. 12 tests.

## E7. schema.py + bootstrap.py — first `}Meta_*` objects in TM1
- Pure-data schema definitions + idempotent creation. `tm1dd bootstrap` created `}Meta_ExtractionRun`,
  `}Meta_AuditMeasure`, `}Meta_Extraction_Audit` in the local model; a second run said "already present."
- Separation of concerns: schema (what) vs bootstrap (how).

## E8. audit_writer.py — data lands in TM1
- Writes a run record (timestamp element + measure cells) into `}Meta_Extraction_Audit`. Injectable
  clock, dry-run guarded, idempotent element creation. Corrected TM1py method names against the real
  docs before ship. `tm1dd record-run` produced a real, readable row in the cube.

---

# Part F — The Parser (the heart of the tool)

The parser is a static analyser: it reads a TI's source as text and extracts lineage. Built
incrementally, each stage small, tested, and validated against real code.

## F1. ti_reader.py — the input layer (anti-corruption layer)
- **What:** Reads a TI via TM1py and repackages its ~25 attributes into a tidy `TIProcess`
  (`TIDatasource`, `TIVariable`, `TIParameter`), plus a `TIReader` with `list_process_names` /
  `exists` / `read`. 11 tests.
- **Why:** Insulates the whole parser from TM1py's specific attribute names — a future TM1py change
  touches only this file. Verified the Process API (`prolog_procedure`, `datasource_*`) against the docs.
- **Result:** `tm1dd inspect-process` shows any TI's datasource, variables, parameters, and block sizes.

## F2. blocks.py — segmentation + string-aware comment stripping
- **What:** Splits each block into numbered `CodeLine`s, stripping `#` comments with a character-by-
  character scanner that respects single-quoted strings and the doubled-quote escape. 13 tests.
- **Why:** Everything downstream walks over lines; a `#` inside a string (`'Total # of records'`) must
  not be treated as a comment. Proven on real code — it correctly ignored commented-out `CellPutN` lines
  a naive parser would have miscounted as writes.

## F3. references.py — the first genuine lineage
- **What:** Finds calls to the TI functions we care about (cube writes/reads, dim updates, attribute
  writes, chains, external), using whole-word regex matching and balanced-paren argument extraction.
- **Why:** This is the actual "what does this TI touch?" extraction.
- **Result:** Ran against the Cubewise Day GL and Wholesale loaders (correctly found their writes, and
  crucially *ignored* commented-out code), and against the anonymised mapping-cube loader (406 references
  from 1,500 lines, instantly).

## F4. const_prop.py — resolve variables to real names (with transitive resolution)
- **What:** Builds a safe variable->literal table. Resolves direct literals, follows single-variable
  chains **transitively** to a fixed point (with a cycle guard), and handles simple concatenation.
- **Why:** Real code names cubes via variables (`cCube = cSourceCube = 'Food_Weekly_Sales'`). Without
  this, lineage reads "writes to cCube" (useless). With transitive resolution, it reads "writes to
  Food_Weekly_Sales" (queryable).
- **Safety:** refuses to resolve anything assigned conditionally, assigned different values, or via a
  function call — correctness over coverage. Proven: resolved all 122 `cCube` references in the real
  loader to `Food_Weekly_Sales`, and `cMappingCube` to `DW_Mapping`, while safely leaving genuinely
  dynamic variables unresolved.

## F5. assignments.py — the variable dictionary (capture facts, defer judgement)
- **What:** Captures *every* variable assignment (RHS, block, line, literal?, in-control-flow?) and a
  per-variable summary with a "derived from" description. `tm1dd show-vars`.
- **Why (owner's idea):** where const-prop safely won't resolve a variable, a developer still wants to
  see how it got its value. This makes a 1,500-line process readable at a glance — e.g. one can see
  `cCube = cSource_Cube = 'Food_Weekly_Sales'`, and the whole mapping-cube pattern (`cMeasure1..61`,
  `cSignMeasure1..61`). Feeds the spec's `}Meta_Process_Variable.DerivedFrom`.

## F6. Function-aware target selection
- **What:** A `TARGET_ARG_INDEX` table records which argument holds the cube/dimension per function
  (`CellPutN`->1, `CellGetN`->0, `AttrPutS`->1, ...). The extractor picks the right argument, then
  resolves it via const-prop. 20 tests.
- **Why:** `CellPutN(value, cube, ...)` puts the cube in argument 1, not 0. Before this, writes reported
  the *value* variable; after, they report the *cube*.
- **Result — complete, correct lineage on the real loader:** all 140 writes -> `Food_Weekly_Sales`;
  all 122 mapping reads -> `DW_Mapping`; read-modify-write pairs both -> `Food_Weekly_Sales`; 13 chains ->
  real process names. A developer now knows exactly what the process does, extracted automatically.

## Parser status
- The parser now extracts correct, readable, production-grade lineage from real 1,500-line processes.
  ~180 tests total, all green. This is the core capability the whole project set out to deliver.

---

# Current State

- Foundation, write path, and the full parser core are complete and committed.
- The lineage is *correct* — targets resolve to real cube/dimension/process names.
- **Next planned step:** the `}Meta_Process_Cube` writer — roll up the references per
  (process, cube, role) and write them into `}Meta_Process_Cube`, so questions like "which processes
  write to Food_Weekly_Sales?" become queryable in PAfE. This is the step where the parser's output
  finally lands as queryable data inside TM1. After that: `}Meta_Process_Chain`, the orchestrator
  (`tm1dd extract` for all processes), then deployment packaging.

---

*End of journal (to be appended as we build).*
