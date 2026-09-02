# TM1 Data Dictionary — Learning Log

> A personal reference of the **concepts** we cover while building the tool — Python, Git,
> VS Code, and TM1py — each explained in plain English with a pointer to where it appears in
> our own code. Grows as we build. Lives in `docs/` so it travels with the project.
>
> Companion to `BUILD_JOURNAL.md` (which records *what* we built and *why*). This file records
> *what you learned*.

**Owner:** Zameel Arif
**Status:** current through chain lineage (parser + cube writer + orchestrator + exclusions +
diagnostics + multi-line joining + chain writer).

---

## How to use this log

- Skim the **Concept Index** to find a topic fast.
- Each concept has: a plain-English definition, a tiny example, and **where it lives in our code**.
- When something clicks, that's the entry doing its job. When it doesn't, ask for a deep-dive.

---

## Concept Index

| # | Concept | Domain | First seen in |
|---|---------|--------|---------------|
| 1-30 | Foundations (imports, dataclasses, ABCs, context managers, DI, keyring, TM1py writes, ...) | Python/Eng | config → audit_writer |
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
| 42 | Error isolation (resilience over optimism) | Engineering | `extract.py` |
| 43 | Batching for efficiency | Engineering | `extract.py` |
| 44 | Orchestration (composing tested pieces) | Engineering | `extract.py` |
| 45 | Callbacks (progress reporting) | Python | `extract.py` ProgressFn |
| 46 | Scope before depth | Engineering | widened exclusions (`}*`) |
| 47 | Measure before you optimise | Engineering | `diagnostics.py` |
| 48 | Aggregation / grouping / frequency counting | Python | `diagnostics.py` |
| 49 | The limits of static analysis (runtime params) | Engineering | the `pCubeName` finding |
| 50 | Good data models make new questions cheap | Engineering | `--expression` via stored occurrences |
| 51 | Logical vs physical lines (line joining) | Python | `blocks.py` `code_lines` |
| 52 | Fix at the root layer, not the symptom | Engineering | multi-line fix in `blocks.py` |
| 53 | Parse once, derive many | Engineering | `extract.py` (cube + chain from one parse) |
| 54 | Aliasing a dimension (same axis twice) | TM1py | `}Meta_Process_Callee` |
| 55 | Coverage as a "did I forget something?" signal | Tooling | chain_rollup 29% -> missing test file |
| 56 | Define vs. create (schema vs. bootstrap wiring) | Engineering | adding `process_chain_schema` |
| 57 | Don't over-defend (trust your contracts) | Engineering | the `hasattr` mypy nit |

*(Concepts 1-30 remain as recorded in earlier versions of this file; the parser-and-beyond
concepts 31-50 are unchanged and summarised in the index above. New for this update: 51-57.)*

---

## 51. Logical vs physical lines (line joining)

**What:** A *physical* line is what's between two newlines; a *logical* line is a complete
statement, which may span several physical lines. A line-based parser must reassemble multi-line
statements before analysing them.

**In our code:** `code_lines()` in `blocks.py` accumulates physical lines until their parenthesis
depth returns to zero (string-aware), then emits one logical line - keeping the *start* line number.
A `CellPutN(...)` split across 5 lines becomes one line, so its cube argument is read correctly
instead of coming out blank. `segment()` still gives the physical view where that matters.

---

## 52. Fix at the root layer, not the symptom

**What:** When a bug shows up downstream, fix its *cause* at the right layer - one fix then benefits
every consumer, instead of patching each symptom separately.

**In our code:** blank cube targets appeared in `references.py`'s output, but the *cause* was
multi-line statements in `blocks.py`. We fixed `blocks.py` (line joining), so references,
const-prop, AND assignments all improved at once. Had we patched `references.py` alone, we'd have
had to patch the other two separately too.

---

## 53. Parse once, derive many

**What:** Do the expensive work once, then derive multiple outputs from it - rather than repeating
the expensive step per output.

**In our code:** the orchestrator parses each process a single time, then feeds its references to
*both* the cube rollup and the chain rollup. `tm1dd extract` populates two cubes from one parse per
process. Efficient and clean.

---

## 54. Aliasing a dimension (same axis twice)

**What:** A cube can conceptually use the same dimension on two axes, but TM1 requires distinct
dimension *names*. The idiom is to make a second, alias-style copy of the dimension.

**In our code:** `}Meta_Process_Chain` records caller AND callee, both processes. The caller axis is
`}Meta_Process`; the callee axis is `}Meta_Process_Callee` (a second process dimension). Both hold
process names; the two names let one cube relate processes to processes.

---

## 55. Coverage as a "did I forget something?" signal

**What:** Test coverage isn't just a score - a module that's dramatically less covered than its
neighbours is a smoke signal (missing tests, untested branch, dead code).

**In our code:** after adding chain lineage, `chain_rollup.py` showed 29% while everything else was
90%+. That asymmetry flagged that `test_chain_rollup.py` hadn't been saved. Reading coverage as a
*diagnostic* caught the gap.

---

## 56. Define vs. create (schema vs. bootstrap wiring)

**What:** Adding a `}Meta_*` cube has two halves: (1) *define* it in `schema.py` (what it looks
like), and (2) *create* it by wiring that definition into the bootstrap command (actually build it
in TM1). Miss the second and the writer has nowhere to write.

**In our code:** `process_chain_schema()` existed in `schema.py`, but `tm1dd extract` failed with a
404 ("`}Meta_Process_Chain` can not be found") because the bootstrap command didn't call it yet.
Adding `r3 = ensure_schema(client, process_chain_schema())` fixed it. Same "define vs. use" split as
"a function must be both defined and imported."

---

## 57. Don't over-defend (trust your contracts)

**What:** Defensive code that guards against cases which can't actually happen has a cost - it
muddies types and adds paths that never run. When you *know* the shape of your data (because a
contract guarantees it), write to that shape directly.

**In our code:** `extract.py` had `[d.name if hasattr(d, "name") else d for d in part.excluded]`.
But `partition()` always returns `ExclusionDecision` objects with a `.name`, so the fallback was
unreachable - and it made the type `list[object]` instead of `list[str]`, which mypy rightly
flagged. Simplifying to `[d.name for d in part.excluded]` was cleaner and correctly typed.

---

*End of learning log (to be appended as we learn).*
