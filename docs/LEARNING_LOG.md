# TM1 Data Dictionary — Learning Log

> A personal reference of the **concepts** we cover while building the tool — Python, Git,
> VS Code, and TM1py — each explained in plain English with a pointer to where it appears in
> our own code. Grows as we build. Lives in `docs/` so it travels with the project.
>
> Companion to `BUILD_JOURNAL.md` (which records *what* we built and *why*). This file records
> *what you learned*.

**Owner:** Zameel Arif
**Status:** current through diagnostics (parser + rollup + cube writer + exclusions +
`tm1dd extract` + `tm1dd diagnose-unresolved`).

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
| 43 | Batching for efficiency | Engineering | `extract.py` |
| 44 | Orchestration (composing tested pieces) | Engineering | `extract.py` |
| 45 | Callbacks (progress reporting) | Python | `extract.py` ProgressFn |
| 46 | Scope before depth (reduce the problem first) | Engineering | widened exclusions (`}*`) |
| 47 | Measure before you optimise | Engineering | `diagnostics.py` |
| 48 | Aggregation / grouping / frequency counting | Python | `diagnostics.py` |
| 49 | The limits of static analysis (runtime params) | Engineering | the `pCubeName` finding |
| 50 | Good data models make new questions cheap | Engineering | `--expression` via stored occurrences |

---

## 1-45

(Concepts 1-45 remain as previously recorded — foundation, write path, the parser, cube writer,
and the orchestrator. See the earlier entries; they are unchanged and summarised in the index above.
The new material for this update is concepts 46-50 below.)

---

## 46. Scope before depth (reduce the problem before solving it)

**What:** Before pouring effort into *solving* a problem harder, ask whether you can *shrink* the
problem first. Narrowing scope is often far higher-leverage than deepening effort.

**In our code:** the whole-model run reported 5,012 unresolved references. Instead of immediately
trying to *resolve* more, we asked "do we even care about all these processes?" ~200 were framework
machinery (`}APQ`, `}tp_`, `}pulse_`, ...), all `}`-prefixed. Adding one exclusion pattern (`}*`)
dropped included processes 322 -> 117 and unresolved references 5,012 -> 133 (a 97% cut). No clever
code - just the right scope. The remaining work became ~37x smaller and entirely relevant.

**Lesson:** "reduce the problem" beats "try harder" surprisingly often. Scope first, depth second.

---

## 47. Measure before you optimise

**What:** Don't guess what to improve - measure it. Build the instrument that tells you where the
problem actually is, then target the highest-impact cause.

**In our code:** `diagnostics.py` + `tm1dd diagnose-unresolved` is a "resolution profiler." Rather
than guessing which variable patterns to teach the resolver, it lists the unresolved target
expressions by frequency. The result was decisive: two expressions (`pCubeName`, `pTargetCube`)
caused 77% of the misses. This is the same discipline as a performance profiler: optimise what the
measurement proves is costly, not what you *think* is.

---

## 48. Aggregation / grouping / frequency counting

**What:** Turning a flat list of events into a grouped, counted summary - "how many of each kind,
and which is most common?" A very common data-analysis shape.

**In our code:** `diagnose()` walks every unresolved occurrence and groups them by expression into
`UnresolvedGroup`s, each counting its occurrences and the distinct processes it appears in. `top()`
sorts groups by frequency. The pattern - collect events, key them, count per key, sort by count - is
one you'll reuse constantly (log analysis, reporting, "top N" of anything).

---

## 49. The limits of static analysis (runtime parameters)

**What:** Static analysis reads *source code*; it cannot know values that are only decided at
*runtime*. A process parameter (`pCubeName`, `pTargetCube`) gets its value from whoever *calls* the
process - there is no literal in the source to resolve it to.

**In our code:** the diagnostic revealed that most remaining unresolved cube targets were
`p`-prefixed parameters. Const-prop leaving them unresolved is not a bug - it is the *truth*: "this
utility writes to whatever cube you pass it." Recognising this reframed the plan: there was little
"resolution" work left to do, because the answer genuinely isn't in the code. Knowing the *boundary*
of what a technique can do is as important as the technique itself.

---

## 50. Good data models make new questions cheap

**What:** When your data is modelled well, answering a new question is "store or read a bit more of
what you already have," not a rewrite.

**In our code:** the first diagnostic answered "*what* patterns cause misses?" by counting per
expression. When we then needed "*where* does expression X occur?", the change was tiny: have each
`UnresolvedGroup` keep its list of occurrences (which it was already visiting) instead of only
counting them, and add a `find()` method. The `--expression ""` locator (to hunt blank targets) fell
straight out. A structure that holds the underlying facts, not just summaries, makes future questions
cheap to answer.

---

*End of learning log (to be appended as we learn).*
