# Installation guide

This guide covers three setups: a developer machine, building an offline bundle, and
installing on a TM1 server with no internet access. It ends with configuration and
credentials, which are the same everywhere.

---

## 1. Developer machine (has internet)

```powershell
cd C:\TM1_Models\tm1-data-dictionary
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pre-commit install
pytest -q
```

`pip install -e` makes `tm1dd` run directly from `src\`, so code changes take effect
without reinstalling.

---

## 2. Build the offline bundle (on the developer machine)

Every new release needs a version bump, otherwise the server's `--upgrade` will not
replace the installed wheel. Bump the version in **both** places so `tm1dd --version`
matches the wheel:

- `pyproject.toml` → `version = "x.y.z"`
- `src\tm1_data_dictionary\__init__.py` → `__version__ = "x.y.z"`

Then build:

```powershell
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
python -m build
copy dist\tm1_data_dictionary-*.whl offline\
pip download . --dest offline
pip download pip setuptools wheel --dest offline
```

Everything in `offline\` must be a wheel (`.whl`). Check for source archives and convert
any you find:

```powershell
Get-ChildItem offline\*.tar.gz
pip wheel <package-name> --no-deps --wheel-dir offline
Remove-Item offline\<package-name>-*.tar.gz
```

Prove the bundle installs without internet:

```powershell
py -3.13 -m venv C:\TM1_Models\testvenv
C:\TM1_Models\testvenv\Scripts\Activate.ps1
pip install --no-index --find-links=offline tm1_data_dictionary
tm1dd --version
deactivate
Remove-Item -Recurse -Force C:\TM1_Models\testvenv
```

---

## 3. Server install (no internet)

### First install

1. Copy the whole `offline\` folder and a `config.yaml` to the target folder, for example
   `D:\tm1dd\`.
2. Create the virtual environment with Python 3.13. If the server has several Python
   versions, name it explicitly; the wheels are built for 3.13.

   ```powershell
   cd D:\tm1dd
   py -3.13 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python --version
   ```

3. Install:

   ```powershell
   pip install --no-index --find-links=.\offline --upgrade pip
   pip install --no-index --find-links=.\offline tm1_data_dictionary
   tm1dd --version
   ```

### Upgrading

Copy only the new `tm1_data_dictionary-x.y.z-py3-none-any.whl` into `offline\` (dependency
wheels rarely change), delete the old `tm1_data_dictionary` wheel, then:

```powershell
.\.venv\Scripts\Activate.ps1
pip install --no-index --find-links=.\offline --upgrade tm1_data_dictionary
tm1dd --version
tm1dd bootstrap --env <name>     # creates any cubes added by the new release
```

**Upgrading from schema 1.5 or earlier:** three cubes were renamed in 1.6 so that TI cubes
and rules cubes sort together. After upgrading, run:

```powershell
tm1dd bootstrap --env <name> --drop-legacy
tm1dd extract --env <name>
tm1dd extract-rules --env <name>
```

`--drop-legacy` deletes the old cubes (`}Meta_Chore_Process`, `}Meta_Unresolved_Reference`,
`}Meta_Cube_Rule_Dependency`); the next extractions fill the new ones. Dimensions are
unchanged. Any saved views or reports on the old cube names must be re-pointed.

If `tm1dd --version` shows the old number, check `pip show tm1_data_dictionary`. If pip
has the new version, only `__init__.py` was not bumped.

---

## 4. Configuration (`config.yaml`)

`tm1dd` looks for `config.yaml` in the current folder; use `--config <path>` to point
elsewhere. One file can describe many TM1 instances. A `defaults:` block holds shared
settings; each named environment only states what differs, usually just the port.

```yaml
default_environment: dev

defaults:
  connection:
    address: tm1server01
    ssl: true
    auth_mode: basic
    user: TM1_SERVICE_USER
    password_env: TM1_DEV_PWD     # keyring entry NAME - never the password
    namespace: ""
  run:
    dry_run: true                 # set false when ready to write
    max_requests_per_second: 20
  logs:
    enabled: true
    copy_logs_locally: true

environments:
  dev:
    connection:
      port: 10019
  test:
    connection:
      port: 10020
```

Rules:

- Pick an environment with `--env <name>`; without it, `default_environment` is used.
- Each connection field can be a literal (`address: tm1server01`) or the name of an
  environment variable (`address_env: TM1_ADDRESS`). A literal wins if both are given.
  A `.env` file is optional and only read if present.
- The TM1 instance is reached by host and port; the environment name is just a label.
- **The password never goes in `config.yaml`.** `password_env` names the keyring entry.
  Never leave it blank.

---

## 5. Credentials

Store the password once per keyring entry, on each machine that runs `tm1dd`:

```powershell
tm1dd set-credential --name TM1_DEV_PWD
```

You are prompted twice; nothing is echoed or written to disk. On Windows the secret is
held in Credential Manager under your user account, so run `set-credential` as the same
Windows user that will run `tm1dd` (including any scheduled task account). Instances that
share a TM1 user can share one keyring entry.

---

## 6. First run checklist

```powershell
tm1dd list-processes --env dev            # proves connection and credentials
tm1dd extract --env dev --quiet           # dry-run: parses, writes nothing
tm1dd extract-rules --env dev --quiet     # dry-run
```

When the dry-run counts look right, set `dry_run: false` for that environment, then:

```powershell
tm1dd bootstrap --env dev
tm1dd extract --env dev
tm1dd extract-rules --env dev
```
