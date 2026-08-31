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
**Status:** foundation + write path + full parser + whole-model orchestrator complete.
The tool extracts cube lineage for an entire instance in one command and writes it into
`}Meta_Process_Cube`.

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
  element? what's the transformation logic? source and target?" Answering it today means manually
  reading TI code. Agreed to build a lineage + metadata catalogue for the TM1 model.

## A2. Approach: hybrid (static + runtime)
- Static TI parsing as the backbone, runtime log augmentation for run stats. Static is why the
  parser can analyse a whole instance in seconds and even analyse TIs whose cubes don't exist.

## A3. Portability-first, no LLM in Phase 1
- On-prem only, no cloud/v12, no LLM. Corporate environments rarely allow outbound LLM calls;
  portability lets us deploy anywhere. Pure Python + TM1py.

## A4. Store results inside TM1 itself
- The dictionary is written into native `}Meta_*` cubes and dimensions; developers slice it in
  PAfE; it inherits TM1 security and backup.

## A5. Two real-world stress tests
- Validated the design against two genuine production TIs (anonymised). Surfaced 16 improvements
  (Gaps 1-9 and A-G), all additive.

## A6. Specification (v1.0 -> v1.1, anonymised)
- `docs/phase1_spec.docx` (v1.1) is the authoritative design reference.

---

# Part B — Environment & Toolchain

- Python 3.13 for dev, shipping compatible with 3.10+.
- Plain Python + venv (not Anaconda), to match locked-down client servers.
- VS Code, Git from day one, private GitHub repo.
- A credential was accidentally shared in chat; password rotated, no tokens existed. Clean.
- Full toolchain: black, ruff, mypy, pytest, pytest-cov, pre-commit, GitHub Actions CI, via
  `pyproject.toml`. One-time setup, compounding daily savings.

---

# Part C — Scaffolding

- src-layout package (`src/tm1_data_dictionary/`) with `parser/` and `writers/` subpackages.
- 17 core files; `.gitignore` excludes `.env`. `pip install -e ".[dev]"` registers the `tm1dd`
  CLI and pulls in the toolchain. `.env` loaded explicitly by our code, so it works everywhere.

---

# Part D — Environment Verification

- `scripts/check_environment.py` proved all five gates: Python, config loading, TM1 connectivity,
  TM1 write permission, log access. The "write test OK" line proved the full plumbing.

---

# Part E — Foundation & Write Path

## E1. GitHub + CI
- Pushed `main`; GitHub Actions CI runs lint + tests on every push.

## E2-E3. Credentials & config backbone
- `CredentialProvider` abstraction + `EnvCredentialProvider`; then `config.py` (validated loader,
  frozen dataclasses, fail-fast). mypy caught an `int()` edge case, fixed before ship.

## E4. Learning-as-we-build
- Set up `LEARNING_LOG.md`, explain-as-we-go notes, and mini-exercises.

## E5. Keyring (encrypted secrets) — proven end-to-end
- `KeyringCredentialProvider` + `ChainedCredentialProvider` + keyring->env `default_provider()`, and
  `tm1dd set-credential`. Stored the PA password in Windows Credential Manager, deleted it from
  `.env`, and the diagnostic still connected — zero plaintext password on disk. `config.py` unchanged,
  thanks to the abstraction.

## E6. tm1_client.py — the TM1 connection wrapper
- `TM1Client`: built from `AppConfig`, a context manager (always logs out, even on error), dry-run
  guard (`ensure_writable`), lazy TM1py import + injectable service for tests, ownership tracking.

## E7. schema.py + bootstrap.py — first `}Meta_*` objects in TM1
- Pure-data schema + idempotent creation. `tm1dd bootstrap` created the audit schema; a second run
  said "already present." Separation of concerns: schema (what) vs bootstrap (how).

## E8. audit_writer.py — data lands in TM1
- Writes a run record into `}Meta_Extraction_Audit`. Injectable clock, dry-run guarded, idempotent.
  Corrected TM1py method names against the real docs before ship. `tm1dd record-run` produced a real
  row in the cube.

---

# Part F — The Parser (the heart of the tool)

The parser is a static analyser: it reads a TI's source as text and extracts lineage. Built
incrementally, each stage tested, and validated against real code.

## F1. ti_reader.py — the input layer (anti-corruption layer)
- Reads a TI via TM1py and repackages its ~25 attributes into a tidy `TIProcess`, plus a `TIReader`
  (`list_process_names`/`exists`/`read`). Insulates the whole parser from TM1py's attribute names.
  Verified the Process API against the docs. `tm1dd inspect-process` shows any TI's shape.

## F2. blocks.py — segmentation + string-aware comment stripping
- Splits blocks into numbered `CodeLine`s, stripping `#` comments with a character scanner that
  respects single-quoted strings and the doubled-quote escape. Proven on real code — it correctly
  ignored commented-out `CellPutN` lines a naive parser would have miscounted.

## F3. references.py — the first genuine lineage
- Finds calls to the TI functions we care about (cube writes/reads, dim updates, attribute writes,
  chains, external), using whole-word regex matching and balanced-paren argument extraction. Ran
  against the Cubewise Day GL/Wholesale loaders and the anonymised mapping-cube loader (406
  references from 1,500 lines, instantly).

## F4. const_prop.py — resolve variables to real names (transitive)
- Builds a safe variable->literal table. Resolves direct literals, follows single-variable chains
  **transitively** to a fixed point (with a cycle guard), and handles simple concatenation. Refuses to
  resolve anything conditional/varying/function-derived — correctness over coverage. Proven: resolved
  all 122 `cCube` references in the real loader to `Food_Weekly_Sales`, `cMappingCube` to `DW_Mapping`.

## F5. assignments.py — the variable dictionary (owner's idea)
- Captures *every* variable assignment (RHS, block, line, literal?, in-control-flow?) with a
  per-variable "derived from" summary. `tm1dd show-vars`. Where const-prop won't safely resolve a
  variable, a developer can see how it got its value (e.g. `cCube = cSource_Cube = 'Food_Weekly_Sales'`).
  Feeds the spec's `}Meta_Process_Variable.DerivedFrom`.

## F6. Function-aware target selection
- A `TARGET_ARG_INDEX` table records which argument holds the cube/dimension per function
  (`CellPutN`->1, `CellGetN`->0, `AttrPutS`->1, ...). Before this, writes reported the *value*
  variable; after, they report the *cube*. Result on the real loader: all 140 writes ->
  `Food_Weekly_Sales`, all 122 mapping reads -> `DW_Mapping`, 13 chains -> real process names.
  Complete, correct, production-grade lineage.

---

# Part G — Landing Lineage in a Cube

## G1. rollup.py — deduplicate references into lineage rows
- Groups cube reads/writes by (resolved cube, role), counting occurrences and keeping the first line.
  140 identical writes become one row: "writes to Food_Weekly_Sales, count=140, first=Data:194". Only
  references that resolve to a named cube are included; still-dynamic ones are counted as "unresolved."

## G2. process_cube_writer.py — write to }Meta_Process_Cube
- Ensures the process/cube/role elements exist, then writes the measure cells (Count, FirstBlock,
  FirstLine). Dry-run guarded, idempotent, lazy TM1py import. Added `}Meta_Process_Cube` and its
  dimensions to `schema.py`/bootstrap.
- **Milestone:** `tm1dd extract-cube "..."` wrote a single process's lineage into `}Meta_Process_Cube`,
  and it was sliced in PAfE — the first queryable lineage inside TM1.

---

# Part H — Whole-Model Orchestrator

## H1. exclusions.py — which processes to include
- Applies Bedrock/utility glob patterns and test/temp substrings, with explicit include/exclude lists
  (explicit-include wins). Every exclusion is *recorded* with its reason, never silently dropped.

## H2. extract.py — the orchestrator ("whole model in one command")
- Loops all processes, applies exclusions, parses each, and batches the writes. Two design points:
  **per-process error isolation** (one malformed process is counted/reported and the loop continues —
  it must not abort the run) and **batched writing** (collect all rows, write once). Full
  clear-and-reload (verified TM1py `cells.clear(cube=...)` against the docs). Dry-run aware. Returns an
  `ExtractionSummary` for the CLI and audit cube. `tm1dd extract` is the command.

## H3. Whole-model milestone (proven on the local instance)
- `tm1dd extract` ran against the local "Cubewise Day" instance:
  - **492 processes total, 322 included, 170 excluded, 0 failures.**
  - **271 cube-lineage rows written.**
  - **5,012 cube references unresolved** (stayed dynamic — the roadmap for resolution improvements).
- Sliced `}Meta_Process_Cube` with `}Meta_Cube` on the rows -> impact analysis across the whole model
  ("which processes write to cube X?"). The tool is now a working data dictionary of the instance.

---

# Current State

- Foundation, write path, full parser, cube writer, and whole-model orchestrator complete.
- `tm1dd extract` analyses an entire instance in one command with zero-failure robustness.
- ~240 tests, all green; ruff/black/mypy clean; everything committed and pushed.

## Next planned steps (agreed order: 3 -> 2 -> 1)

1. **(step 3) Diagnostic for the 5,012 unresolved references** — a mode (e.g. `--show-unresolved`)
   that lists *which* cube references stayed dynamic and their raw targets, so we can see the patterns
   to prioritise. "You can't fix what you can't see."
2. **(step 2) `}Meta_Process_Chain` writer** — fold chain lineage into the same orchestrator loop, so
   the tool answers "what triggers what? / what breaks if I retire this process?" (chain targets are
   usually literal process names, so this is a quick, high-yield broadening).
3. **(step 1) Attack the 5,012** — improve const-prop / targeting for the common unresolved patterns
   surfaced by the diagnostic; each fix lifts resolution across all included processes at once.

Later: deployment packaging (the "easy deploy & run" story), then the remaining `}Meta_*` cubes
(dimension/element/attribute lineage, control flow, runtime log stats) per the spec.

---

*End of journal (to be appended as we build).*
