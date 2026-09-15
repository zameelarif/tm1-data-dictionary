# TM1 Data Dictionary (`tm1dd`)

A command-line tool that reads every TurboIntegrator (TI) process in an IBM
Planning Analytics / TM1 model and builds a **living data dictionary** of how
the model actually fits together — which processes read and write which cubes,
which processes call each other, where data enters the model, what runs on a
schedule, and which processes maintain which dimensions and attributes.

The results are written back into TM1 as a set of `}Meta_*` cubes, so you can
slice the lineage in Planning Analytics for Excel (PAfE) just like any other
cube — and optionally exported as an interactive offline HTML data-flow map.

---

## Why this exists

When you inherit or audit a TM1 model, the hardest questions are always the
same:

- Where does the data come from?
- What runs automatically?
- What breaks if I change this cube?
- Which processes maintain this dimension?
- What calls this process?

Answering these by hand means opening dozens of TI processes and reading them
line by line. `tm1dd` answers them automatically by parsing the TI code once
and rolling the findings up into queryable lineage cubes.

---

## What it produces

| Cube | Question it answers |
|------|---------------------|
| `}Meta_Process_Cube` | Which processes read/write this cube? |
| `}Meta_Process_Chain` | Which processes call (trigger) which others? |
| `}Meta_Process_Datasource` | Where does data enter the model (file/ODBC/view)? |
| `}Meta_Chore_Process` | What runs on a schedule, and in what order? |
| `}Meta_Process_Dimension` | Which processes build/maintain a dimension or its attributes? |
| `}Meta_Extraction_Audit` | When was the dictionary last built, by whom, and with what result? |

---

## Quick start

```powershell
# 1. Store your TM1 password securely (once per environment)
tm1dd set-credential --name TM1_DEV_PWD

# 2. Create the }Meta_* schema in the target model
tm1dd bootstrap --env dev

# 3. Build the dictionary
tm1dd extract --env dev

# 4. (Optional) Export the interactive data-flow map
tm1dd export-graph --env dev --out data_flow.html
```

See **INSTALLATION_GUIDE.md** for full setup and **USER_GUIDE.md** for everyday
use.

---

## Key features

- **Parse-once, roll-up-many** — each process is read a single time and fed into
  every lineage type.
- **Const-propagation** — resolves variable cube/dimension targets (e.g.
  `cCube` → `'WeeklySales'`) where it is safe to do so.
- **Per-process error isolation** — one malformed process never aborts the run.
- **Exclusion rules** — Bedrock/utility, control (`}`-prefixed), and test/temp
  processes are excluded and recorded, never silently dropped.
- **Dry-run mode** — parse and report counts without writing anything.
- **Multiple environments in one config** — `--env dev`, `--env demo`, etc.
- **Audit trail** — every run is recorded (who, when, status, and row counts).

---

## Technology

- Python 3.13
- [TM1py](https://github.com/cubewise-code/tm1py) for TM1 REST access
- `click` (CLI), `pyyaml` + `python-dotenv` (config), `keyring` (secrets)
- `pre-commit` with `black`, `ruff`, and `mypy`

---

## Project layout

```
src/tm1_data_dictionary/
    bootstrap.py          Create }Meta_* schema (idempotent)
    chore_reader.py       Read chores + their task order
    cli.py                tm1dd command-line interface
    config.py             Load + validate config (multi-environment)
    credentials.py        Secret storage (keyring / env)
    exclusions.py         Which processes to skip
    extract.py            Whole-model extraction pipeline
    graph.py              Build + render the HTML data-flow map
    schema.py             }Meta_* dimension + cube definitions
    tm1_client.py         Connection wrapper (dry-run aware)
    parser/               TI parsing + lineage roll-ups
    writers/              Write each lineage type into its cube
```

---

## Status

Phase 1 (the lineage engine) is complete: cube, chain, datasource, chore, and
dimension/attribute lineage all extract and write successfully, with an audit
trail. See **BUILD_JOURNAL.md** for the development history and the current
backlog.
