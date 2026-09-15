# Installation Guide — TM1 Data Dictionary (`tm1dd`)

This guide takes you from a clean machine to a working `tm1dd` install that can
build a data dictionary against a TM1 / Planning Analytics model.

It assumes you are on **Windows** with **PowerShell**, since that is the primary
development environment for this project. The steps are otherwise
platform-neutral.

---

## 1. Overview

`tm1dd` is a Python command-line tool. Installing it means:

1. Getting the code.
2. Creating an isolated Python environment.
3. Installing dependencies.
4. Telling it how to connect to your TM1 server(s).
5. Storing your TM1 password securely.
6. Creating the `}Meta_*` schema in the model.
7. Running your first extraction.

---

## 2. Prerequisites

| Requirement | Version / Notes |
|-------------|-----------------|
| Python | 3.13 (64-bit) |
| Git | Any recent version |
| VS Code | Recommended editor |
| TM1 / Planning Analytics | REST API (HTTP/HTTPS) reachable from your machine |
| A TM1 user | With rights to read TI processes/chores and create `}Meta_*` cubes |

Verify Python and Git:

```powershell
python --version
git --version
```

---

## 3. Get the code

```powershell
cd C:\TM1_Models
git clone <your-repo-url> tm1-data-dictionary
cd tm1-data-dictionary
```

---

## 4. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Your prompt should now start with `(.venv)`.

> If activation is blocked, run PowerShell as your user and set:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## 5. Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

The `-e` (editable) install means code changes take effect immediately. The
`[dev]` extra installs the tooling (`pre-commit`, `black`, `ruff`, `mypy`,
`pytest`).

Install the git hooks:

```powershell
pre-commit install
```

---

## 6. Configure your environment(s)

Configuration lives in **`config.yaml`** (structure) and **`.env`** (values and
secrets). The YAML never contains secrets directly — it names the environment
variables that hold each value.

### 6.1 `config.yaml`

`config.yaml` supports **multiple named environments** in one file:

```yaml
default_environment: dev

environments:
  dev:
    connection:
      address_env: TM1_DEV_ADDRESS
      port_env: TM1_DEV_PORT
      ssl_env: TM1_DEV_SSL
      auth_mode: basic
      user_env: TM1_DEV_USER
      password_env: TM1_DEV_PWD
      namespace_env: TM1_DEV_NAMESPACE
    run:
      dry_run: false
      max_requests_per_second: 20
    logs:
      enabled: true
      server_log_path_env: TM1_DEV_LOG_PATH
      copy_logs_locally: true

  demo:
    connection:
      address_env: TM1_DEMO_ADDRESS
      port_env: TM1_DEMO_PORT
      ssl_env: TM1_DEMO_SSL
      auth_mode: basic
      user_env: TM1_DEMO_USER
      password_env: TM1_DEMO_PWD
      namespace_env: TM1_DEMO_NAMESPACE
    run:
      dry_run: true
      max_requests_per_second: 20
    logs:
      enabled: true
      server_log_path_env: TM1_DEMO_LOG_PATH
      copy_logs_locally: true
```

- `default_environment` is used when you don't pass `--env`.
- Each environment names its **own** set of environment variables, so secrets
  never mix between servers.

> **Legacy single-block files still work.** If `config.yaml` has a top-level
> `connection:` block and no `environments:` section, it loads exactly as
> before, and `--env` is not accepted.

### 6.2 `.env`

Create a `.env` file next to `config.yaml` (it is git-ignored) with the values:

```dotenv
# --- dev ---
TM1_DEV_ADDRESS=localhost
TM1_DEV_PORT=8001
TM1_DEV_SSL=false
TM1_DEV_USER=admin
TM1_DEV_NAMESPACE=

# --- demo (fill in when ready) ---
TM1_DEMO_ADDRESS=demo-host
TM1_DEMO_PORT=8010
TM1_DEMO_SSL=true
TM1_DEMO_USER=svc_tm1dd
TM1_DEMO_NAMESPACE=
```

Do **not** put passwords in `.env` — use the keyring (next step).

---

## 7. Store your password securely

Passwords are stored in the OS keyring (Windows Credential Manager), keyed by
the `password_env` name for each environment:

```powershell
tm1dd set-credential --name TM1_DEV_PWD
tm1dd set-credential --name TM1_DEMO_PWD
```

You are prompted for the value; it is never echoed or written to a file.

---

## 8. Verify your environment

```powershell
tm1dd check
```

This runs the environment diagnostic. Resolve anything it flags before
continuing.

---

## 9. Bootstrap the schema

Create the `}Meta_*` dimensions and cubes in the target model. This is
idempotent — existing objects are left untouched.

```powershell
tm1dd bootstrap --env dev
```

You should see `created` / `exists` lines for each dimension and cube.

---

## 10. Run your first extraction

```powershell
tm1dd extract --env dev
```

A successful run ends with a summary like:

```
Extraction complete.
  Processes: 492 total, 117 included, 375 excluded
  Parsed OK: 117, failed: 0
  Cube-lineage rows: 73 written
  Chain-lineage rows: 105 written
  Datasource rows: 74 written
  Chore rows: 13 written
  Dimension rows: 58 written
  Run recorded in }Meta_Extraction_Audit (RunBy: <you> via <tm1_user>)
```

---

## 11. (Optional) Export the data-flow map

```powershell
tm1dd export-graph --env dev --out data_flow.html
```

For a fully offline file, download `vis-network.min.js` and pass it:

```powershell
tm1dd export-graph --env dev --out data_flow.html --vis-js .\vis-network.min.js
```

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Environment variable 'TM1_..._ADDRESS' is not set` | `.env` missing the per-env names | Add the variables for that environment to `.env` |
| `--env 'x' ... has no 'environments' section` | Legacy single-block `config.yaml` | Add an `environments:` block, or omit `--env` |
| `... can not be found in collection of type 'Cube'` | Schema not created on this server | Run `tm1dd bootstrap --env <name>` |
| `Audit record not written: ...` | Audit measures missing on an older model | The writer self-heals; if it persists, re-run `bootstrap` |
| Connection refused / timeout | Wrong host/port/SSL | Check the `.env` values and that the TM1 REST port is reachable |
| Pre-commit reformats files then fails | `black`/`ruff` auto-fixed | `git add -A` and re-run `pre-commit run --all-files` |

---

## 13. Running against a new (e.g. demo) server

1. Add a new environment block to `config.yaml`.
2. Add its variables to `.env`.
3. `tm1dd set-credential --name TM1_<ENV>_PWD`
4. `tm1dd bootstrap --env <name>`
5. `tm1dd extract --env <name>`

Consider setting `dry_run: true` for a new environment first, to parse and
report counts without writing anything.

---

## 14. Security notes

- Secrets live only in the OS keyring, never in `config.yaml` or `.env`.
- `config.yaml` names variables; `.env` holds non-secret values and is
  git-ignored.
- Use a dedicated, least-privilege TM1 service account where possible.
- Nothing about credentials is written to the audit cube.
