<#
.SYNOPSIS
    Install or upgrade tm1dd from this offline package. No internet access is needed.

.DESCRIPTION
    Run from the unzipped package folder, or point -InstallDir at the folder tm1dd should
    live in (for example D:\tm1dd). The script:

      1. Creates a Python virtual environment in <InstallDir>\.venv if there is none,
         using the Python version this package was built for.
      2. Installs or upgrades tm1dd from the offline\ folder.
      3. Copies config.example.yaml and functions.example.txt to config.yaml and
         functions.txt in <InstallDir> - only if those files do not exist yet.
         An existing config.yaml is never overwritten.

    It never connects to TM1 and never changes the }Meta_* cubes. After installing,
    run the schema check shown at the end.

.EXAMPLE
    .\install.ps1 -InstallDir D:\tm1dd
#>
param(
    [string]$InstallDir = $PSScriptRoot,
    [string]$PythonVersion = "3.13"
)

$ErrorActionPreference = "Stop"
$package = $PSScriptRoot
$offline = Join-Path $package "offline"

function Invoke-Checked {
    param([string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

if (-not (Test-Path $offline)) { throw "offline\ folder not found next to install.ps1." }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# ---- 1. Virtual environment ---------------------------------------------------------------
$venv = Join-Path $InstallDir ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment with Python $PythonVersion in $venv"
    Invoke-Checked "Creating the virtual environment (is Python $PythonVersion installed?)" {
        py "-$PythonVersion" -m venv $venv
    }
}
$actual = & $venvPython -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
if ($actual -ne $PythonVersion) {
    throw ("$venv uses Python $actual, but this package needs $PythonVersion. " +
        "Rename or delete $venv and run install.ps1 again.")
}

# ---- 2. Install / upgrade -------------------------------------------------------------------
$before = ""
if (Test-Path (Join-Path $venv "Scripts\tm1dd.exe")) {
    $before = & (Join-Path $venv "Scripts\tm1dd.exe") --version
}
& $venvPython -m pip install --no-index --find-links $offline --upgrade pip --quiet
Invoke-Checked "Installing tm1dd" {
    & $venvPython -m pip install --no-index --find-links $offline --upgrade tm1_data_dictionary
}
$after = & (Join-Path $venv "Scripts\tm1dd.exe") --version
if ($before) { Write-Host "Upgraded: $before  ->  $after" } else { Write-Host "Installed: $after" }

# ---- 3. Starter files (never overwritten) ----------------------------------------------------
foreach ($pair in @(@("config.example.yaml", "config.yaml"), @("functions.example.txt", "functions.txt"))) {
    $target = Join-Path $InstallDir $pair[1]
    if (Test-Path $target) {
        Write-Host "Kept existing $($pair[1])"
    } else {
        Copy-Item (Join-Path $package $pair[0]) $target
        Write-Host "Created $($pair[1]) from $($pair[0]) - edit it before first use"
    }
}

# ---- Next steps -------------------------------------------------------------------------------
Write-Host ""
Write-Host "Next steps (in $InstallDir):" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\Activate.ps1"
if (-not $before) {
    Write-Host "  tm1dd set-credential --name <keyring entry named in config.yaml>"
    Write-Host "  tm1dd list-processes --env <name>          # proves the connection"
}
Write-Host "  tm1dd bootstrap --env <name> --check       # read-only: what the schema needs"
Write-Host "Then follow INSTALL.md (bootstrap, extract, extract-rules, create-views)."
