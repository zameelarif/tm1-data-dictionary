# TM1 Data Dictionary — Build Journal

> A running log of every step in building the TM1 Data Dictionary (Phase 1), with the
> reasoning behind each decision. This document lives in `docs/` so it is version-controlled
> alongside the code and grows as the project does.
>
> NOTE: This journal uses **generic, anonymised** examples only. No client names or real
> production process names are recorded. Where real client TIs were used to pressure-test the
> design, they are referred to only as "Example Loader 1" and "Example Loader 2".

**Project owner:** Zameel Arif
**Target:** IBM Planning Analytics / TM1 on-premises (v11.x)
**Repo:** private GitHub repository
**Status:** foundation + write path complete; parser is the next module.

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
  cube / loads this file / updates this element? what's the transformation logic? what's the source
  and what does it write?"
- **Why:** These questions are asked constantly during maintenance, impact analysis, and handovers,
  and answering them today means manually reading TI code. A queryable dictionary turns hours of
  code-reading into a single slice in PAfE.
- **Result:** Agreed to build a lineage + metadata catalogue for the TM1 model.

## A2. Approach selection (static vs runtime vs hybrid)
- **What:** Evaluated static TI parsing, runtime log observation, and a hybrid.
- **Why:** Static parsing captures intent and works offline but misses dynamically-built names.
  Runtime log mining catches what actually ran but misses dormant processes and is noisy. The hybrid
  gives the completeness of both.
- **Result:** Chose the **hybrid** — static parser as backbone, log augmentation for runtime facts.

## A3. Portability-first, no LLM in Phase 1
- **What:** Constrained Phase 1 to TM1 on-prem only, no cloud/v12, no LLM dependency.
- **Why:** Corporate TM1 environments rarely allow outbound calls to public LLM endpoints, and
  portability lets us drop the tool onto any client site in an afternoon. Dropping the LLM improves
  deployability by removing the last external dependency.
- **Result:** Phase 1 is pure Python + TM1py, deployable to any on-prem instance.

## A4. Store results inside TM1 itself
- **What:** Decided the dictionary is written into native TM1 cubes and dimensions (`}Meta_*`).
- **Why:** Makes the dictionary part of the model — copy the objects across, re-run, done. Developers
  slice it in PAfE/PAW/Arc. It inherits TM1 security and backup for free.
- **Result:** The `}Meta_*` cube-and-dimension schema became the heart of the design.

## A5. Two real-world stress tests (anonymised)
- **What:** Ran the design against two genuine production TIs — "Example Loader 1" (file-based
  account loader) and "Example Loader 2" (mapping-cube-driven weekly loader).
- **Why:** Synthetic examples never expose the messy patterns real code uses.
- **Result:** Test 1 surfaced 9 improvements; Test 2 surfaced 7 more (Gaps A–G), most importantly
  mapping-cube-driven target resolution. All findings additive; none forced a redesign.

## A6. Specification documents
- **What:** Produced a consolidated Phase 1 spec, revised to v1.1 after Test 2; later anonymised.
- **Why:** A locked, written spec means we stop re-deciding verbally and build against one reference.
- **Result:** `docs/phase1_spec.docx` (v1.1, anonymised) — the authoritative design reference.

---

# Part B — Environment & Toolchain Setup

## B1. Python version choice (3.13)
- **What:** Develop on Python 3.13, declare `requires-python = ">=3.10"` for shipping.
- **Why:** Works with TM1py and all tools; shipping compatible with 3.10+ means older client servers
  can still install. The venv makes switching trivial if needed.
- **Result:** Development on 3.13.2 confirmed working.

## B2. Plain Python + venv (not Anaconda)
- **What:** Chose plain Python with `venv`.
- **Why:** The extractor must run on locked-down client servers with plain Python and `pip`, not
  conda. Building in the same shape we deploy eliminates "works in conda, breaks on deploy" bugs.
- **Result:** A `.venv` per project; system Python untouched.

## B3. Understanding venv
- **What:** Established that a venv is a self-contained Python + isolated `site-packages` in `.venv/`,
  activated by prepending it to `PATH` for the current shell.
- **Why:** Isolation prevents dependency conflicts; reproducibility means anyone can recreate the
  environment from `pyproject.toml`.
- **Result:** Daily rhythm — activate, work.

## B4. VS Code as the IDE
- **What:** Standardised on VS Code, signed in with GitHub credentials.
- **Why:** Free, cross-platform, first-class Python + Git integration, auto-detects the venv.
- **Result:** Project opened with the `.venv` interpreter selected; terminals auto-activate.

## B5. Git from day one
- **What:** Initialised Git and a private GitHub repo.
- **Why:** Every known-good state recoverable; every change traceable; collaboration possible later.
- **Result:** Local repo on `main`, pushed to private GitHub.

## B6. Security incident — password rotation
- **What:** A credential was accidentally shared in chat; rotated the GitHub password, confirmed no
  rogue personal access tokens existed.
- **Why:** Any secret that touches a chat/log must be treated as compromised. Rotating is cheap.
- **Result:** Password changed, 2FA recommended, no tokens to revoke. Clean.

## B7. Full toolchain adopted
- **What:** Adopted `black`, `ruff`, `mypy`, `pytest`, `pytest-cov`, `pre-commit`, and GitHub Actions
  CI, all configured via `pyproject.toml`.
- **Why:** black ends style debates; ruff catches bugs fast; mypy catches type errors before runtime;
  pytest gives confidence to change parser rules; pre-commit makes the tools automatic; CI catches
  "works on my machine". One-time setup, compounding daily savings.
- **Result:** Toolchain configured and enforced on every commit.

---

# Part C — Project Scaffolding

## C1. src-layout project structure
- **What:** Created `src/tm1_data_dictionary/` with `parser/` and `writers/` subpackages, plus
  `scripts/`, `tests/`, `docs/`, `.github/workflows/`, `out/`.
- **Why:** The `src/` layout prevents import-order bugs and makes installation a one-liner.
  Separating `parser/` and `writers/` mirrors "read/parse" vs "write to TM1".
- **Result:** Clean, conventional structure.

## C2. Core project files (17 files)
- **What:** Created `.gitignore`, `.env.example`, `config.yaml.example`, `pyproject.toml`,
  `.pre-commit-config.yaml`, `LICENSE`, `README.md`, `CHANGELOG.md`, `ci.yml`, package `__init__.py`s,
  `cli.py`, test scaffolding, and `scripts/check_environment.py`.
- **Why:** `.gitignore` excludes `.env` so secrets never get committed. `pyproject.toml` is the single
  source of truth for metadata, deps, entry points, and tool config. Templates let others see config
  shape without secrets.
- **Result:** All 17 files in place.

## C3. Editable install
- **What:** Ran `pip install -e ".[dev]"`.
- **Why:** `-e` means source edits take effect immediately; `[dev]` pulls in the toolchain; registers
  the `tm1dd` CLI.
- **Result:** `Successfully installed tm1-data-dictionary-0.1.0`.

## C4. Config note — loading .env ourselves
- **What:** Load `.env` explicitly via `python-dotenv`, not VS Code injection.
- **Why:** Self-contained code works identically on laptop, server, and CI.
- **Result:** The VS Code env-injection prompt can be ignored.

---

# Part D — Environment Verification

## D1. Local PA connection details
- **What:** Populated `.env` with the local PA address, port, SSL, credentials, and log path.
- **Why:** A safe sandbox to build against (not a client instance).
- **Result:** `.env` complete and gitignored.

## D2. Sanity checks
- **What:** Verified package import, CLI registration, and a passing unit test.
- **Result:** All green.

## D3. Environment diagnostic — ALL GREEN
- **What:** Ran `scripts\check_environment.py`.
- **Why:** The gate before building — proves Python, config loading, TM1 connectivity, TM1 write
  permission, and log access.
- **Result:** Five PASS. Connected to the local PA (~5-7ms), read 467 processes and 187 cubes, scratch
  dimension write succeeded, 31.9 MB log readable. The "write test OK" line proves the full plumbing.

---

# Part E — Building the Extractor

## E1. Pushed to GitHub + CI live
- **What:** Linked to the private GitHub repo and pushed `main`.
- **Why:** Off-machine backup; activates GitHub Actions CI on every push.
- **Result:** Pushed; `main` tracks `origin/main`; `.env` confirmed absent from the remote.

## E2. Credential strategy decided (encrypted secrets, pluggable)
- **What:** Agreed credentials should not live only in plaintext `.env`; they should be retrievable
  from an OS keyring or, for enterprise clients, a vault — selected per environment.
- **Why:** Plaintext-on-disk is fine for local dev but a security audit at a client would flag it.
  Different deployment targets need different backends.
- **Decision:** Design for it now via a `CredentialProvider` abstraction; implement keyring shortly
  after the config backbone. Costs nothing now, avoids a painful refactor later.
- **Result:** `credentials.py` with a `CredentialProvider` interface and `EnvCredentialProvider`.

## E3. config.py — the configuration backbone (first real module)
- **What:** Built the config loader/validator plus a 10-case test suite.
- **Why:** Every downstream module needs one trustworthy, validated source of settings. Centralising
  means we fail fast with a clear message instead of crashing deep in the pipeline.
- **Design:** env-var indirection (Option A); minimal validation now; frozen dataclasses; secrets via
  the credential provider.
- **Toolchain note:** mypy caught an `int()` overload edge case, fixed by narrowing types and
  rejecting `bool` in `_as_int`.
- **Result:** `config.py` + `credentials.py` + `test_config.py`; 10/10 tests green.

## E4. Learning-as-we-build set up
- **What:** Established a learning track — explain-as-we-go notes, `LEARNING_LOG.md`, mini-exercises.
  Two exercises covered classes, `__init__`, `self`, polymorphism, indentation, YAGNI.
- **Why:** The owner is new to Python/Git/VS Code/TM1py and wants to learn while building.
- **Result:** `LEARNING_LOG.md` created (generic concepts only).

## E5. Keyring credential provider + set-credential command
- **What:** Added `KeyringCredentialProvider`, `ChainedCredentialProvider`, a keyring→env
  `default_provider()`, and a `tm1dd set-credential` command that stores the password in Windows
  Credential Manager.
- **Why:** Moves the TM1 password out of plaintext into encrypted OS-native storage. The chained
  fallback means it still works on servers/CI that rely on env vars, with no config change.
- **Design payoff:** because the password is fetched through the abstraction, `config.py` did not
  change one line — the factory now returns a chain instead of a bare provider.
- **Proven end-to-end:** stored the password via `tm1dd set-credential`, deleted `TM1_METADICT_PWD`
  from `.env`, updated `check_environment.py` to resolve the password via the provider, and re-ran the
  diagnostic — all five checks still green, now with **zero plaintext password on disk**.
- **Result:** `credentials.py` + `cli.py` updated, `test_credentials.py` (12 tests). All green.

## E6. tm1_client.py — the TM1 connection wrapper
- **What:** Built `TM1Client`, a thin wrapper around a TM1py `TM1Service`, plus a 12-case test suite.
  Every part of the extractor that talks to TM1 goes through this client.
- **Why:** Centralising the connection in one tested place removes duplicated `os.getenv` +
  `TM1Service(...)` logic and gives one place to enforce cross-cutting behaviour (dry-run, clean
  shutdown, error wrapping). The DRY principle.
- **Design choices:** built from `AppConfig`; context manager (always logs out on exit, even on
  error); dry-run guard via `ensure_writable()`; lazy TM1py import + injectable service for tests;
  `_owns_service` ownership tracking; uniform `TM1ClientError`.
- **Toolchain note:** ruff flagged an unused `TYPE_CHECKING` import and a `try/except/pass` that
  should be `contextlib.suppress`; both fixed before handover.
- **Result:** `tm1_client.py` + `test_tm1_client.py`; 12/12 tests green.

## E7. schema.py + bootstrap.py — first }Meta_* objects created in TM1
- **What:** Built `schema.py` (pure-data definitions of the `}Meta_Extraction_Audit` cube and its two
  dimensions) and `bootstrap.py` (idempotent creation of those objects via `TM1Client`), plus 9 + 6
  tests. Added a `tm1dd bootstrap` CLI command.
- **Why:** Proving the create path on the simplest cube de-risks the rest of the schema. Splitting
  "what" (`schema.py`) from "how" (`bootstrap.py`) keeps each piece small and testable
  (separation of concerns).
- **Design choices:** frozen dataclasses for the schema; idempotent `ensure_schema` (checks
  `exists()`, skips what's there, never deletes); dimensions created before cubes; dry-run guarded;
  lazy TM1py import; a `BootstrapResult` summarising created vs skipped.
- **Proven against real PA:** `tm1dd bootstrap` created `}Meta_ExtractionRun`, `}Meta_AuditMeasure`,
  and `}Meta_Extraction_Audit` in the local model; a second run reported "already present" — proving
  idempotency.
- **Result:** `schema.py` + `bootstrap.py` + tests; 100% coverage on both; all green.

## E8. audit_writer.py — the write path (data lands in TM1)
- **What:** Built `AuditWriter`, which writes one run record into `}Meta_Extraction_Audit` (adds a
  UTC-timestamp run element, then writes seven measure cells), plus an 8-case test suite. Added a
  `tm1dd record-run` CLI command.
- **Why:** Completes the round-trip — from "structure exists" to "real data written and readable in
  TM1". This is the module pattern all future writers follow.
- **Design choices:** injectable clock for deterministic tests; dry-run guarded; checks whether the
  run element exists before creating it (idempotent); builds the cellset as
  `{(run_id, measure): value}`.
- **Verify-against-docs lesson:** the first draft used TM1py method names from memory that were
  wrong. Before handover we searched the real TM1py docs and corrected to `elements.exists`/
  `elements.create` and `cells.write(cube_name=..., cellset_as_dict=...)`, then rewrote the tests to
  match. The tools also caught a missing `cli.py` import/constant and an `Any`-return mypy issue.
- **Result:** `audit_writer.py` + `test_audit_writer.py`; 98% coverage; 58 tests total, all green.
  Running `tm1dd record-run` writes a real run row visible in the `}Meta_Extraction_Audit` cube.

---

# Current State

- **Foundation + write path complete.** config → credentials (encrypted keyring, proven) → TM1 client
  (context-managed, dry-run-aware) → schema definitions → idempotent bootstrap → audit writer. 58
  tests, all green; ruff/black/mypy clean; everything committed and pushed.
- The tool can read config, authenticate securely, connect, create the `}Meta_*` schema, and write
  data into TM1 — all proven against the local PA.

## Next planned step: the parser

The parser is the heart of the tool. It will be built incrementally (not all at once), in dependency
order, each stage small, tested, and runnable:

1. `ti_reader.py` — read a TI process from TM1 and expose its parts (blocks, datasource, variables,
   parameters). Pure TM1py reading; the parser's input layer.
2. Writers for `}Meta_Process_Info`, `}Meta_Process_Datasource`, `}Meta_Process_Variable` — write the
   already-structured process facts (no parsing needed yet). First real metadata from a real process.
3. `blocks.py` — segment TI scripts into numbered lines/blocks (foundation for all parsing).
4. `references.py` — scan for the common target/read/chain functions; populate `}Meta_Process_Cube`
   and `}Meta_Process_Chain`. First genuine lineage.
5. `const_prop.py` + `correlation.py` — resolve variables, bind arguments, classify roles/tags;
   populate `}Meta_Variable_Mapping`.
6. `control_flow.py` — Level-2 IF/WHILE capture.
7. The remaining rollups, DimInspect, external calls, v1.1 gaps, chores, exclusions, and the
   orchestrator.

---

*End of journal (to be appended as we build).*
