# Installing tm1dd from this package

This package installs with no internet access. It needs **Windows x64** and the **Python
version in `VERSION.txt`** (3.13), installed so that `py -3.13` works.

Full documentation is in `docs\` – start with `INSTALLATION_GUIDE.md`.

## 1. Install or upgrade

Unzip the package anywhere, then in PowerShell:

```powershell
cd <unzipped folder>
powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallDir D:\tm1dd
```

The installer creates `D:\tm1dd\.venv` if needed, installs or upgrades tm1dd, and creates
`config.yaml` and `functions.txt` from the examples **only if they do not exist**. An
existing configuration is never overwritten.

## 2. First install only

```powershell
cd D:\tm1dd
.\.venv\Scripts\Activate.ps1
notepad config.yaml                          # host, port, user, password_env per instance
tm1dd set-credential --name <password_env entry>
tm1dd list-processes --env <name>            # proves connection and credentials
```

Keep `dry_run: true` until the dry-run extractions look right.

## 3. Bring the schema up to date (every install and upgrade)

```powershell
tm1dd bootstrap --env <name> --check         # read-only report
tm1dd bootstrap --env <name> --drop-legacy   # adds what is missing; deletes no data
```

Only if `--check` listed cubes as **REBUILD** (their data and views are deleted):

```powershell
tm1dd bootstrap --env <name> --rebuild-cube "<cube name>"
```

## 4. Refresh

```powershell
tm1dd extract --env <name>
tm1dd extract-rules --env <name>
tm1dd create-views --env <name>
```

Repeat steps 3–4 for each instance in `config.yaml`.
