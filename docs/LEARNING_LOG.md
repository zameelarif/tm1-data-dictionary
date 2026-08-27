<!--
    APPEND THIS to the bottom of docs/LEARNING_LOG.md
    (paste it just above the final "*End of learning log...*" line, then update the
    Concept Index table at the top by adding rows 23-26).
-->

Add these rows to the Concept Index table at the top of the file:

| 23 | Context managers (`with` / `__enter__` / `__exit__`) | Python | `tm1_client.py` |
| 24 | Dependency injection | Python | `tm1_client.py`, tests |
| 25 | Guard clauses | Python | `ensure_writable` |
| 26 | Resource ownership & lifecycle | Engineering | `_owns_service` flag |

---

## 23. Context managers (`with` / `__enter__` / `__exit__`)

**What:** An object that defines `__enter__` and `__exit__` can be used with `with`. `__enter__`
runs when the block starts; `__exit__` runs when it ends — **guaranteed, even if an error is raised
inside the block.** It's Python's mechanism for "always clean up, no matter what."

**Example:**
```python
with TM1Client(cfg) as client:
    client.ensure_writable("create cube")   # even if this raises...
    do_work()
# ...__exit__ still runs here -> the connection is always closed
```

**Why it matters:** without it, a crash mid-work skips the cleanup line and leaks the resource (a
dangling TM1 session, an open file). The `with` guarantee means cleanup *always* happens. You've
used this already every time you wrote `with open(...) as f:`.

**In our code:** `TM1Client.__enter__` calls `connect()`; `__exit__` calls `close()`. So a TM1
connection is opened on entry and always logged out on exit — even if the code inside blows up.

---

## 24. Dependency injection

**What:** Passing a dependency *into* an object instead of having the object create it internally.
This lets you substitute a fake/stub in tests.

**Example:**
```python
# Real use: the client connects for real.
client = TM1Client(cfg)
# Test use: inject a fake service so no real TM1 is needed.
client = TM1Client(cfg, service=FakeService())
```

**Why it matters:** it's what makes code testable. All 12 `test_tm1_client.py` tests run with no
TM1py installed and no server, because a `FakeService` is injected. The production path (no
injection) opens a real connection.

**In our code:** the `service` parameter on `TM1Client.__init__`. Defaults to `None` (connect for
real); tests pass a fake.

---

## 25. Guard clauses

**What:** A check at the *start* of an operation that stops it early if conditions aren't met —
rather than burying the logic in nested `if`s.

**Example:**
```python
def ensure_writable(self, operation="write"):
    if self.dry_run:
        raise TM1ClientError(f"Refusing to {operation}: dry-run mode.")
    # ...otherwise fall through and allow the write
```

**Why it matters:** it enforces a rule consistently and loudly. Every future write calls
`ensure_writable()` first, so dry-run mode blocks *all* writes with one central, well-tested check —
no writer can forget. Note it **raises** (halts), it doesn't quietly return — so nothing downstream
wrongly assumes the write happened.

**In our code:** `TM1Client.ensure_writable` — the concrete realisation of the spec's dry-run mode.

---

## 26. Resource ownership & lifecycle

**What:** The principle that *whoever creates a resource is responsible for destroying it*. A piece
of code that merely *borrows* a resource must not close it.

**Why it matters:** if a borrower closes a shared resource, other users of it break. Tracking
ownership prevents "someone else already closed the connection I was using" bugs.

**In our code:** the `_owns_service` flag. `TM1Client` only logs out a service it opened itself
(`connect()`); a service *injected* by the caller is left alone. This is what lets a single TM1
connection be safely shared across several `TM1Client` uses in future, and it's exactly what the
test `test_injected_service_is_not_logged_out` verifies.
