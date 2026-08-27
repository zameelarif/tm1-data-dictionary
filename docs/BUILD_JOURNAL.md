<!--
    APPEND THIS to the bottom of docs/BUILD_JOURNAL.md
    (paste it just above the final "*End of journal...*" line).
    It replaces the previous "Current State" block with an updated one.
-->

## E6. `tm1_client.py` - the TM1 connection wrapper

- **What:** Built `TM1Client`, a thin wrapper around a TM1py `TM1Service`, plus a 12-case unit test
  suite (`test_tm1_client.py`). Every part of the extractor that talks to TM1 will go through this
  client rather than constructing a service directly.
- **Why:** Centralising the connection in one tested place removes the duplicated, scattered
  `os.getenv` + `TM1Service(...)` logic (which currently lives in `check_environment.py`), and gives
  us one place to enforce cross-cutting behaviour (dry-run, clean shutdown, error wrapping). This is
  the DRY principle - connection logic written and tested once.
- **Design choices:**
  - **Built from `AppConfig`:** the client takes the validated config object, so connection details
    are never loose env reads. Password flows through the config's credential provider (keyring/env).
  - **Context manager:** `with TM1Client(cfg) as client:` opens the connection on entry and always
    logs out on exit - even if the body raises. No leaked TM1 sessions.
  - **Dry-run guard:** `ensure_writable()` raises in dry-run mode. Higher layers call it before any
    write, so dry-run consistently blocks all writes while still allowing reads - the concrete
    realisation of the spec's dry-run mode (Section 2.4).
  - **Lazy import + dependency injection:** TM1py is imported inside `connect()` (so the module loads
    even where TM1py isn't installed), and a service can be injected for tests. This is why all 12
    tests run with no TM1py and no live server.
  - **Ownership tracking:** `_owns_service` ensures the client only logs out a connection it opened
    itself; an injected/shared service is left for its owner to close.
  - **Uniform error type:** connection failures and disallowed operations both raise `TM1ClientError`
    with a clear message.
- **Toolchain note:** ruff flagged two idiom improvements during development - an unused
  `TYPE_CHECKING` import and a `try/except/pass` that should be `contextlib.suppress`. Both fixed
  before handover, so pre-commit stays clean.
- **Result:** `tm1_client.py` + `test_tm1_client.py` complete; 12/12 tests green; ruff/black/mypy
  clean. The extractor can now open, use, and cleanly close a TM1 connection in a dry-run-aware way.

## Current State (updated)

- Foundation complete: config, credentials (with encrypted keyring storage proven end-to-end), and
  now a tested TM1 connection wrapper. Diagnostic passes all five checks against the local PA using
  the keyring for the password.
- **Next planned step:** the first `}Meta_*` object end-to-end - create and populate
  `}Meta_Extraction_Audit` via a small bootstrap/writer that uses `TM1Client`. This proves the whole
  write path (config -> client -> create schema -> write cells -> read back) on a single, simplest
  cube before we widen to the full schema and the parser.
