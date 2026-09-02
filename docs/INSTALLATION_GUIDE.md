# TM1 Data Dictionary — Installation Guide

This guide gets the TM1 Data Dictionary (`tm1dd`) installed and connected to a TM1 /
Planning Analytics instance, from a clean machine.

---

## 1. Prerequisites

| Requirement | Version / notes |
|---|---|
| **Operating system** | Windows Server 2016+ / Windows 10+, or Linux (RHEL 7+, Ubuntu 18.04+) |
| **Python** | 3.10 or later (developed and tested on 3.13). 64-bit. |
| **TM1 / Planning Analytics** | On-premises v11.x (PAL 2.0.x). The REST API (HTTP/HTTPS) must be enabled. |
| **Network access to TM1** | The machine running `tm1dd` needs REST reachability to the TM1 server's HTTP(S) port. Off-server is fine and preferred. |
| **A TM1 user** | An account that can read processes/cubes/dimensions and create `}Meta_*` control objects. Admin or a suitably-privileged service account. |
| **Git** | Optional but recommended (for cloning/updating the repo). |
| **pip** | Comes with Python. |

**No internet is required at runtime** for the core tool. (The optional data-flow HTML map
fetches a graph library from a CDN on first open unless you inline it — see the User Guide.)

### Find your TM1 REST port
You need the **HTTP port** (the REST API port), not the classic client port. Check the
instance's `tm1s.cfg`:
- `HTTPPortNumber=NNNN`  ← this is the one `tm1dd` connects to
- `UseSSL=T` or `F`      ← tells you whether to use `ssl: true/false`

---

## 2. Get the code

Either clone the repository:

```powershell
git clone https://github.com/<your-org>/tm1-data-dictionary.git
cd tm1-data-dictionary
```

…or copy the project folder onto the machine and `cd` into it.

---

## 3. Create a virtual environment

A virtual environment keeps the tool's dependencies isolated from system Python.

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Linux / macOS (bash):**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

You should see `(.venv)` at the start of your prompt. Confirm the right Python:
```powershell
where.exe python      # Windows - should point inside .venv\Scripts
python --version      # should be 3.10+
```

> **PowerShell execution policy:** if activation is blocked, run once in that window:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`

---

## 4. Install the tool

From the project root, with the venv active:

```powershell
pip install -e ".[dev]"
```

This installs `tm1dd`, TM1py, and (with `[dev]`) the developer toolchain. It should end with
`Successfully installed tm1-data-dictionary-...`. If you only want to *run* the tool (not
develop it), `pip install -e .` is enough.

Confirm the CLI is registered:
```powershell
tm1dd --version
tm1dd --help
```

---

## 5. Configure the connection

Create your config from the templates:

```powershell
copy config.yaml.example config.yaml
copy .env.example .env
```

Edit **`config.yaml`** — the connection section names environment variables that hold the
actual values:

```yaml
connection:
  address_env: TM1_ADDRESS
  port_env: TM1_PORT
  ssl_env: TM1_SSL
  auth_mode: basic          # basic | cam | sso
  user_env: TM1_USER
  password_env: TM1_METADICT_PWD
  namespace_env: TM1_NAMESPACE

run:
  dry_run: false            # true = parse & report, write nothing

logs:
  enabled: true
  server_log_path_env: TM1_LOG_PATH
```

Edit **`.env`** — the real values (this file is gitignored, so secrets stay local):

```
TM1_ADDRESS=localhost
TM1_PORT=8010
TM1_SSL=true
TM1_USER=admin
TM1_NAMESPACE=
TM1_LOG_PATH=C:/path/to/tm1server.log
# TM1_METADICT_PWD - see the next step (prefer the keyring over putting it here)
```

---

## 6. Store the TM1 password securely (recommended)

Rather than leave the password in `.env` as plaintext, store it in the OS keyring
(Windows Credential Manager / macOS Keychain / Linux Secret Service):

```powershell
tm1dd set-credential
```

You'll be prompted (hidden input, entered twice). It's stored encrypted, tied to your OS
user. Then **delete the `TM1_METADICT_PWD` line from `.env`** — the tool will read it from
the keyring automatically.

> On unattended servers where a keyring isn't available, you can instead set
> `TM1_METADICT_PWD` as an environment variable (e.g. via the scheduler) — the tool falls
> back to that automatically.

---

## 7. Verify the environment

Run the built-in diagnostic (make sure the TM1 instance is running first):

```powershell
python scripts\check_environment.py
```

You want **all five checks PASS**:
- Python version
- Config files (config.yaml + .env loaded)
- TM1 connection (connects and reports the server name/version)
- TM1 permissions (reads processes/cubes; a scratch write test succeeds)
- Log file access

If **TM1 connection** fails, it's almost always the port or SSL flag — re-check
`HTTPPortNumber` / `UseSSL` in `tm1s.cfg` and match `TM1_PORT` / `TM1_SSL`.

---

## 8. First run

Create the dictionary schema, then populate it:

```powershell
tm1dd bootstrap      # creates the }Meta_* dimensions and cubes (idempotent)
tm1dd extract        # parses all business processes; writes cube + chain lineage
```

You're installed and running. See the **User Guide** for what each command does and how to
read the results.

---

## 9. Updating

```powershell
git pull                      # get the latest code
.venv\Scripts\Activate.ps1    # ensure the venv is active
pip install -e ".[dev]"       # reinstall in case dependencies changed
```

---

## Troubleshooting quick reference

| Symptom | Likely cause / fix |
|---|---|
| `tm1dd: command not found` | venv not active, or `pip install -e .` not run. Activate and reinstall. |
| TM1 connection FAIL | Wrong `TM1_PORT` / `TM1_SSL`. Match `tm1s.cfg` `HTTPPortNumber` / `UseSSL`. |
| `... can not be found in collection of type 'Cube'` | Run `tm1dd bootstrap` before `tm1dd extract`. |
| Password prompt every run / not found | Store it with `tm1dd set-credential`, or set `TM1_METADICT_PWD` in the environment. |
| PowerShell blocks `Activate.ps1` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` in that window. |
| Control objects (`}Meta_*`) not visible in PA | Enable "Display Control Objects" in Architect / PAW / Arc. |
