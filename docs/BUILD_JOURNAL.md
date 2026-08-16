# TM1 Data Dictionary — Build Journal

> A running log of every step in building the TM1 Data Dictionary (Phase 1), with the
> reasoning behind each decision. Newest entries are appended at the bottom. This document
> lives in `docs/` so it is version-controlled alongside the code and grows as the project does.

**Project owner:** Zameel Arif
**Started:** July 2026
**Target:** IBM Planning Analytics / TM1 on-premises (v11.x)
**Repo:** `github.com/zameelarif/tm1-data-dictionary`

---

## How to read this journal

Each entry follows the same shape so it stays scannable:

- **What** — the action we took.
- **Why** — the reasoning (this is the important part — the "why" is what you forget later).
- **How** — the concrete commands or files involved.
- **Result** — what we observed / the state we ended in.

---

# Part A — Design Phase (pre-code)

Before writing a line of code, we spent several sessions designing the solution. This
matters because a data-dictionary/lineage tool is easy to start and hard to get right —
the cost of a wrong data model is enormous once code depends on it.

## A1. Problem definition

- **What:** Defined the core problem — TM1 developers can't easily answer "which TI updates
  this cube / loads this file / updates this element? what's the transformation logic? what's
  the source and what does it write?"
- **Why:** These questions are asked constantly during maintenance, impact analysis, and
  handovers, and answering them today means manually reading TI code. A queryable dictionary
  turns hours of code-reading into a single slice in PAfE.
- **Result:** Agreed to build a lineage + metadata catalogue for the TM1 model.

## A2. Approach selection (static vs runtime vs hybrid)

- **What:** Evaluated three approaches — static TI parsing, runtime log observation, and a
  hybrid of both.
- **Why:** Static parsing captures *intent* and works offline, but misses dynamically-built
  object names. Runtime log mining catches what actually ran but misses dormant processes and
  is noisy. The hybrid gives the completeness of both.
- **Result:** Chose the **hybrid** approach — static parser as the backbone, log augmentation
  for runtime facts (last-run, duration, rows).

## A3. Portability-first, no LLM in Phase 1

- **What:** Constrained Phase 1 to TM1 on-prem only, no cloud/v12, and no LLM dependency.
- **Why:** Corporate TM1 environments (Coles, Patties, DTFS, MBFS) rarely allow outbound calls
  to public LLM endpoints, and portability is what lets us drop the tool onto any client site
  in an afternoon. Dropping the LLM actually *improves* deployability by removing the last
  external dependency. Deterministic tags and template summaries recover most of the readability.
- **Result:** Phase 1 is pure Python + TM1py, deployable to any on-prem instance with zero
  external services.

## A4. Store results inside TM1 itself

- **What:** Decided the dictionary is written into native TM1 cubes and dimensions (`}Meta_*`),
  not an external database.
- **Why:** Makes the dictionary part of the model — copy the `}Meta_*` objects across, re-run
  the extractor, done. Developers slice it in PAfE/PAW/Arc with tools they already have. It
  inherits TM1 security and backup for free. A future LLM phase just reads/writes these same cubes.
- **Result:** The `}Meta_*` cube-and-dimension schema became the heart of the design.

## A5. Two real-world stress tests

- **What:** Ran the design against two genuine Coles production TIs — `Merch_PL BIW Actual Load`
  (Test #1) and `Food_Weekly_Sales DW_CatLocation_File_Load` (Test #2).
- **Why:** Synthetic examples never expose the messy patterns real code uses. Testing the *design*
  against real TIs before writing code is far cheaper than discovering gaps mid-build.
- **Result:**
  - Test #1 surfaced 9 improvements (const propagation, DimInspect, ExecuteCommand capture,
    Level-2 control flow, etc.).
  - Test #2 surfaced 7 more (Gaps A–G), most importantly **mapping-cube-driven target resolution**
    — the dominant Coles pattern where a write target is looked up from a mapping cube at runtime.
  - All findings were additive; none forced a redesign.

## A6. Specification documents

- **What:** Produced a consolidated Phase 1 specification, then revised it to v1.1 after Test #2.
- **Why:** A locked, written spec means we stop re-deciding things verbally and can build against
  a single reference. Versioning it (v1.0 -> v1.1) keeps a clear audit trail of what changed and why.
- **Result:** `docs/phase1_spec.docx` (v1.1) — the authoritative design reference.

---

# Part B — Environment & Toolchain Setup

With the design locked, we set up a professional Python project environment. The theme
throughout: **build in the same shape we will ship in**, and **make good habits automatic**.

## B1. Python version choice (3.13 vs 3.12)

- **What:** Chose to develop on Python 3.13 (already installed), while declaring
  `requires-python = ">=3.10"` for shipping.
- **Why:** 3.13 works with TM1py and all our tools. Developing on 3.13 but shipping compatible
  with 3.10+ means client servers on older Pythons can still install our package. The venv makes
  switching versions trivial if we ever hit a library issue.
- **Result:** Development on 3.13.2 confirmed working.

## B2. Plain Python + venv (not Anaconda)

- **What:** Chose plain Python with `venv` over Anaconda for this project.
- **Why:** The extractor must run on locked-down client servers that will have plain Python and
  `pip`, not conda. Developing in the same environment we deploy to eliminates a class of
  "works in conda, breaks on deploy" bugs. venv is lightweight, universal, and per-project isolated.
- **Result:** A `.venv` folder created per-project; system Python untouched.

## B3. Understanding venv (why it matters)

- **What:** Established that a virtual environment is a self-contained Python + isolated
  `site-packages` living in `.venv/`, activated by prepending it to `PATH` for the current shell.
- **Why:** Isolation prevents dependency conflicts between projects; reproducibility means any
  developer (or a client server) can recreate the exact environment from `pyproject.toml`;
  version pinning holds because the venv only has what we deliberately installed.
- **Result:** Daily rhythm established — `cd project` -> `.venv\Scripts\Activate.ps1` -> work.

## B4. VS Code as the IDE

- **What:** Standardised on VS Code, signed in with GitHub credentials.
- **Why:** Free, cross-platform, first-class Python + Git integration, auto-detects the venv, and
  is widely used across Cubewise. GitHub sign-in means pushes work without manual token handling.
- **Result:** Project opened in VS Code with the `.venv` interpreter selected.

## B5. Git from day one

- **What:** Initialised a Git repository and a private GitHub repo `tm1-data-dictionary`.
- **Why:** Version control from the first file means every known-good state is recoverable, every
  change is traceable, and collaboration/onboarding is possible later. Committing at each working
  milestone is a safety net.
- **Result:** Local repo on `main`, ready to push to GitHub.

## B6. Security incident — password rotation

- **What:** A credential was accidentally shared in chat; rotated the GitHub password and confirmed
  no rogue personal access tokens existed.
- **Why:** Any secret that touches a chat/log must be treated as compromised. Rotating immediately
  is cheap; a leaked credential at a security-conscious client is not. The habit matters more than
  the specific incident.
- **Result:** Password changed, 2FA recommended, no tokens to revoke. Clean.

## B7. Full toolchain adopted

- **What:** Adopted `black`, `ruff`, `mypy`, `pytest`, `pytest-cov`, `pre-commit`, and GitHub
  Actions CI, all configured via `pyproject.toml`.
- **Why (per tool):**
  - **black** — auto-formats code; ends all style debates and keeps diffs clean.
  - **ruff** — fast linter; catches bugs (unused vars, undefined names) instantly. Highest
    value-to-cost ratio in the toolchain.
  - **mypy** — static type checking; catches type mismatches before runtime, important in a
    parser project with many internal dataclasses.
  - **pytest** — automated tests; the thing that lets us change parser rules without fear.
    Non-negotiable for a parser-heavy project.
  - **pytest-cov** — shows which code paths are tested; free upside.
  - **pre-commit** — runs the above automatically on every commit, so the tools are actually
    used and not forgotten.
  - **GitHub Actions CI** — runs lint + tests on every push; catches "works on my machine" bugs.
- **Why (overall):** Setup is a one-time hour; the savings compound daily. For a tool that will
  ship to Cubewise clients, this is table stakes, not overhead.
- **Result:** Toolchain configured and ready.

---

# Part C — Project Scaffolding

## C1. src-layout project structure

- **What:** Created a `src/tm1_data_dictionary/` package layout with `parser/` and `writers/`
  subpackages, plus `scripts/`, `tests/`, `docs/`, `.github/workflows/`, and `out/`.
- **Why:** The `src/` layout (package under `src/` rather than repo root) is the current Python
  packaging best practice — it prevents a class of import-order bugs and makes proper installation
  a one-liner. Separating `parser/` and `writers/` mirrors the design's separation of "read/parse"
  from "write to TM1".
- **Result:** Clean, conventional structure ready for modules.

## C2. Core project files created (17 files)

- **What:** Created all scaffolding files:
  - **Root:** `.gitignore`, `.env.example`, `config.yaml.example`, `pyproject.toml`,
    `.pre-commit-config.yaml`, `LICENSE`, `README.md`, `CHANGELOG.md`.
  - **CI:** `.github/workflows/ci.yml`.
  - **Package:** `src/tm1_data_dictionary/__init__.py`, `cli.py`, and the two subpackage `__init__.py`s.
  - **Tests:** `tests/__init__.py`, `conftest.py`, `unit/__init__.py`, `unit/test_version.py`,
    `integration/__init__.py`.
  - **Scripts:** `scripts/check_environment.py`.
- **Why (key files):**
  - `.gitignore` — critically, excludes `.env` so secrets never get committed.
  - `pyproject.toml` — single source of truth for package metadata, dependencies, entry points
    (the `tm1dd` command), and all tool configuration.
  - `.env.example` / `config.yaml.example` — committed templates so others can see the shape of
    the config without seeing our secrets.
  - `check_environment.py` — a diagnostic that proves the environment before we build on it.
- **Result:** All 17 files in place, verified via the VS Code file tree.

## C3. Editable install

- **What:** Ran `pip install -e ".[dev]"`.
- **Why:** The `-e` (editable) flag means source edits take effect immediately without reinstalling.
  `[dev]` pulls in the full toolchain. This one command wires up dependencies and registers the
  `tm1dd` CLI command.
- **Result:** `Successfully installed tm1-data-dictionary-0.1.0`. TM1py and all dev tools present.

## C4. Config note — loading .env ourselves

- **What:** Chose to load `.env` explicitly in our code via `python-dotenv` (`load_dotenv(...)`),
  rather than relying on VS Code's env injection.
- **Why:** Self-contained code works identically everywhere — your laptop, a colleague's machine,
  a client server with no VS Code, and CI. This is the same portability principle as the whole design.
- **Result:** The VS Code "enable env injection" prompt can be safely ignored/dismissed.

---

# Part D — Environment Verification

## D1. Filled in real local PA connection details

- **What:** Populated `.env` with the local PA instance's address, port, SSL flag, credentials,
  and `tm1server.log` path.
- **Why:** The diagnostic and the extractor need to reach a real running TM1 instance. Using a
  local PA (not a client prod instance) is the right sandbox for building — safe to experiment,
  even if not big enough for stress testing.
- **Result:** `.env` complete; `.env` confirmed gitignored so credentials stay local.

## D2. Sanity checks

- **What:** Verified the package imports (`0.1.0`), the CLI registered (`tm1dd --version`), and the
  unit test passed (`pytest tests/unit`).
- **Why:** Confirms the install actually worked end-to-end before relying on it.
- **Result:** All three green. 1 test passed. (Low coverage % expected — barely any code yet.)

## D3. Environment diagnostic — ALL GREEN

- **What:** Ran `python scripts\check_environment.py`.
- **Why:** This is the gate before building. It proves the five things every downstream module
  depends on: correct Python, config loading, TM1 connectivity, TM1 write permission, and log access.
- **Result — all five PASS:**
  - Python version — 3.13.2
  - Config files — `config.yaml` + `.env` loaded
  - TM1 connection — "Cubewise Day" v11.8.02200.2, 5ms round trip
  - TM1 permissions — read 467 processes, 187 cubes; **scratch dimension write succeeded**
  - Log file access — 31.6 MB `tm1server.log` readable
- **Significance:** The "write test OK" line is the most important — it proves the full plumbing
  (config -> connect -> create schema -> write -> verify) works. Everything else builds on this.

---

# Current State (as of latest entry)

- Foundation complete and verified against a real local PA instance (467 processes to build against).
- All scaffolding installed, tests passing, environment all-green.
- **Next planned step:** write `config.py` — a validated config loader that becomes the single
  source of connection + rules for every downstream module. Rationale: fail fast with clear errors
  instead of cryptic crashes deep in the pipeline, and give every module one clean config object
  to depend on.

## Planned build sequence (for context)

1. `config.py` — config loader + validation. *(next)*
2. `tm1_client.py` — TM1py wrapper (connect, retry, throttle, dry-run switch).
3. `models.py` — dataclasses that flow through the pipeline.
4. `bootstrap.py` (first cube: `}Meta_Extraction_Audit`) — proves the write path end-to-end.
5. Widen to more cubes, then the parser.

Each step follows the same rhythm: **small module -> test -> commit -> repeat.**

---

*End of journal (to be appended as we build).*
