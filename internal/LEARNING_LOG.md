# TM1 Data Dictionary — Learning Log

Everything you need to understand this project from scratch: what it is, how the pieces
fit, and every concept used to build it – Python, software engineering, TM1, and tooling –
each explained in plain English with a pointer to where it lives in our code.

Read **Part 0** first. It is the whole project on one page. After that, use the concept
index to look things up. The companion [BUILD_JOURNAL.md](BUILD_JOURNAL.md) records *what
was built and when*; this file records *how it works and why*.

**Owner:** Zameel Arif
**Covers:** the project through rules analysis Phase 2d.

---

## Contents

- [Part 0 — The project in one page](#part-0--the-project-in-one-page)
- [Part 1 — Python](#part-1--python)
- [Part 2 — Software engineering](#part-2--software-engineering)
- [Part 3 — TM1 and TM1py](#part-3--tm1-and-tm1py)
- [Part 4 — Tooling: Git, tests, packaging](#part-4--tooling-git-tests-packaging)
- [Part 5 — Playbooks](#part-5--playbooks)
- [Concept index](#concept-index)

---

## Part 0 — The project in one page

### What it does
`tm1dd` reads a TM1 model – its TI processes, chores and cube rules – **without running
anything**, works out how data and logic flow through it, and writes the answers into TM1
cubes named `}Meta_*`. You then explore the model in PAfE like any other cube.

### The two halves

```
                      config.yaml + OS keyring
                                │
                          tm1_client.py   (one connection, dry-run guard)
                 ┌──────────────┴───────────────┐
         tm1dd extract                    tm1dd extract-rules
         (extract.py)                     (extract_rules.py)
                 │                                 │
   exclusions.py → ti_reader.py      rule_exclusions.py → rule_reader.py
   blocks.py → const_prop.py         rule_text.py  (parse once)
   references.py  (parse once)          ├ rule_dependencies.py      (2b)
     ├ rollup.py          cube          ├ rule_element_references.py (2c)
     ├ chain_rollup.py    chain         │     └ element_index.py (lookup)
     ├ datasource_rollup  datasource    └ rule_functions.py          (2d)
     ├ dim_rollup.py      dimension
     ├ function_scan.py   functions
     └ chore_reader.py    chores (read, not parsed)
                 │                                 │
            writers/*.py                      writers/rule_*.py
                 └──────────────┬───────────────┘
                     }Meta_* cubes in TM1  +  }Meta_Extraction_Audit
```

### The life of one run (`tm1dd extract`)
1. **Load config** for the chosen `--env`; fetch the password from the keyring.
2. **Connect** with `TM1Client` (a context manager, so it always disconnects).
3. **List** all processes and **apply exclusions**, recording each reason.
4. **Clear** the target cubes once (unless dry-run).
5. For each included process, inside a `try`:
   read it → split into blocks and logical lines → resolve constant variables →
   extract references → roll up into cube, chain, datasource, dimension and function rows.
6. **Read chores** once.
7. **Write** each cube in one batch; flag `CubeExists`.
8. **Record the run** in the audit cube and print a summary.

`tm1dd extract-rules` follows the same shape with cubes and rule text instead of processes.

### Design rules that run through everything
- **Never guess.** If a target is an expression, record it as unresolved; never invent
  lineage. (Concepts 36, 74.)
- **Parse once, derive many.** One read of each object feeds every output. (53)
- **Isolate failures.** One bad object never stops the run. (42)
- **Report, don't hide.** Exclusions, failures, unresolved items and lookup problems are all
  counted and shown. (76)
- **Idempotent and dry-run safe.** Every command can be re-run; dry-run writes nothing. (61, 62)
- **Separate "what" from "how".** `schema.py` describes cubes as data; `bootstrap.py`
  creates them. (56)

### Where things live

| Folder / file | Role |
|---|---|
| `src/tm1_data_dictionary/cli.py` | Every `tm1dd` command |
| `config.py`, `credentials.py`, `tm1_client.py` | Settings, secrets, connection |
| `schema.py`, `bootstrap.py` | Cube definitions and creation |
| `exclusions.py`, `rule_exclusions.py` | What to skip |
| `extract.py`, `extract_rules.py` | The two orchestrators |
| `parser/` | TI parsing and roll-ups |
| `parser/rules/` | Rule parsing and analysis |
| `element_index.py` | Element and alias lookup for rules |
| `writers/` | One writer per `}Meta_` cube, plus the audit writer |
| `graph.py` | HTML data-flow map |
| `tests/unit/` | All tests (fakes, no live TM1 needed) |
| `docs/` | Public documentation (install, use, reference) |
| `internal/` | Private notes: this log, the build journal, the spec. Never published |
| `scripts/export_public.ps1` | Builds the clean public copy of the repository |

---

## Part 1 — Python

### 1. Virtual environments
**What:** a private folder holding one Python interpreter and its packages, so projects
don't interfere. **Example:** `py -3.13 -m venv .venv`, then `.\.venv\Scripts\Activate.ps1`;
the prompt shows `(.venv)`. **In our code:** every machine runs `tm1dd` from a venv.
**Gotcha:** a prompt without `(.venv)` means you are using the system Python – commands
like `python -m build` then fail with "No module named build".

### 2. Packages and the src layout
**What:** a package is a folder of modules with `__init__.py`. Putting it under `src/`
means tests import the *installed* copy, which catches packaging mistakes. **In our
code:** `src/tm1_data_dictionary/` with `parser/` and `writers/` subpackages.

### 3. Editable installs and console scripts
**What:** `pip install -e .` links the installed package to your source, so edits take
effect immediately. `[project.scripts]` in `pyproject.toml` turns a function into a
command. **In our code:** `tm1dd = "tm1_data_dictionary.cli:main"`.

### 4. Imports, and lazy imports
**What:** `import` loads a module once. A *lazy* import sits inside a function and only
runs when called. **In our code:** every writer has `_load_element_class()` that imports
`TM1py.Objects.Element` lazily, so tests can swap in a fake by putting a fake module in
`sys.modules`.

### 5. Type hints and `from __future__ import annotations`
**What:** hints such as `def f(x: str) -> int` document types and let mypy check them.
The future import lets you use newer hint syntax (`str | None`) on older Pythons.
**In our code:** every module starts with it.

### 6. Dataclasses
**What:** `@dataclass` writes `__init__`, equality and printing for you. `frozen=True`
makes instances read-only. **In our code:** `CubeRuleInfo`, `ElementReference`,
`DatasourceRow`, `AuditRecord`; rows that accumulate counts (e.g. `ElementReferenceRow`)
are *not* frozen because `count` is incremented.

### 7. Enums
**What:** a fixed set of named values. `class Role(str, Enum)` makes each value also a
string, so `Role.CUBE_WRITE.value == "CubeWrite"`. **In our code:** `Role`
(references), `DependencyType`, `ReferenceType` – their values become TM1 element names.

### 8. Protocols and abstract base classes
**What:** a contract saying "anything with these methods will do". A `Protocol` is checked
by shape (duck typing); an ABC by inheritance. **In our code:** the credential provider
abstraction; `ElementLookup` in `rule_element_references.py` – the roll-up accepts the real
`ElementIndex` or a simple test fake.

### 9. Context managers (`with`)
**What:** guarantees setup and clean-up, even on error. **In our code:**
`with TM1Client(cfg) as client:` always logs out of TM1.

### 10. Dependency injection
**What:** pass collaborators in rather than creating them inside. Makes code testable.
**In our code:** `TM1Client(cfg, service=fake)`; `extract_all_rules(client,
element_index=...)`; `ElementIndex(loader, resolver)`; `AuditWriter(client, clock=...)` –
a fixed clock makes timing tests exact.

### 11. Exceptions: custom errors, `raise ... from`, fail fast
**What:** raise early with a clear message; wrap low-level errors with context, keeping the
original (`raise RuntimeError(...) from exc`). **In our code:** `ConfigError`,
`TM1ClientError`; the audit writer names the run that failed to write.

### 12. Catching broadly, on purpose
**What:** `except Exception` is normally a smell, but right where you *want* "one failure
must not stop the rest", it is correct – as long as the failure is recorded. **In our
code:** per-process and per-cube loops; per-dimension and per-alias reads. Each carries a
`# noqa: BLE001` comment saying why.

### 13. Functions as values: callbacks
**What:** functions can be passed around. **In our code:** the `progress` callback prints
`[  4/16] Employee ...` without the orchestrator knowing about the screen.

### 14. Dictionaries as indexes; `setdefault`; normalised keys
**What:** a dict gives instant lookup. `d.setdefault(k, v)` sets only if missing.
Normalising keys (lower case, no spaces) makes lookups match TM1's rules. **In our code:**
`_normalise()` everywhere; alias values are added with `setdefault` so a principal name
always wins over another element's alias.

### 15. Comprehensions
**What:** `[x.name for x in items if x.ok]` builds a list in one line. **In our code:**
summaries such as `{row.cube for row in rows if row.category == CAT_HIERARCHY}`.

### 16. Regular expressions and lookbehind
**What:** patterns for finding text. `(?<![A-Za-z0-9_])DB\s*\(` means "DB followed by `(`,
not preceded by a letter, digit or underscore" – so `XDB(` and `ATTRS(` don't match.
**In our code:** `references.py`, `rule_dependencies.py`, `rule_functions.py` (which also
excludes `!` so `!Dim(` is not a function call).

### 17. String-aware scanning (a small state machine)
**What:** walk text one character at a time, tracking "am I inside a string?". Only then
are `#`, `;`, `[` and `(` meaningful. TM1 escapes a quote by doubling it (`''`).
**In our code:** `blocks.py`, `rule_text.split_statements`, `_string_mask()` (marks which
characters are inside strings so regex matches there can be ignored).

### 18. Balanced-bracket parsing
**What:** to find a call's arguments, count depth: `(` +1, `)` −1; the call ends at depth
0. Split arguments only on commas at depth 0. **In our code:** `_extract_arg_string` and
`_split_top_level_args` in `references.py`, reused by the rules parser.

### 19. Fixpoint resolution with a cycle guard
**What:** repeat a substitution until nothing changes, stopping if a value loops back to
itself. **In our code:** `const_prop.py` follows `a = b; b = 'Sales'` chains.

### 20. Paths and files
**What:** `pathlib.Path` handles paths on any OS. **In our code:** `load_watchlist()` reads
`functions.txt`, ignoring blanks and `#` comments; a missing file means "no watch list".

### 21. Building a CLI with click
**What:** decorators turn functions into commands and options. Shared options are reused
by wrapping (`_env_option`). `click.ClickException` prints a clean error without a
traceback. **In our code:** `cli.py`.

### 22. Dates, times and UTC
**What:** store times in UTC with a timezone. A naive datetime has no timezone; we treat it
as UTC. **In our code:** run ids like `2026-10-01T09:15:00Z`.
**Gotcha:** `from datetime import UTC` only exists in Python 3.11+. Use
`timezone.utc` if 3.10 must be supported.

### 23. URL encoding
**What:** names in a URL must be encoded (`'` → `%27`, space → `%20`); OData also doubles a
quote inside a quoted name. **In our code:** the one-call alias read in `element_index.py`.

---

## Part 2 — Software engineering

### 31. Static versus dynamic analysis
Reading code (static) versus watching it run (dynamic). Static covers everything fast and
safely but cannot know run-time values. The whole parser is static.

### 32. Anti-corruption layer
Wrap an external library in your own small types so the rest of the code doesn't depend on
its object model. `ti_reader.py`, `rule_reader.py` (`CubeRuleInfo`), `chore_reader.py`.

### 33. Correctness over coverage
Better to say "unknown" than to be wrong. A variable assigned twice or inside `IF` is not
resolved (`const_prop.py`); ambiguous elements are not guessed (2c).

### 34. Capture facts, defer judgement
Record everything raw first, decide later. `assignments.py` stores every assignment;
`show-vars` lets a person judge.

### 35. Lookup tables encode domain knowledge
A table beats a chain of `if`s. `TARGET_ARG_INDEX` (which argument is the cube),
`FUNCTIONS` (function → role), the rule function catalogue (function → category).

### 36. Never guess (the dynamic-value rule)
If a target is an expression – `pCubeName`, `DB(IF(...))`, `!Version`, `ATTRS(...)` – it is
recorded as unresolved or skipped, never turned into lineage.

### 37. Rules with precedence
When rules can conflict, fix the order. Exclusions: explicit include → explicit exclude →
patterns → substrings → include by default.

### 38. Error isolation
Each item runs in its own `try`; failures are counted and listed; the loop continues.

### 39. Batching
Collect all rows, then write once per cube – far faster than a REST call per cell.

### 40. Orchestration
A thin coordinator composes small, separately tested pieces. `extract.py`,
`extract_rules.py`.

### 41. Scope before depth
Before improving the parser, stop parsing what doesn't matter. Excluding `}*` cut
unresolved references 97% with no parser change.

### 42. Measure before you optimise
`diagnose-unresolved` showed 77% of unresolved targets were parameters – correct as they
were. Without measuring, effort would have gone into the wrong fix.

### 43. Grouping and frequency counting
Group by a key, count, sort. `diagnostics.py`, every roll-up (`Count`, `FirstLine`).

### 44. The limits of static analysis
A parameter's value is set by the caller at run time. It can only be resolved by following
the caller (a backlog item), not by reading the called process.

### 45. Good data models make new questions cheap
Storing occurrences (not just counts) made `--expression` a few lines of code.

### 46. Logical versus physical lines
A statement may span lines; join them (bracket-aware) before analysing, keeping the
first line number. `blocks.code_lines`.

### 47. Fix at the root layer
Blank targets showed up in `references.py`, but the cause was in `blocks.py`. Fixing the
root fixed every consumer.

### 48. Parse once, derive many
Each process or rule text is parsed once; every roll-up uses the result.

### 49. Define versus create
A new cube needs a definition in `schema.py` **and** wiring into `bootstrap` in `cli.py`.
Miss the second and the writer gets a 404.

### 50. Don't over-defend
Guards for impossible cases add noise and muddy types. Trust a function's contract.

### 51. Read once versus per item
Instance-level data (chores, the list of all cubes) is read once, outside the per-item
loop.

### 52. Metadata before parsing
Before writing a parser, ask whether the information is already stored as structured
metadata. Chores, cube dimensions and rule pragmas are read, not parsed.

### 53. A category belongs in a dimension, not a string measure
If people will filter or group by it, make it a dimension. `}Meta_SourceType`,
`}Meta_RuleDependencyType`, `}Meta_RuleElementRefType`.

### 54. Placeholders instead of guesses
When a value can't be determined, use an explicit placeholder: `(Unknown)`,
`(Ambiguous)`, `(blank)`.

### 55. Three-valued answers
Yes / No is not enough when a check can fail. `ElementExists` has `Unknown` so "couldn't
check" is never shown as "missing".

### 56. Separate "what" from "how"
`schema.py` is pure data, no TM1 calls – easy to read and test. `bootstrap.py` does the
creating.

### 57. Self-healing schemas
Before writing, create any missing measure elements. Older environments keep working after
a release adds measures. The audit writer does this for every metric.

### 58. Open-ended metrics
Don't hard-code a list that callers can outgrow. Audit metrics map any snake_case key to a
measure; a fixed list silently dropped new metrics for weeks.

### 59. Validate before side effects
Check inputs before touching TM1, so a bad value never half-writes a run. The audit writer
builds and validates all cells first.

### 60. Full clear-and-reload
Clear a cube, then write everything. Simpler than updating, and stale rows can't survive.

### 61. Idempotency
Running twice gives the same result. `bootstrap` skips existing objects; writers create
elements only if missing.

### 62. Dry-run
A mode that does all the work except writing. `client.dry_run` checks plus
`ensure_writable()` as a hard guard.

### 63. Separate commands for separate concerns
TI and rules are different commands so one can fail or be scheduled without the other.

### 64. Phasing by value against complexity
Rules phases run cheapest-and-most-useful first (facts → dependencies → elements →
functions) and the hardest (feeder behaviour) last.

### 65. Layered fallbacks
Try the cheap method, then a more robust one, then the authoritative one. Element lookup:
bulk names → one-call alias read → per-alias reads → ask TM1.

### 66. Caching and budgets
Cache every answer (each dimension read once; each TM1 lookup once). Cap the expensive
step (500 TM1 lookups) and say when the cap was hit.

### 67. Two-pass search
Search every dimension cheaply first; only if nothing matches, use the expensive check.
Stops one hit in dimension 7 costing queries in dimensions 1–6.

### 68. Report, don't hide
A silent fallback hid the alias failure. Adding *Aliases not readable* to the summary
showed the exact cause in one run.

### 69. Triage: false positive or real defect?
When many results fail the same way (17 × `Actual`/`Budget`), suspect the tool. When one
fails alone (`Freight`), suspect the model. Check before "fixing" either.

### 70. Keep a known defect as a regression check
The Freight feeder bug was left in the dev model so every run proves the tool still finds
it.

### 71. Escape untrusted text, or avoid building queries from it
Element names can contain anything. TM1py built MDX from names without escaping; we read
via REST `$select` instead, and escape `]` as `]]` where MDX is unavoidable.

### 72. Configuration as data, secrets elsewhere
Settings go in `config.yaml` (literal values or variable names, `defaults` + per-environment
overrides). Passwords go in the OS keyring, named by `password_env`.

### 73. Confidentiality by design
No client names or statistics in outputs, docs or examples; no passwords anywhere. The
build journal uses anonymised examples.

### 74. Never invent lineage
Extension of 36 to rules: `DB()` with an expression for a cube is counted, not written;
`!Dim` and `ATTRS(...)` arguments are skipped.

### 75. Count, don't drop
Malformed rule statements are counted and still scanned for `DB()` and functions.

### 77. Names that sort into groups
In PAfE and Architect, cubes are listed alphabetically. Giving every TI cube the prefix
`}Meta_Process_` and every rules cube `}Meta_Rule_` makes each group appear together.
Renaming a cube means: change the name in `schema.py`, keep the old name in `LEGACY_CUBES`,
and give users a clean-up path (`bootstrap --drop-legacy`).

### 78. Public versus private repositories
Git keeps every past version of every file. Moving a file out of a folder, or deleting it,
does not remove it from history – anyone who clones a public repository can read old
commits. So private notes are kept in `internal/` in the private repository, and the
public repository is created **fresh** from an allow-listed export with no history
(`scripts/export_public.ps1`), after a scan for private terms.

### 76. Summaries are part of the product
A run summary that shows exclusions, failures, unresolved and lookup health lets the user
trust (or question) the numbers.

---

## Part 3 — TM1 and TM1py

### 81. Control objects and the `}` prefix
Objects whose names start with `}` are control objects: hidden unless *Display Control
Objects* is on, and handled specially by TM1 (which is why some `}` elements could not be
deleted). Our cubes use `}Meta_` so they sit with them.

### 82. Designing a `}Meta_` cube
Name dimensions first, measure dimension last. Measures are string (`S`) or numeric (`N`)
elements. A row is a combination of name elements; measures hold the facts.

### 83. Shared dimensions
One dimension used by several cubes lets a view pivot across them. `}Meta_Cube` (all rules
cubes and `}Meta_Process_Cube`), `}Meta_Dimension`, `}Meta_Function`, `}Meta_Process`.

### 84. Same dimension on two axes
TM1 needs distinct dimension names, so relating processes to processes uses
`}Meta_Process` and `}Meta_Process_Callee`; cubes to cubes uses `}Meta_Cube` and
`}Meta_Rule_RelatedCube`.

### 85. `Count = 1` and zero suppression
Rows that only hold strings vanish under zero suppression. A numeric `Count` keeps them
visible.

### 86. Principal names and aliases
Every element has one principal name and may have alias values. TM1 accepts either, in any
case, ignoring spaces. A rule writing `'Actual'` is valid if `Actual` is an alias of
element `1`. Our index files rows under the principal name and keeps `WrittenAs`.

### 87. Hierarchies
A dimension can have several hierarchies; the default one has the dimension's name. We read
only the default (a known limit).

### 88. TI process structure
Prolog (once, before data), Metadata (per record, for dimensions), Data (per record, for
cells), Epilog (once, after). Parameters (`pCube`) are set by the caller.

### 89. Lineage functions in TI
Writes: `CellPutN/S`, `CellIncrementN`. Reads: `CellGetN/S`, `DB`, `DBRW`. Chains:
`ExecuteProcess`, `RunProcess`. Dimensions: `DimensionElementInsert`,
`HierarchyElementInsert`, `...ComponentAdd`. Attributes: `AttrPutS/N`.

### 90. Datasources and chores
A process may read a file, ODBC query or cube view. A chore runs processes in order on a
schedule (`Frequency` like `P1DT0H0M0S` = every day).

### 91. Rule anatomy
```
SKIPCHECK;                                  pragma
['Local','Salaries'] = N: DB('Employee', !Version, 'Total');
 └ area ─────────────┘   └ qualifier, expression ───────┘
FEEDERS;
['Local','Salaries'] => ['Payroll Taxes'];
 └ source area ─────┘    └ target ─────┘
```
`N:` leaf cells, `C:` consolidations, `S:` strings. `!Dim` means "the current element of
Dim". `STET` keeps the cell as input; `CONTINUE` passes to the next matching rule.

### 92. `DB()` arguments are positional
`DB('Cube', e1, e2, ...)` – each argument after the cube is an element of the cube's
dimension in that position. That is how 2c maps a literal to a dimension.

### 93. Pragmas
`SKIPCHECK` – only calculate fed cells (fast, but needs feeders). `FEEDSTRINGS` – allow
string cells to be fed. `UNDEFVALS` – return undefined instead of zero.

### 94. Feeders and why a bad feeder matters
With SKIPCHECK, a rule-calculated cell is only shown if it is *fed*. A feeder whose target
element does not exist feeds nothing; values can silently vanish when other feeders
change. That is what the Freight finding is.

### 95. The TM1 REST API and OData
TM1 exposes everything over REST. OData options like `$select=Name,Attributes` choose
fields. TM1py wraps this; its connection is at `service.elements._rest`.

### 96. MDX member sets
`{[Dim].[Dim].[Element]}` asks TM1 for one member; TM1 resolves aliases. An error means
"not found". `]` in a name is escaped as `]]`.

### 97. TM1py services used
`processes` (get, names), `cubes` (names incl. control, dimensions, rules), `elements`
(names, aliases, attributes, exists, create, MDX), `cells` (write, clear), `chores`
(get_all), `dimensions` (exists).

---

## Part 4 — Tooling: Git, tests, packaging

### 101. Git basics
`git status`, `git add -A`, `git commit -m "..."`, `git push`, `git pull`. Commit small,
working steps.

### 102. `.gitignore`
Lists files Git should never track: `.venv/`, `.env`, generated output (`data_flow.html`),
third-party downloads (`vis-network.min.js`). `git rm --cached <file>` stops tracking a file
already committed.

### 103. pre-commit
Runs checks before each commit: whitespace, large files, black, ruff, mypy. A failed hook
blocks the commit until fixed.

### 104. black, ruff, mypy
black formats; ruff lints (unused imports, import order, simplifications); mypy checks
types. Common mypy fix: annotate wider (`dict[str, object]`) or don't reuse one variable
for different types.

### 105. pytest basics
Files `test_*.py`, functions `test_*`, plain `assert`. `pytest -q` runs everything quietly.

### 106. Fixtures and monkeypatch
A fixture prepares something for a test. `monkeypatch.setattr` swaps a function for the
test only; `monkeypatch.setitem(sys.modules, ...)` fakes a whole module (how we fake TM1py).

### 107. Fakes instead of a live server
Small classes with the same methods as TM1py services (`_FakeElements`, `_FakeCells`)
record what was called. Every test runs offline in under a second.

### 108. Testing with real data
Tests use real rule text from the dev model (General Ledger, Employee, Retail) and the real
Version dimension shape, so they prove the cases that actually occurred.

### 109. Coverage as a signal
A module far below its neighbours (0–29%) means missing tests, a test file not saved, or
dead code – not just a low score.

### 110. Tests drift
Code that changes without its tests re-run leaves stale tests. Rule: run `pytest -q` before
every dev run, and update tests in the same change as the code.

### 111. Where test files live
All tests in `tests/unit/`. A test file inside `src/` gets packaged into the wheel.

### 112. Editor buffer versus disk
What the editor shows may not be saved. Python, Git and `tm1dd` read the disk. Check with
`Get-Content <file> | Select-String "<text>"`.

### 113. Building a wheel
`python -m build` creates `dist/*.whl`. The version comes from `pyproject.toml`; keep
`__init__.py`'s `__version__` in step so `tm1dd --version` is accurate.

### 114. Offline installation
`pip download` collects wheels; `pip install --no-index --find-links=offline <pkg>`
installs without internet. Every file must be a wheel; convert `.tar.gz` with `pip wheel`.

### 115. Keyring
The OS's secure store (Credential Manager on Windows), per Windows user. A scheduled task
must run as the user who stored the secret.

---

## Part 5 — Playbooks

### Add a new extraction (a new `}Meta_` cube)
1. **Parser / reader:** a module that turns source text or metadata into small
   dataclasses, plus a roll-up into one row per key.
2. **Schema:** names, measure tuple and a `*_schema()` function in `schema.py`.
3. **Bootstrap:** add the schema to the list in `cli.py` `bootstrap`.
4. **Writer:** `clear_*` and `write_*` in `writers/`, copying an existing writer.
5. **Orchestrator:** clear, collect, write, add summary fields and lines.
6. **CLI audit metrics:** add the new counts to the `metrics` dict.
7. **Tests:** parser, writer, and orchestrator updates, in `tests/unit/`.
8. **Docs:** the relevant guide and the schema reference.
9. `pytest -q` → `tm1dd bootstrap --env dev` → run → check summary → commit.

### Investigate an unexpected result
1. Is it many results failing the same way? Suspect the tool (69).
2. Read the summary's health lines (alias errors, TM1 lookups, failures).
3. Narrow to one object: `extract-refs`, `show-vars`, or filter the cube in PAfE.
4. Reproduce it in a unit test with the real text, fix, keep the test.

### Release to a server
Bump version in both places → `pytest -q` → `python -m build` → copy wheel to `offline\` →
on the server, `pip install --no-index --find-links=.\offline --upgrade
tm1_data_dictionary` → `tm1dd --version` → `tm1dd bootstrap --env <name>`.

---

## Concept index

| # | Concept | Part |
|---|---|---|
| 1–23 | Python: venv, packaging, imports, types, dataclasses, enums, protocols, context managers, dependency injection, exceptions, callbacks, dicts, regex, string scanning, bracket parsing, fixpoint, paths, click, UTC, URL encoding | 1 |
| 31–50 | Engineering foundations: static analysis, anti-corruption layer, correctness over coverage, never guess, precedence, isolation, batching, orchestration, scope before depth, measure first, logical lines, root fixes, parse once, define vs create | 2 |
| 51–78 | Engineering, later phases: read once, metadata first, dimensions for categories, placeholders, three-valued answers, self-healing schemas, open-ended metrics, idempotency, dry-run, layered fallbacks, caching, two-pass search, report don't hide, triage, regression defects, escaping, config vs secrets, confidentiality, grouped names, public vs private repos | 2 |
| 81–97 | TM1: control objects, cube design, shared dimensions, aliases, hierarchies, TI structure, rules, `DB()`, pragmas, feeders, REST, MDX, TM1py services | 3 |
| 101–115 | Tooling: Git, pre-commit, linters, pytest, fakes, coverage, test drift, editor vs disk, wheels, offline install, keyring | 4 |
| – | Playbooks: new extraction, investigation, release | 5 |

*Append new concepts in the right part, with the next free number.*
