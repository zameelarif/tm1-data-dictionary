# TM1 Data Dictionary — Learning Log

> A personal reference of the **concepts** we cover while building the tool — Python, Git,
> VS Code, and TM1py — each explained in plain English with a pointer to where it appears in
> our own code. Grows as we build. Lives in `docs/` so it travels with the project.
>
> Companion to `BUILD_JOURNAL.md` (which records *what* we built and *why*). This file records
> *what you learned*.

**Owner:** Zameel Arif
**Status:** current through the parser (ti_reader, blocks, references, const-propagation with
transitive resolution, variable assignments, function-aware target selection).

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
| 29 | Verify against real documentation | Engineering | TM1py cells/elements/process API |
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

---

## 1. Modules & imports

**What:** A *module* is just a `.py` file. `import` lets one file use code from another.

**Example:**
```python
import os                       # bring in the whole 'os' module; use as os.getenv(...)
from pathlib import Path        # bring in just 'Path' from the 'pathlib' module
```

**In our code:** `config.py` does `from tm1_data_dictionary.credentials import CredentialProvider`.
Import paths mirror folder paths: `tm1_data_dictionary.parser.references` means the file at
`src/tm1_data_dictionary/parser/references.py`.

---

## 2. Docstrings

**What:** A string at the top of a file, class, or function that documents it. Triple-quoted.

**In our code:** every file opens with a `"""..."""` block explaining its purpose.

---

## 3. `from __future__ import annotations`

**What:** A line at the very top that lets us write modern type hints (like `str | None`) on
older Python versions. Harmless, standard, always safe.

**In our code:** the first import line of nearly every module.

---

## 4. Type hints

**What:** Optional labels telling the reader (and `mypy`) what type a value is meant to be.

**Example:** `def get_secret(self, name: str) -> str | None:` — takes a string, returns a string or
`None`. `mypy` uses these to catch bugs before runtime (it has caught several for us).

---

## 5. Classes

**What:** A blueprint bundling data and behaviour. You create *instances* from a class.

**In our code:** `TM1Client`, `AuditWriter`, `TIReader` are all classes.

---

## 6. Abstract Base Classes (ABC)

**What:** A class that defines *what methods must exist* but not how — a contract. `@abstractmethod`
marks methods subclasses *must* implement.

**In our code:** `CredentialProvider(ABC)` with `@abstractmethod def get_secret(...)`. Every real
provider subclasses it; Python *forces* each to define `get_secret`.

---

## 7. Inheritance & overriding

**What:** A class can *inherit* from another, reusing code and *overriding* specific parts.

**In our code:** `EnvCredentialProvider(CredentialProvider)` inherits `require_secret` for free and
overrides `get_secret`.

---

## 8. Custom exceptions

**What:** Your own error types, so failures are specific and catchable.

**In our code:** `CredentialError`, `ConfigError`, `TM1ClientError` — each makes a specific failure
mode catchable and clear.

---

## 9. `None` and optional values

**What:** `None` is Python's "nothing here" value. `str | None` means "a string, or nothing."

**In our code:** `namespace: str | None = None`. Every provider returns `None` when it has no secret,
so a chain can fall back.

---

## 10. Dataclasses

**What:** A concise way to make a class that mainly holds data; `@dataclass` writes the boilerplate.

**In our code:** config classes, schema classes, plus `Reference`, `CodeLine`, `Assignment`,
`VariableInfo`, `ConstTable` in the parser.

---

## 11. Immutability (`frozen=True`)

**What:** `@dataclass(frozen=True)` makes instances read-only; mutating raises `FrozenInstanceError`.

**In our code:** config, schema, and most parser dataclasses (`CodeLine`, `Reference`, `Assignment`)
are frozen — parsed facts should not change after they are recorded.

---

## 12. Functions, arguments, keyword-only args

**What:** A bare `*` in a signature forces everything after it to be passed by name.

**In our code:** `def load_config(config_path, *, env_path=None, provider=None)`, and
`TM1Client(config, *, service=None)`.

---

## 13. `raise ... from exc` (exception chaining)

**What:** When you catch one error and raise a friendlier one, `from exc` keeps the original cause.

**In our code:** `_as_int`, `_build_connection`, `set_keyring_secret`, `connect()`.

---

## 14. The factory function pattern

**What:** A small function whose job is to *create and return* an object, hiding which one.

**In our code:** `default_provider()`. After the keyring upgrade it returns a keyring->env chain —
and `config.py` did not change one line, because it only calls `default_provider()`.

---

## 15. Unit testing with pytest

**What:** Small functions that check behaviour. `assert` states what must be true; `pytest.raises`
checks an error is thrown; `monkeypatch` safely swaps env vars/modules for one test.

**In our code:** ~180 tests across config, credentials, client, schema, bootstrap, audit writer, and
the parser. All use fakes, so no real TM1 is needed.

---

## 16. `__init__` and `self` (constructors)

**What:** `__init__` runs automatically when an instance is created; it sets the instance's starting
data. `self` means "this particular instance."

**In our code:** many classes; a `@dataclass` writes `__init__` for you.

---

## 17. Polymorphism

**What:** "One method, many behaviours." Shared code calling `self.method()` runs the actual
object's version.

**In our code:** `require_secret` (base class) calls `self.get_secret(...)` and runs whichever
provider is in play.

---

## 18. Indentation & tabs-vs-spaces

**What:** In Python, indentation defines structure. Use spaces (4 per level); never mix with tabs.
VS Code + black handle this.

---

## 19. YAGNI ("You Aren't Gonna Need It")

**What:** Don't build features speculatively. Add code when a real need appears.

**In our code:** we wrote `FileCredentialProvider` as an exercise but chose not to keep it.

---

## 20. venv auto-activation in VS Code

**What:** VS Code auto-activates the venv in new integrated terminals; confirm via the `(.venv)`
prefix. A plain terminal opened outside VS Code is not auto-activated.

---

## 21. Lazy imports

**What:** Importing a module *inside a function* so the import only happens when that code runs.

**In our code:** `import keyring` inside `KeyringCredentialProvider.get_secret`; TM1py object classes
imported inside `bootstrap.py` / `audit_writer.py`. This is why those modules load, and their tests
run, without TM1py installed.

---

## 22. Composition (chaining objects)

**What:** Building behaviour by combining small, single-purpose objects.

**In our code:** `ChainedCredentialProvider` holds a list of providers and tries them in order.

---

## 23. Context managers (`with` / `__enter__` / `__exit__`)

**What:** An object with `__enter__`/`__exit__` usable via `with`. `__exit__` runs on exit —
*guaranteed, even if an error is raised inside the block.*

**In our code:** `TM1Client.__enter__` connects; `__exit__` closes. The connection is always logged
out, even on a crash.

---

## 24. Dependency injection

**What:** Passing a dependency *into* an object instead of creating it internally; lets tests
substitute a fake.

**In our code:** the `service` parameter on `TM1Client`; the `const_table` parameter on
`extract_references`; the `clock` on `AuditWriter`. All make code testable with no real TM1.

---

## 25. Guard clauses

**What:** A check at the start of an operation that stops it early if conditions aren't met.

**In our code:** `TM1Client.ensure_writable` — raises in dry-run mode, so every write is blocked
consistently.

---

## 26. Resource ownership & lifecycle

**What:** Whoever creates a resource is responsible for destroying it; a borrower must not close it.

**In our code:** the `_owns_service` flag — the client only logs out a service it opened itself.

---

## 27. Idempotency

**What:** An operation is idempotent if running it once or many times has the same end result.

**In our code:** `ensure_schema` (bootstrap) checks `exists()` before creating; re-running skips
everything. The audit writer checks the run element before creating it.

---

## 28. Separation of concerns ("what" vs "how")

**What:** Split a problem so each piece does one thing.

**In our code:** `schema.py` (what the `}Meta_*` schema is — pure data) vs `bootstrap.py` (how to
create it in TM1). Also `ti_reader.py` (read) vs `blocks.py`/`references.py` (parse).

---

## 29. Verify against real documentation

**What:** Check the real docs before shipping, rather than relying on memory.

**In our code:** we searched the TM1py docs to confirm `cells.write`, `elements.create`, and the
`Process` object's attribute names (`prolog_procedure`, `datasource_*`) before writing `audit_writer`
and `ti_reader`. Corrected a from-memory mistake before it ever reached the project.

---

## 30. Writing cells & creating elements (TM1py)

**What:** The core write operations. Create an element: `elements.exists(...)` then
`elements.create(dim, hier, Element(name, type))`. Write cells:
`cells.write(cube_name="C", cellset_as_dict={(e1, e2): value})`.

**In our code:** `audit_writer.py` and `bootstrap.py`.

---

## 31. Static vs dynamic analysis

**What:** *Dynamic* analysis runs code and observes it (needs a working environment, data, supporting
objects). *Static* analysis reads code without running it (needs only the source text).

**In our code:** the whole parser is static — it reads a TI's source as text and pattern-matches. This
is why it can analyse a 467-process instance in seconds, and why it happily analyses a TI whose cubes
do not even exist (the anonymised test loaders).

---

## 32. Anti-corruption layer

**What:** A boundary that keeps a messy external API from leaking into your clean internal code — it
maps the external shape into your own once, in one place.

**In our code:** `ti_reader.py`. TM1py exposes a process as ~25 attributes with names like
`datasource_ascii_delimiter_char`. `ti_reader` maps them once into a tidy `TIProcess`; everything
downstream works against our clean shape, so a future TM1py change touches only this one file.

---

## 33. String-aware scanning (state machines)

**What:** Walking text character-by-character while tracking state (e.g. "am I inside a string?"),
so structure is respected. You cannot treat code as plain text — strings, escapes, and nesting matter.

**In our code:** `strip_comment` in `blocks.py` tracks whether it is inside a `'...'` string (honouring
the doubled-quote `''` escape) so a `#` inside a string is NOT treated as a comment. Proven on real
code: it correctly ignored commented-out `CellPutN` lines that a naive parser would have counted.

---

## 34. Regex and negative lookbehind

**What:** Regular expressions match text patterns. A *negative lookbehind* `(?<!...)` asserts what
must NOT precede a match.

**In our code:** `_NAME_BEFORE_PAREN = r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\("` finds a
whole-word function name followed by `(`. The lookbehind ensures `CellPutN(` matches but
`MyCellPutN(` does not.

---

## 35. Balanced-parenthesis parsing

**What:** To grab a function's arguments you cannot just "find the next `)`" — nested calls like
`CellPutN(DB('FX', a), 'C', 'e')` need depth counting. You track paren depth (ignoring parens inside
strings) and split top-level commas.

**In our code:** `_extract_arg_string` and `_split_top_level_args` in `references.py`.

---

## 36. Correctness over coverage

**What:** A lineage tool that gives a *confident wrong answer* is worse than one that says "unknown."
Prefer a known-unknown to a wrong resolution.

**In our code:** `const_prop.py` refuses to resolve a variable that is assigned conditionally, or
assigned different values, or via a function call. It would rather leave `(cCube)` unresolved than
guess wrongly. This is the same instinct as the spec's `ParseConfidence`.

---

## 37. Capture facts, defer judgement

**What:** A good dictionary captures raw facts and lets a human apply judgement where the machine
cannot safely.

**In our code:** `assignments.py` captures *every* variable assignment (even ambiguous ones) so a
developer can trace `cCube = cSourceCube = a cube read` by hand — complementing const-prop, which only
auto-resolves what is safe. Your own idea, and exactly the spec's `}Meta_Process_Variable.DerivedFrom`.

---

## 38. Fixpoint resolution with a cycle guard

**What:** Following variable-to-variable chains to a fixed point (`cCube -> cSourceCube -> literal`),
while guarding against cycles (`a = b; b = a`) with a "seen" set so you never loop forever.

**In our code:** the transitive resolver in `const_prop.py`. It resolved all 122 `cCube` references in
the real loader to `Food_Weekly_Sales`, while correctly refusing cycles and ambiguous sources. Same
technique used by dependency resolvers and spreadsheet engines.

---

## 39. Lookup tables encode domain knowledge

**What:** Instead of complex per-case `if/else` logic, put the knowledge in a data table and write one
generic piece of code that consults it. Adding a new case = adding a table entry, not changing code.

**In our code:** `TARGET_ARG_INDEX` in `references.py` records which argument holds the cube/dimension
for each function (`CellPutN`->1, `CellGetN`->0, `AttrPutS`->1, ...). One small table turned "this
process touches vNewVal" (meaningless) into "this process writes to Food_Weekly_Sales" (correct), with
no added code complexity.

---

*End of learning log (to be appended as we learn).*
