# TM1 Data Dictionary

Native TM1 data dictionary for IBM Planning Analytics (on-premises v11.x).
Static parser + `}Meta_*` schema, TM1py-driven, zero external dependencies.

## Status

Phase 1 — under active development. See `docs/phase1_spec.docx` for the locked specification.

## Quickstart (development)

```powershell
# Clone
git clone https://github.com/zameelarif/tm1-data-dictionary.git
cd tm1-data-dictionary

# Create venv (Python 3.13)
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install with dev dependencies
pip install -e ".[dev]"

# Set up pre-commit hooks
pre-commit install

# Configure environment
copy .env.example .env
copy config.yaml.example config.yaml
# edit both files with your TM1 details

# Verify environment
python scripts\check_environment.py
```

## Project layout

See `docs/architecture.md` (coming soon).

## License

MIT — see LICENSE.
