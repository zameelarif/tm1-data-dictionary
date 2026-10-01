# tm1-data-dictionary (`tm1dd`)

A native data dictionary for IBM Planning Analytics / TM1. `tm1dd` statically parses a TM1
model's TurboIntegrator (TI) processes, chores and cube rules, and writes what it finds
into TM1 itself, as a set of `}Meta_*` control cubes. Developers and administrators then
explore the model's lineage in PAfE, PAW or Architect, with no external database.

Nothing is executed on the TM1 server: processes are read, never run. No passwords or
sensitive data are stored in TM1 or in any file.

## What it answers

Two independent areas, each with its own command:

| Area | Command | Answers | Guide |
|---|---|---|---|
| **TI lineage** | `tm1dd extract` | Where does data come from? Which process writes this cube? What does this process trigger? Which chore runs it? Which processes maintain this dimension? | [TI lineage](docs/TI_LINEAGE.md) |
| **Rules analysis** | `tm1dd extract-rules` | Which cubes are rule-driven? Which cubes do these rules read from or feed? Is this element safe to rename or delete? Which rules use hierarchy or attribute functions? | [Rules analysis](docs/RULES_ANALYSIS.md) |

The two commands succeed or fail independently and can run on different schedules.

## Quick start

```powershell
tm1dd set-credential --name TM1_DEV_PWD   # store the password in the OS keyring, once
tm1dd bootstrap --env dev                 # create the }Meta_* cubes
tm1dd extract --env dev                   # TI lineage
tm1dd extract-rules --env dev             # rules analysis
tm1dd export-graph --env dev --out data_flow.html
```

## Documentation

| Document | Contents |
|---|---|
| [Installation guide](docs/INSTALLATION_GUIDE.md) | Developer setup, offline build and server install, configuration, credentials |
| [User guide](docs/USER_GUIDE.md) | Commands, dry-run, audit trail, common workflows, troubleshooting |
| [TI lineage](docs/TI_LINEAGE.md) | `tm1dd extract`: cubes, chains, datasources, chores, dimensions, functions |
| [Rules analysis](docs/RULES_ANALYSIS.md) | `tm1dd extract-rules`: rule facts, dependencies, element references, functions |
| [Schema reference](docs/SCHEMA_REFERENCE.md) | Every `}Meta_*` cube, dimension and measure |
| [Build journal](docs/BUILD_JOURNAL.md) | How the project was built, step by step, with reasons; current state and how to resume |
| [Learning log](docs/LEARNING_LOG.md) | The project on one page, then every concept used (Python, engineering, TM1, tooling) and playbooks |

## Requirements

- Python 3.13 (the version tested in deployment)
- TM1py 2.x and network access to the TM1 REST API
- A TM1 user able to read processes, chores and cubes, and (for writing) to create
  dimensions/cubes and write cells. Read-only use is possible in dry-run mode.

## Development

```powershell
pip install -e ".[dev]"
pre-commit run --all-files   # black, ruff, mypy (line length 100)
pytest -q
```

## Licence

MIT
