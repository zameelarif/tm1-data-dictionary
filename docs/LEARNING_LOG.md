# TM1 Data Dictionary — Learning Log

> A personal reference of the **concepts** we cover while building the tool — Python, Git,
> VS Code, and TM1py — each explained in plain English with a pointer to where it appears in
> our own code. Grows as we build. Lives in `docs/` so it travels with the project.
>
> Companion to `BUILD_JOURNAL.md` (which records *what* we built and *why*). This file records
> *what you learned*.

**Owner:** Zameel Arif
**Status:** current through the whole-model orchestrator (parser + rollup + cube writer +
exclusions + `tm1dd extract`).

---

## How to use this log

- Skim the **Concept Index** to find a topic fast.
- Each concept has: a plain-English definition, a tiny example, and **where it lives in our code**.
- When something clicks, that's the entry doing its job. When it doesn't, ask for a deep-dive.

---

## Concept Index

| # | Concept | Domain | First seen in |
|---|---------|--------|---------------|
| 1 | Modules & imports | Python | every file |
| 2 | Docstrings | Python | top of every file |
| 3 | `from __future__ import annotations` | Python | top of `config.py` |
| 4 | Type hints | Python | everywhere |
| 5 | Classes | Python | `credentials.py` |
| 6 | Abstract Base Classes (ABC) | Python | `credentials.py` |
| 7 | Inheritance & overriding | Python | `credentials.py` |
| 8 | Custom exceptions | Python | `credentials.py`, `config.py` |
| 9 | `None` and optional values | Python | both files |
| 10 | Dataclasses | Python | `config.py`, `schema.py` |
| 11 | Immutability (`frozen=True`) | Python | `config.py`, `schema.py` |
| 12 | Functions, arguments, keyword-only args | Python | `config.py` |
| 13 | `raise ... from exc` (exception chaining) | Python | `config.py` |
| 14 | The factory function pattern | Python | `credentials.py` |
| 15 | Unit testing with pytest | Python/Tooling | all `test_*.py` |
| 16 | `__init__` and `self` (constructors) | Python | Exercises 1 & 2 |
| 17 | Polymorphism | Python | Exercise 1 (Q4) |
| 18 | Indentation & tabs-vs-spaces | Python | Exercise 2 |
| 19 | YAGNI ("You Aren't Gonna Need It") | Engineering | FileCredentialProvider decision |
| 20 | venv auto-activation in VS Code | VS Code | daily startup |
| 21 | Lazy imports | Python | `KeyringCredentialProvider`, `bootstrap.py` |
| 22 | Composition (chaining objects) | Python | `ChainedCredentialProvider` |
| 23 | Context managers (`with` / `__enter__` / `__exit__`) | Python | `tm1_client.py` |
| 24 | Dependency injection | Python | `tm1_client.py`, tests |
| 25 | Guard clauses | Python | `ensure_writable` |
| 26 | Resource ownership & lifecycle | Engineering | `_owns_service` flag |
| 27 | Idempotency | Engineering | `bootstrap.py` |
| 28 | Separation of concerns ("what" vs "how") | Engineering | `schema.py` vs `bootstrap.py` |
| 29 | Verify against real documentation | Engineering | TM1py API checks |
| 30 | Writing cells & creating elements (TM1py) | TM1py | `audit_writer.py`, `bootstrap.py` |
| 31 | Static vs dynamic analysis | Engineering | the whole parser |
| 32 | Anti-corruption layer | Engineering | `ti_reader.py` |
| 33 | String-aware scanning (state machines) | Python | `blocks.py`, `references.py` |
| 34 | Regex and negative lookbehind | Python | `references.py` |
| 35 | Balanced-parenthesis parsing | Python | `references.py` |
| 36 | Correctness over coverage | Engineering | `const_prop.py` |
| 37 | Capture facts, defer judgement | Engineering | `assignments.py` |
| 38 | Fixpoint resolution with a cycle guard | Python | `const_prop.py` (transitive) |
| 39 | Lookup tables encode domain knowledge | Engineering | `references.py` TARGET_ARG_INDEX |
| 40 | Glob patterns (`fnmatch`) | Python | `exclusions.py` |
| 41 | Rules with precedence | Engineering | `exclusions.py` |
| 42 | Error isolation (resilience over optimism) | Engineering | `extract.py` orchestrator |
| 43 | Batching for efficiency | Engineering | `extract.py` (one write, not 322) |
| 44 | Orchestration (composing tested pieces) | Engineering | `extract.py` |
| 45 | Callbacks (progress reporting) | Python | `extract.py` ProgressFn |

---

## 1-30 (foundation + write path)

(Concepts 1-30 cover: modules/imports, docstrings, type hints, classes, ABCs, inheritance,
custom exceptions, `None`, dataclasses, immutability, keyword-only args, exception chaining,
factories, pytest, `__init__`/`self`, polymorphism, indentation, YAGNI, venv auto-activation,
lazy imports, composition, context managers, dependency injection, guard clauses, ownership,
idempotency, separation of concerns, verify-against-docs, and TM1py cell/element writes. See
earlier entries in this file — they remain unchanged and are summarised in the index above.)

---

## 31. Static vs dynamic analysis

**What:** *Dynamic* analysis runs code and observes it (needs a working environment, data,
supporting objects). *Static* analysis reads code without running it (needs only the source text).

**In our code:** the whole parser is static — it reads a TI's source as text and pattern-matches.
This is why it analysed a 492-process instance in seconds, and why it happily analyses TIs whose
cubes do not even exist (the anonymised test loaders).

---

## 32. Anti-corruption layer

**What:** A boundary that keeps a messy external API from leaking into your clean internal code — it
maps the external shape into your own once, in one place.

**In our code:** `ti_reader.py`. TM1py exposes a process as ~25 attributes; `ti_reader` maps them once
into a tidy `TIProcess`, so everything downstream works against our clean shape and a future TM1py
change touches only this one file.

---

## 33. String-aware scanning (state machines)

**What:** Walking text character-by-character while tracking state (e.g. "am I inside a string?"), so
structure is respected. You cannot treat code as plain text — strings, escapes, and nesting matter.

**In our code:** `strip_comment` in `blocks.py` tracks whether it is inside a `'...'` string (honouring
the doubled-quote `''` escape) so a `#` inside a string is NOT treated as a comment. Proven on real
code.

---

## 34. Regex and negative lookbehind

**What:** Regular expressions match text patterns. A *negative lookbehind* `(?<!...)` asserts what must
NOT precede a match.

**In our code:** `_NAME_BEFORE_PAREN` finds a whole-word function name followed by `(`. The lookbehind
ensures `CellPutN(` matches but `MyCellPutN(` does not.

---

## 35. Balanced-parenthesis parsing

**What:** To grab a function's arguments you cannot just "find the next `)`" — nested calls need depth
counting. Track paren depth (ignoring parens inside strings) and split top-level commas.

**In our code:** `_extract_arg_string` and `_split_top_level_args` in `references.py`.

---

## 36. Correctness over coverage

**What:** A lineage tool that gives a *confident wrong answer* is worse than one that says "unknown."
Prefer a known-unknown to a wrong resolution.

**In our code:** `const_prop.py` refuses to resolve a variable that is assigned conditionally, or
differently, or via a function call. It would rather leave `(cCube)` unresolved than guess wrongly.
The whole-model run's 5,012 "unresolved" count is this principle being honest at scale.

---

## 37. Capture facts, defer judgement

**What:** A good dictionary captures raw facts and lets a human apply judgement where the machine
cannot safely.

**In our code:** `assignments.py` captures *every* variable assignment so a developer can trace
`cCube = cSourceCube = a cube read` by hand — complementing const-prop, which only auto-resolves what
is safe.

---

## 38. Fixpoint resolution with a cycle guard

**What:** Following variable-to-variable chains to a fixed point (`cCube -> cSourceCube -> literal`),
while guarding against cycles (`a = b; b = a`) with a "seen" set so you never loop forever.

**In our code:** the transitive resolver in `const_prop.py`. It resolved all 122 `cCube` references in
the real loader to `Food_Weekly_Sales`, while correctly refusing cycles and ambiguous sources.

---

## 39. Lookup tables encode domain knowledge

**What:** Instead of complex per-case `if/else` logic, put the knowledge in a data table and write one
generic piece of code that consults it. Adding a new case = adding a table entry, not changing code.

**In our code:** `TARGET_ARG_INDEX` in `references.py` records which argument holds the cube/dimension
for each function. One small table turned "this process touches vNewVal" into "this process writes to
Food_Weekly_Sales", with no added code complexity.

---

## 40. Glob patterns (`fnmatch`)

**What:** Shell-style wildcard matching — `*` matches any run of characters, `?` any single character.
Python's `fnmatch.fnmatch(name, pattern)` does case-insensitive-friendly glob matching.

**In our code:** `exclusions.py` matches process names against patterns like `bedrock.*`, `}bedrock.*`,
`cubewise.*`. A glob is a lightweight, readable way to say "any process whose name starts with
bedrock." Simpler than a full regex when you only need wildcards.

---

## 41. Rules with precedence

**What:** When multiple rules could apply, the *order* they are checked matters. You define an explicit
precedence so the outcome is predictable.

**In our code:** `decide()` in `exclusions.py` checks in a fixed order: explicit-include (always wins)
-> explicit-exclude -> name patterns -> substrings -> default include. This means a genuine business
process called `Test.Coverage.RealThing` can be force-included even though it contains "test". Encoding
precedence explicitly (rather than hoping the checks happen to run in the right order) makes the
behaviour intentional and testable.

---

## 42. Error isolation (resilience over optimism)

**What:** When processing many items, wrap each in its own `try/except` so one failure doesn't abort
the whole batch. Record the failure, keep going.

**Why it matters:** with 492 real, varied processes, *something* will surprise the parser. The naive
version crashes on the first oddity and you lose the whole run. The resilient version delivers
everything that worked and reports what didn't.

**In our code:** the per-process loop in `extract.py` catches any exception, counts it, records
`(name, error)`, and continues. The test `test_failing_process_does_not_abort_run` proves it. This is
why the whole-model run reported "0 failures" cleanly — and *would* have kept going even if some had
failed. Note: a broad `except Exception` is normally discouraged, but is exactly right here, where the
goal is "one bad input must not kill the batch."

---

## 43. Batching for efficiency

**What:** Instead of many small remote calls, collect the work and make one (or a few) large calls.

**In our code:** the orchestrator collects cube-lineage rows from *all* processes and writes them to
`}Meta_Process_Cube` in a single `cells.write`, rather than 322 separate writes. Far faster against a
real instance, and the test `test_rows_batched_into_single_write` proves only one write happens.

---

## 44. Orchestration (composing tested pieces)

**What:** Building a higher-level workflow by wiring together smaller components that are already built
and tested — introducing very little new logic, mostly "glue" and control flow.

**In our code:** `extract.py` composes `TIReader` + `code_lines` + `build_const_table` +
`extract_references` + `rollup_cube_lineage` + `write_cube_lineage`, wrapped in exclusion filtering and
error isolation. The hard parts were done and trusted; the orchestrator is the loop that runs them over
the whole model. This is the payoff of building bottom-up: the whole-model command was mostly assembly.

---

## 45. Callbacks (progress reporting)

**What:** Passing a *function* as an argument, so the caller can plug in behaviour (here, "print a
progress line") without the callee knowing or caring what it does.

**In our code:** `extract_all(client, progress=...)` accepts a `ProgressFn` callback invoked per
process as `(index, total, name, status)`. The CLI passes a function that prints a line; a test passes
one that records calls in a list. The orchestrator doesn't know or care — it just calls the callback.
This keeps the orchestrator decoupled from *how* progress is displayed.

---

*End of learning log (to be appended as we learn).*
