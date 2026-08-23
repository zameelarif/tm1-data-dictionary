# TM1 Data Dictionary — Learning Log

> A personal reference of the **concepts** we cover while building the tool — Python, Git,
> VS Code, and TM1py — each explained in plain English with a pointer to where it appears in
> our own code. Grows as we build. Lives in `docs/` so it travels with the project.
>
> Companion to `BUILD_JOURNAL.md` (which records *what* we built and *why*). This file records
> *what you learned*.

**Owner:** Zameel Arif
**Started:** learning-as-we-build, from the config module onward.

---

## How to use this log

- Skim the **Concept Index** below to find a topic fast.
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
| 10 | Dataclasses | Python | `config.py` |
| 11 | Immutability (`frozen=True`) | Python | `config.py` |
| 12 | Functions, arguments, keyword-only args | Python | `config.py` |
| 13 | `raise ... from exc` (exception chaining) | Python | `config.py` |
| 14 | The factory function pattern | Python | `credentials.py` |
| 15 | Unit testing with pytest | Python/Tooling | `test_config.py` |
| 16 | `__init__` and `self` (constructors) | Python | Exercise 1 & 2 |
| 17 | Polymorphism | Python | Exercise 1 (Q4) |
| 18 | Indentation & tabs-vs-spaces | Python | Exercise 2 |

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
of the project connect.

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

**In our code:** first import line of `config.py` and `credentials.py`.

---

## 4. Type hints

**What:** Optional labels telling the reader (and `mypy`) what type a value is meant to be. They
don't change how the code runs; they catch mistakes and document intent.

**Example:**
```python
def greet(name: str) -> str:      # takes a str, returns a str
    return "hi " + name
```

**In our code:** `def get_secret(self, name: str) -> str | None:` — takes a string `name`, returns
either a string or `None`. `mypy` uses these to catch bugs before runtime (it already caught one
for us in `config.py`).

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

**In our code:** `EnvCredentialProvider` is a class; `EnvCredentialProvider()` creates an instance
that knows how to fetch secrets from environment variables.

---

## 6. Abstract Base Classes (ABC)

**What:** A class that defines *what methods must exist* but not how they work — a contract.
You can't create it directly; you must subclass it and fill in the blanks. `@abstractmethod` marks
the methods that subclasses *must* implement.

**Why:** It lets the rest of the code depend on the *contract*, not any specific implementation. We
can swap env-vars for keyring for a vault, and nothing that uses the contract needs to change.

**In our code:** `CredentialProvider(ABC)` with `@abstractmethod def get_secret(...)`. Every real
provider (env, keyring, vault) subclasses it and provides its own `get_secret`. Confirmed in
Exercise 1 Q1: Python *forces* every subclass to define `get_secret`, or it refuses to create it.

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
from the base class for free, and *overrides* `get_secret` with its own env-var logic.

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

**In our code:** `CredentialError` and `ConfigError`. When config is bad we `raise ConfigError(...)`
with a clear message — this is the "fail fast, fail clearly" principle in action.

---

## 9. `None` and optional values

**What:** `None` is Python's "nothing here" value. A type like `str | None` means "a string, or
nothing."

**In our code:** `namespace: str | None = None` — a TM1 namespace might not apply (basic auth), so
it's optional and defaults to `None`. Our helpers turn empty strings into `None` deliberately. In
Exercise 2, `FileCredentialProvider` returns `None` when the file is missing, so a chain can fall
back to the next provider.

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

**In our code:** `ConnectionConfig`, `RunConfig`, `LogConfig`, `AppConfig` — clean containers for
validated configuration values. (Note: a `@dataclass` writes `__init__` for you automatically —
see concept #16.)

---

## 11. Immutability (`frozen=True`)

**What:** `@dataclass(frozen=True)` makes instances *read-only* — you can't change fields after
creation. Attempting to raises `FrozenInstanceError`.

**Why:** Config should be loaded once and never accidentally changed mid-run. Immutability enforces
that at the language level.

**In our code:** all four config dataclasses are `frozen=True`. Our test `test_config_is_immutable`
proves you can't reassign `cfg.connection.port`.

---

## 12. Functions, arguments, keyword-only args

**What:** Functions take *positional* args and *keyword* args. A bare `*` in the signature forces
everything after it to be passed by name — clearer at the call site.

**Example:**
```python
def f(a, *, verbose=False):   # 'verbose' must be named
    ...
f(1, verbose=True)            # OK
f(1, True)                    # ERROR — can't pass verbose positionally
```

**In our code:** `def load_config(config_path, *, env_path=None, provider=None)`. The `*` means you
must call `load_config(path, provider=...)` — self-documenting and prevents argument-order mistakes.

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

**In our code:** `_as_int` and `_build_connection` do exactly this — turn low-level errors into
clear `ConfigError`s while keeping the original cause.

---

## 14. The factory function pattern

**What:** A small function whose job is to *create and return* an object, hiding the details of
which one. Callers ask the factory instead of constructing directly.

**Why:** We can change *what* gets built (env provider today, keyring→env chain tomorrow) in one
place, and no caller has to change.

**In our code:** `default_provider()` returns the provider Phase 1 uses. Later it'll return a
chained keyring→env provider — and `config.py` won't need a single edit.

---

## 15. Unit testing with pytest

**What:** Small functions that check our code behaves correctly. `pytest` finds and runs them.
`assert` states what must be true; `pytest.raises(...)` checks an error is thrown.

**Example:**
```python
def test_add():
    assert add(2, 3) == 5
```

**In our code:** `test_config.py` has 10 tests — happy path plus every failure mode. They use a
*temporary* config file and fake environment variables, so they need no real TM1 and never touch
your real `.env`. `monkeypatch` safely sets/removes env vars just for one test.

---

## 16. `__init__` and `self` (constructors)

**What:** `__init__` is the **constructor** — a special method that runs *automatically* the moment
you create an instance. Its job is to set the instance up with its starting data. `self` means
"this particular instance — me."

**Example:**
```python
class StaticCredentialProvider(CredentialProvider):
    def __init__(self, secret: str) -> None:
        self._secret = secret          # remember 'secret' onto myself
    def get_secret(self, name: str) -> str | None:
        return self._secret            # use what I remembered
```
Creating it: `p = StaticCredentialProvider("hunter2")` → Python calls `__init__("hunter2")`, which
stores `"hunter2"` in `self._secret`.

**Rule of thumb:** a class needs `__init__` when each instance must carry its own data.
`EnvCredentialProvider` has **no** `__init__` (nothing to remember — it reads the env every time);
`StaticCredentialProvider` and `FileCredentialProvider` **do** (they must remember a secret / a
file path). Also: a `@dataclass` writes `__init__` for you, which is why our config classes didn't
need one by hand.

**Leading underscore** (`_secret`, `_file_path`) is a convention meaning "internal — don't touch
from outside." (Learned in Exercises 1 & 2.)

---

## 17. Polymorphism

**What:** "One method, many behaviours." When shared code calls `self.something()`, it runs the
*actual object's* version — even if that shared code was written before the object's class existed.

**In our code:** `require_secret` is written once in the base `CredentialProvider`. When it calls
`self.get_secret(...)`, it runs whichever provider's `get_secret` is actually in play — env, static,
file, keyring. That's why we write the shared logic once and it "just works" for every provider.
(Discovered in Exercise 1 Q4.)

---

## 18. Indentation & tabs-vs-spaces

**What:** In Python, **indentation defines structure** — it's not just for looks. Lines at the same
indent run in sequence; indented lines are "inside" the block above them. There are no `{ }` braces.

**Two golden rules:**
1. **Put lines at the right level.** A `return` that should run *after* an `if` must be at the same
   indent as the `if`, not inside it:
   ```python
   if not path.exists():
       return None          # inside the if
   return path.read_text()  # OUTSIDE the if — runs when file exists
   ```
2. **Never mix tabs and spaces.** Python rejects it (`TabError`/`IndentationError`). Use **spaces**
   (4 per level, the standard). VS Code inserts spaces on Tab for `.py` files, and `black` fixes
   indentation automatically on commit — so the tools protect you here.

**In our code:** surfaced while writing `FileCredentialProvider` in Exercise 2 — the final `return`
was accidentally trapped inside the `if`, and a tab had crept in among the spaces.

---

*End of learning log (to be appended as we learn).*
