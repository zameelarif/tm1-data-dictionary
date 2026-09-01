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
diagnostics complete. The tool extracts cube lineage for an entire instance in one command,
writes it to `}Meta_Process_Cube`, and can diagnose what stays unresolved.

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
  `tm1dd inspect-process` shows any TI's shape.

## F2. blocks.py — segmentation + string-aware comment stripping
- Splits blocks into numbered `CodeLine`s, stripping `#` comments with a character scanner that
  respects single-quoted strings and the doubled-quote escape. Proven on real code — it correctly
  ignored commented-out `CellPutN` lines a naive parser would have miscounted.

## F3. references.py — the first genuine lineage
- Finds calls to the TI functions we care about, using whole-word regex matching and balanced-paren
  argument extraction. Ran against the Cubewise Day loaders and the anonymised mapping-cube loader
  (406 references from 1,500 lines, instantly).

## F4. const_prop.py — resolve variables to real names (transitive)
- Builds a safe variable->literal table. Resolves direct literals, follows single-variable chains
  transitively to a fixed point (with a cycle guard), handles simple concatenation. Refuses to
  resolve anything conditional/varying/function-derived — correctness over coverage. Resolved all
  122 `cCube` references in the real loader to `Food_Weekly_Sales`, `cMappingCube` to `DW_Mapping`.

## F5. assignments.py — the variable dictionary (owner's idea)
- Captures *every* variable assignment with a per-variable "derived from" summary. `tm1dd show-vars`.
  Where const-prop won't safely resolve a variable, a developer can see how it got its value.

## F6. Function-aware target selection
- A `TARGET_ARG_INDEX` table records which argument holds the cube/dimension per function
  (`CellPutN`->1, `CellGetN`->0, `AttrPutS`->1). Before this, writes reported the *value* variable;
  after, they report the *cube*. Result on the real loader: complete, correct lineage.

---

# Part G — Landing Lineage in a Cube

## G1. rollup.py — deduplicate references into lineage rows
- Groups cube reads/writes by (resolved cube, role), counting occurrences and keeping the first line.
  Only references that resolve to a named cube are included; still-dynamic ones counted as unresolved.

## G2. process_cube_writer.py — write to }Meta_Process_Cube
- Ensures process/cube/role elements exist, writes measure cells (Count, FirstBlock, FirstLine).
  Dry-run guarded, idempotent, lazy TM1py import. `tm1dd extract-cube "..."` wrote one process's
  lineage and it was sliced in PAfE — the first queryable lineage inside TM1.

---

# Part H — Whole-Model Orchestrator

## H1. exclusions.py — which processes to include
- Applies glob patterns and substrings, with explicit include/exclude lists (explicit-include wins).
  Every exclusion is *recorded* with its reason, never silently dropped.

## H2. extract.py — the orchestrator ("whole model in one command")
- Loops all processes, applies exclusions, parses each, batches the writes. Per-process error
  isolation (one bad process is counted/reported, loop continues) and batched writing. Full
  clear-and-reload (verified TM1py `cells.clear(cube=...)`). Dry-run aware. Returns an
  `ExtractionSummary`. `tm1dd extract` is the command.

## H3. First whole-model run
- 492 processes total, 322 included, 170 excluded, 0 failures, 271 cube rows written,
  **5,012 references unresolved**. Sliced `}Meta_Process_Cube` with `}Meta_Cube` on the rows ->
  impact analysis across the model.

## H4. Widened exclusions — scope before depth
- **Insight (owner):** do exclusions first, then there's less to diagnose. Most of the "included"
  set was framework machinery (`}APQ.*` Pulse, `}tp_*` planning sample, `}pulse_*`, `}src_*`,
  `}Drill_*`) - all `}`-prefixed control objects a dictionary should never analyse.
- **Change:** added `}*` as the leading default exclusion pattern (business processes are never
  `}`-prefixed; `explicit_include` remains the escape hatch). Added `excluded_by_rule()` reporting.
- **Result:** re-run dropped included from 322 -> **117**, excluded 170 -> **375**, and unresolved
  from **5,012 -> 133 (a 97% reduction)**. The remaining 117 are real business processes, and the
  remaining unresolved references are the ones that actually matter. Reducing scope first made the
  depth work ~37x smaller.

---

# Part I — Diagnostics (measure before you optimise)

## I1. diagnostics.py — turn "unresolved" into a prioritised list
- **What:** collects the unresolved cube references (cube reads/writes whose target stayed dynamic),
  groups them by raw target expression, and counts how often each appears and in how many processes.
  `tm1dd diagnose-unresolved` (whole-model top offenders; `--process` for one process's detail).
- **Why:** you can't fix what you can't see. Rather than guessing what to improve, measure which
  expressions cause the misses so any resolution work targets the highest-frequency real problems.
- **Result on the 117-process model:** the 133 unresolved references were dominated by
  **runtime parameters** - `pCubeName` (56) and `pTargetCube` (46) alone were 77% of the total, plus
  `sProcLogCube` (19) and a few small ones, and a tiny **blank-target** group (3, in 2 processes).
- **Key finding:** most of the remaining "unresolved" are `p`-prefixed **parameters** - the cube is
  supplied at *runtime* by the caller, so there is no literal in the source to resolve to. Const-prop
  correctly leaving them unresolved is the *truth* ("this utility writes to whatever cube you pass"),
  not a failure. This reframed "step 1: improve resolution" as mostly *not applicable* - the cube
  lineage is already about as complete as static analysis can make it for these processes.

## I2. --expression filter — from "what" to "where"
- **What:** enhanced each `UnresolvedGroup` to remember its full list of occurrences
  (process/block/line), added `report.find(expression)`, and a CLI `--expression "..."` mode that
  lists every process+line where an exact expression is the unresolved target
  (`--expression ""` locates the blank-target parse edge cases).
- **Why:** turn the diagnostic from a summary into an investigation tool - "which processes use this,
  and where?" The design made this cheap: the report already grouped by expression, so we just had
  each group store its occurrences instead of only counting them.
- **Result:** used to hunt the blank-target references to their exact source lines (in progress) so
  we can fix the small parse edge case producing empty targets.

---

# Current State

- Foundation, write path, full parser, cube writer, whole-model orchestrator, widened exclusions,
  and diagnostics all complete. ~206 tests, all green; ruff/black/mypy clean; committed and pushed.
- Whole-model run: 117 business processes, 271 cube rows, 133 unresolved (mostly runtime parameters).

## Next planned steps

1. **Fix the blank-target parse edge case** (small; found via `--expression ""`).
2. **Decide parameter handling** — optionally record parameter-driven targets as
   "writes to a cube given by parameter pCubeName" (useful lineage for utility processes) rather than
   leaving them out. Product decision.
3. **`}Meta_Process_Chain` writer (step 2)** — fold chain lineage into the orchestrator loop so the
   tool answers "what triggers what? / what breaks if I retire this process?" (chain targets are
   usually literal process names, so a quick, high-yield broadening).
- Later: deployment packaging, then the remaining `}Meta_*` cubes (dimension/element/attribute
  lineage, control flow, runtime log stats) per the spec.

---

*End of journal (to be appended as we build).*
