# TM1 Data Dictionary — Learning Log

> A personal reference of the **concepts** we cover while building the tool — Python, Git,
> VS Code, and TM1py — each explained in plain English with a pointer to where it appears in
> our own code. Grows as we build. Lives in `docs/` so it travels with the project.
>
> Companion to `BUILD_JOURNAL.md` (which records *what* we built and *why*). This file records
> *what you learned*.

**Owner:** Zameel Arif
**Status:** current through the write-path milestone (config, credentials, keyring, TM1 client,
schema, bootstrap, audit writer).

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
| 29 | Verify against real documentation | Engineering | TM1py cells/elements API |
| 30 | Writing cells & creating elements (TM1py) | TM1py | `audit_writer.py`, `bootstrap.py` |

---

## 1. Modules & imports

**What:** A *module* is just a `.py` file. `import` lets one file use code from another.

**Example:**
```python
import os                       # bring in the whole 'os' module; use as os.getenv(...)
from pathlib import Path        # bring in just 'Path' from the 'pathlib' module
```

**In our code:** `config.py` does `from tm1_data_dictionary.credentials import CredentialProvider`
— that's our own `credentials.py` module being imported into `config.py`. This is how the pieces
of the project connect. Import paths mirror folder paths: `tm1_data_dictionary.writers.audit_writer`
means the file at `src/tm1_data_dictionary/writers/audit_writer.py`.

---

## 2. Docstrings

**What:** A string at the top of a file, class, or function that documents it. Triple-quoted.

**Example:**
```python
def add(a, b):
    """Return the sum of a and b."""
    return a + b
```

**In our code:** every file opens with a `"""..."""` block explaining its purpose. Tools and IDEs
read these; they're documentation that lives *with* the code.

---

## 3. `from __future__ import annotations`

**What:** A line at the very top that lets us write modern type hints (like `str | None`) even on
older Python versions, and makes hints a bit faster. Harmless, standard, always safe to include.

**In our code:** the first import line of nearly every module.

---

## 4. Type hints

**What:** Optional labels telling the reader (and `mypy`) what type a value is meant to be. They
don't change how the code runs; they catch mistakes and document intent.

**Example:**
```python
def greet(name: str) -> str:      # takes a str, returns a str
    return "hi " + name
```

**In our code:** `def get_secret(self, name: str) -> str | None:` — takes a string, returns a string
or `None`. `mypy` uses these to catch bugs before runtime (it has caught several for us).

---

## 5. Classes

**What:** A blueprint for bundling data and behaviour together. You create *instances* from a class.

**Example:**
```python
class Dog:
    def bark(self):
        return "woof"

d = Dog()      # an instance
d.bark()       # "woof"
```

**In our code:** `EnvCredentialProvider`, `TM1Client`, `AuditWriter` are all classes.

---

## 6. Abstract Base Classes (ABC)

**What:** A class that defines *what methods must exist* but not how they work — a contract.
You can't create it directly; you must subclass it and fill in the blanks. `@abstractmethod` marks
the methods that subclasses *must* implement.

**Why:** It lets the rest of the code depend on the *contract*, not any specific implementation. We
can swap env-vars for keyring for a vault, and nothing that uses the contract needs to change.

**In our code:** `CredentialProvider(ABC)` with `@abstractmethod def get_secret(...)`. Every real
provider (env, keyring, chained) subclasses it. Python *forces* every subclass to define
`get_secret`, or it refuses to create it.

---

## 7. Inheritance & overriding

**What:** A class can *inherit* from another, reusing its code and *overriding* specific parts.

**Example:**
```python
class Animal:
    def speak(self): return "..."
class Cat(Animal):
    def speak(self): return "meow"   # overrides Animal.speak
```

**In our code:** `EnvCredentialProvider(CredentialProvider)` inherits the `require_secret` helper
from the base class for free, and *overrides* `get_secret` with its own logic.

---

## 8. Custom exceptions

**What:** Your own error types, so failures are specific and catchable. Made by subclassing an
existing exception.

**Example:**
```python
class ConfigError(RuntimeError):
    """Raised when configuration is invalid."""
raise ConfigError("port is missing")
```

**In our code:** `CredentialError`, `ConfigError`, `TM1ClientError`. Each makes a specific failure
mode catchable and clear — the "fail fast, fail clearly" principle.

---

## 9. `None` and optional values

**What:** `None` is Python's "nothing here" value. A type like `str | None` means "a string, or
nothing."

**In our code:** `namespace: str | None = None` — a TM1 namespace might not apply (basic auth), so
it's optional. Every credential provider returns `None` when it has no secret, so a chain can fall
back to the next one.

---

## 10. Dataclasses

**What:** A concise way to make a class that mainly holds data. The `@dataclass` decorator writes
the boilerplate (constructor, etc.) for you.

**Example:**
```python
from dataclasses import dataclass
@dataclass
class Point:
    x: int
    y: int
p = Point(1, 2)   # p.x == 1
```

**In our code:** `ConnectionConfig`, `RunConfig`, `LogConfig`, `AppConfig` (config), and
`ElementDef`, `DimensionDef`, `CubeDef`, `SchemaDef`, `AuditRecord`, `BootstrapResult` (schema and
writers). Clean containers for structured data. (A `@dataclass` writes `__init__` for you — see #16.)

---

## 11. Immutability (`frozen=True`)

**What:** `@dataclass(frozen=True)` makes instances *read-only* — you can't change fields after
creation. Attempting to raises `FrozenInstanceError`.

**Why:** Config and schema definitions should be loaded/defined once and never accidentally changed.
Immutability enforces that at the language level.

**In our code:** all four config dataclasses and all the schema-definition dataclasses are frozen.
Tests prove you can't reassign their fields.

---

## 12. Functions, arguments, keyword-only args

**What:** Functions take *positional* args and *keyword* args. A bare `*` in the signature forces
everything after it to be passed by name — clearer at the call site.

**Example:**
```python
def f(a, *, verbose=False):   # 'verbose' must be named
    ...
f(1, verbose=True)            # OK
f(1, True)                    # ERROR
```

**In our code:** `def load_config(config_path, *, env_path=None, provider=None)`, and
`TM1Client(config, *, service=None)`. The `*` prevents argument-order mistakes.

---

## 13. `raise ... from exc` (exception chaining)

**What:** When you catch one error and raise a friendlier one, `from exc` keeps the original as the
"cause," preserving the full story for debugging.

**Example:**
```python
try:
    int("abc")
except ValueError as exc:
    raise ConfigError("port must be a number") from exc
```

**In our code:** `_as_int` and `_build_connection` (config), `set_keyring_secret` (credentials),
and `connect()` (client) all do this.

---

## 14. The factory function pattern

**What:** A small function whose job is to *create and return* an object, hiding the details of
which one. Callers ask the factory instead of constructing directly.

**Why:** We can change *what* gets built in one place, and no caller has to change.

**In our code:** `default_provider()`. It first returned a bare env provider; after the keyring
upgrade it returns a **keyring→env chain** — and `config.py` did not change one line, because it only
ever calls `default_provider()`. This is the payoff of the factory + ABC design.

---

## 15. Unit testing with pytest

**What:** Small functions that check our code behaves correctly. `pytest` finds and runs them.
`assert` states what must be true; `pytest.raises(...)` checks an error is thrown; `monkeypatch`
safely sets env vars or swaps modules just for one test.

**Example:**
```python
def test_add():
    assert add(2, 3) == 5
```

**In our code:** 58 tests across config, credentials, client, schema, bootstrap, and audit writer.
They use *temporary* files, *fake* environment variables, a *fake* keyring, *fake* TM1py objects, and
*fake* services — so they need no real TM1, no real keyring, and never touch your real `.env`.

---

## 16. `__init__` and `self` (constructors)

**What:** `__init__` is the **constructor** — a special method that runs *automatically* the moment
you create an instance. Its job is to set the instance up with its starting data. `self` means
"this particular instance — me."

**Example:**
```python
class KeyringCredentialProvider(CredentialProvider):
    def __init__(self, service_name: str = KEYRING_SERVICE_NAME) -> None:
        self._service_name = service_name
```

**Rule of thumb:** a class needs `__init__` when each instance must carry its own data.
`EnvCredentialProvider` has none (nothing to remember); `KeyringCredentialProvider`, `TM1Client`,
and `AuditWriter` do. A `@dataclass` writes `__init__` for you. Leading underscore (`_service_name`)
means "internal."

---

## 17. Polymorphism

**What:** "One method, many behaviours." When shared code calls `self.something()`, it runs the
*actual object's* version — even if that shared code was written before the object's class existed.

**In our code:** `require_secret` is written once in the base `CredentialProvider`. When it calls
`self.get_secret(...)`, it runs whichever provider's `get_secret` is actually in play — env, keyring,
chained. Write the shared logic once, and it "just works" for every provider.

---

## 18. Indentation & tabs-vs-spaces

**What:** In Python, **indentation defines structure** — it's not just for looks. Lines at the same
indent run in sequence; indented lines are "inside" the block above them. No `{ }` braces.

**Two golden rules:**
1. Put lines at the right level (a `return` after an `if` must be at the same indent as the `if`).
2. Never mix tabs and spaces — use **spaces** (4 per level). VS Code + `black` handle this for you.

**In our code:** surfaced while writing `FileCredentialProvider` in Exercise 2.

---

## 19. YAGNI ("You Aren't Gonna Need It")

**What:** Don't build features speculatively. Add code when a real need appears, not "just in case."
Unused code is a cost, not a safety net.

**In our code:** we wrote `FileCredentialProvider` as an exercise but chose **not** to keep it — no
environment we work with needs it, and the abstraction means we can add it in ~5 minutes if a client
ever does. Rule of thumb: "do we need it now?" — not "might we need it?".

---

## 20. venv auto-activation in VS Code

**What:** Because VS Code detected `.venv` as the project interpreter, it auto-activates the venv in
each new integrated terminal (running `Set-ExecutionPolicy` + `Activate.ps1` for you). Confirm via
the `(.venv)` prefix.

**Key nuance:** activation is per-shell. A plain PowerShell window opened *outside* VS Code won't be
auto-activated — there you'd still run `.venv\Scripts\Activate.ps1`.

**Daily routine:** start local PA → open project in VS Code → open terminal → glance for `(.venv)` →
work.

---

## 21. Lazy imports

**What:** Importing a module *inside a function* instead of at the top of the file, so the import
only happens when (and if) that code runs.

**Why:** Lets a module load on machines that lack an optional dependency. The rest of the file still
works; only the feature that truly needs the dependency fails, and only if used. Also lets tests
inject a fake module before the import happens.

**In our code:** `KeyringCredentialProvider.get_secret` does `import keyring` inside the method;
`bootstrap.py` and `audit_writer.py` import TM1py object classes inside their functions. This is why
those modules load — and their tests run — without TM1py installed.

---

## 22. Composition (chaining objects)

**What:** Building behaviour by *combining* small, single-purpose objects, rather than one big object
that does everything. The combiner orchestrates; each part stays simple.

**In our code:** `ChainedCredentialProvider` holds a *list* of providers and tries them in order,
returning the first secret found. Keyring and env stay simple and independent; the chain delivers
"keyring first, fall back to env" by composing them.

---

## 23. Context managers (`with` / `__enter__` / `__exit__`)

**What:** An object that defines `__enter__` and `__exit__` can be used with `with`. `__enter__`
runs when the block starts; `__exit__` runs when it ends — **guaranteed, even if an error is raised
inside the block.** Python's mechanism for "always clean up, no matter what."

**Example:**
```python
with TM1Client(cfg) as client:
    client.ensure_writable("create cube")   # even if this raises...
    do_work()
# ...__exit__ still runs here -> the connection is always closed
```

**Why it matters:** without it, a crash mid-work skips the cleanup line and leaks the resource (a
dangling TM1 session, an open file). The `with` guarantee means cleanup *always* happens. You've used
this already every time you wrote `with open(...) as f:`.

**In our code:** `TM1Client.__enter__` calls `connect()`; `__exit__` calls `close()`.

---

## 24. Dependency injection

**What:** Passing a dependency *into* an object instead of having the object create it internally.
Lets you substitute a fake/stub in tests.

**Example:**
```python
client = TM1Client(cfg)                       # real: connects for real
client = TM1Client(cfg, service=FakeService())  # test: no real TM1 needed
```

**Why it matters:** it's what makes code testable. Our client, bootstrap, and writer tests all run
with no TM1py and no server because fakes are injected. Also used for the clock in `AuditWriter`, so
timestamps are deterministic in tests.

---

## 25. Guard clauses

**What:** A check at the *start* of an operation that stops it early if conditions aren't met.

**Example:**
```python
def ensure_writable(self, operation="write"):
    if self.dry_run:
        raise TM1ClientError(f"Refusing to {operation}: dry-run mode.")
```

**Why it matters:** it enforces a rule consistently and loudly. Every write (bootstrap, audit writer)
calls `ensure_writable()` first, so dry-run blocks *all* writes with one central check — no writer can
forget. Note it **raises** (halts), it doesn't quietly return.

**In our code:** `TM1Client.ensure_writable` — the concrete realisation of the spec's dry-run mode.

---

## 26. Resource ownership & lifecycle

**What:** *Whoever creates a resource is responsible for destroying it.* Code that merely *borrows* a
resource must not close it.

**Why it matters:** if a borrower closes a shared resource, other users of it break. Ownership
tracking prevents "someone else already closed the connection I was using" bugs.

**In our code:** the `_owns_service` flag. `TM1Client` only logs out a service it opened itself; an
injected service is left for its owner. The test `test_injected_service_is_not_logged_out` verifies it.

---

## 27. Idempotency

**What:** An operation is *idempotent* if running it once or many times has the same end result.
Running it again is safe — it doesn't duplicate, error, or destroy.

**Why it matters:** a deploy/bootstrap tool must be safe to re-run. You should never fear running it
twice.

**In our code:** `ensure_schema` (bootstrap) checks `exists()` before creating each dimension and
cube. First run creates everything; second run skips everything ("already present"). The audit writer
similarly checks whether a run element exists before creating it. Tests prove empty, everything-exists,
and partial cases.

---

## 28. Separation of concerns ("what" vs "how")

**What:** Splitting a problem into pieces that each do one thing, so each can be understood, tested,
and changed independently.

**In our code:** `schema.py` describes *what* the `}Meta_*` schema is (pure data — dimensions,
elements, cubes) with no TM1py at all; `bootstrap.py` handles *how* to create it in TM1. Result: the
schema can be read and diffed on its own, and adding a new cube means adding *data* to `schema.py`
without touching the creation logic. Same instinct as the credential providers.

---

## 29. Verify against real documentation

**What:** Even experienced developers don't memorise every library's exact method names. The
professional habit is to **check the real docs before shipping**, not rely on memory.

**In our code:** the first draft of `audit_writer.py` used TM1py method names from memory that were
wrong. Before handover, we searched the actual TM1py docs and found the correct API
(`elements.exists`/`elements.create`, and `cells.write(cube_name=..., cellset_as_dict=...)`), then
rewrote to match. Lesson: keep the docs open (tm1py.org) when writing TM1py — a 10-second check saves
an hour of confusion.

---

## 30. Writing cells & creating elements (TM1py)

**What:** The two core write operations the whole extractor is built on.

- **Create an element:** check `service.elements.exists(dim, hier, name)`, then
  `service.elements.create(dim, hier, Element(name, type))`.
- **Write cells:** `service.cells.write(cube_name="C", cellset_as_dict={(el1, el2): value, ...})` —
  the dict is keyed by a *tuple* of element names (one per dimension) mapping to the value.

**In our code:** `audit_writer.py` adds a run-timestamp element to `}Meta_ExtractionRun`, then writes
seven measure cells to `}Meta_Extraction_Audit` in one `cells.write` call. `bootstrap.py` uses
`dimensions.create` and `cubes.create` to build the schema. This is the real TM1py write API the rest
of the parser's writers will use.

---

*End of learning log (to be appended as we learn).*
