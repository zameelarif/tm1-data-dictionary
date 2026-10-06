<#
.SYNOPSIS
    Install or upgrade tm1dd from this offline package. No internet access is needed.
.DESCRIPTION
    Run from the unzipped package folder, or point -InstallDir at the folder tm1dd should
    live in (for example D:\tm1dd). The script:
      1. Finds a 64-bit Python matching the version this package was built for
         (python-version.txt). It looks, in order, at: -Python, the Python launcher (py),
         the Python behind an existing <InstallDir>\.venv, python on the PATH, and the
         usual install folders.
      2. Proves the package installs, in a throwaway environment in %TEMP%, before touching
         anything in <InstallDir>. If that fails, the existing install is left as it is.
      3. Installs or upgrades tm1dd in <InstallDir>\.venv. If the existing .venv was made
         with a different Python version, it is renamed to .venv.previous (for rollback)
         and a new .venv is created.
      4. Copies config.example.yaml and functions.example.txt to config.yaml and
         functions.txt in <InstallDir> - only if those files do not exist yet.
         An existing config.yaml is never overwritten.
    pip always runs with --no-index: nothing is ever downloaded. The script never connects
    to TM1 and never changes the }Meta_* cubes.
.EXAMPLE
    .\install.ps1 -InstallDir D:\tm1dd
.EXAMPLE
    .\install.ps1 -InstallDir D:\tm1dd -Python "C:\Program Files\Python312\python.exe"
.NOTES
    Rollback after a version change: delete <InstallDir>\.venv and rename .venv.previous
    back to .venv.
#>
param(
    [string]$InstallDir = $PSScriptRoot,
    [string]$Python = "",
    [string]$PythonVersion = ""
)
$ErrorActionPreference = "Stop"
$package = $PSScriptRoot
$offline = Join-Path $package "offline"

function Invoke-Checked {
    param([string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

# Run a Python and return "<major>.<minor> <bits> <base_prefix>", or $null if it cannot run.
# No double quotes inside the Python code: Windows PowerShell strips them from arguments.
function Get-PythonInfo {
    param([string]$Exe, [string[]]$Prefix = @())
    $code = "import sys, struct; print('%d.%d %d %s' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P') * 8, sys.base_prefix))"
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $Exe @Prefix -c $code 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        return ($out | Select-Object -Last 1).Trim()
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $old
    }
}

if (-not (Test-Path $offline)) { throw "offline\ folder not found next to install.ps1." }

# ---- Which Python does this package need? ---------------------------------------------------
if (-not $PythonVersion) {
    $marker = Join-Path $package "python-version.txt"
    if (Test-Path $marker) {
        $PythonVersion = (Get-Content $marker | Select-Object -First 1).Trim()
    } else {
        $tag = Get-ChildItem $offline -Filter "*-cp3*-win_amd64.whl" | Select-Object -First 1
        if ($tag -and $tag.Name -match "-cp3(\d+)-") { $PythonVersion = "3.$($Matches[1])" }
    }
}
if ($PythonVersion -notmatch '^3\.\d+$') {
    throw "Cannot tell which Python this package needs. Pass -PythonVersion (for example 3.12)."
}
Write-Host "This package needs 64-bit Python $PythonVersion."

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$venv = Join-Path $InstallDir ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

# ---- 1. Find a matching base Python ---------------------------------------------------------
$candidates = New-Object System.Collections.Generic.List[object]
function Add-Candidate {
    param([string]$Exe, [string[]]$Prefix, [string]$Label)
    $candidates.Add([pscustomobject]@{ Exe = $Exe; Prefix = $Prefix; Label = $Label })
}
if ($Python) { Add-Candidate $Python @() "-Python" }
if (Get-Command py -ErrorAction SilentlyContinue) {
    Add-Candidate "py" @("-$PythonVersion") "Python launcher (py -$PythonVersion)"
}
$cfg = Join-Path $venv "pyvenv.cfg"
if (Test-Path $cfg) {
    $homeLine = Get-Content $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
    if ($homeLine) {
        $venvHome = ($homeLine -split '=', 2)[1].Trim()  # not $home: that is read-only
        Add-Candidate (Join-Path $venvHome "python.exe") @() "existing .venv's base Python"
    }
}
foreach ($name in @("python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { Add-Candidate $cmd.Source @() "$name on PATH" }
}
$short = $PythonVersion -replace '\.', ''
foreach ($dir in @(
        "$env:LOCALAPPDATA\Programs\Python\Python$short",
        "$env:ProgramFiles\Python$short",
        "C:\Python$short")) {
    Add-Candidate (Join-Path $dir "python.exe") @() $dir
}

$basePython = $null
$seen = @()
foreach ($c in $candidates) {
    $exe = $c.Exe; $prefix = $c.Prefix; $label = $c.Label
    if ($exe -ne "py" -and -not (Test-Path $exe)) { continue }
    $info = Get-PythonInfo -Exe $exe -Prefix $prefix
    if (-not $info) { continue }
    $ver, $bits, $base = $info -split ' ', 3
    $seen += "$ver ($bits-bit) via $label"
    if ($ver -eq $PythonVersion -and $bits -eq "64") {
        # Always build from the base install, never from inside another venv.
        $basePython = Join-Path $base "python.exe"
        if (-not (Test-Path $basePython)) { $basePython = $exe }
        Write-Host "Using Python $ver from $basePython ($label)"
        break
    }
}
if (-not $basePython) {
    $found = if ($seen) { ($seen | Select-Object -Unique) -join "; " } else { "none" }
    throw ("No 64-bit Python $PythonVersion found on this server. Found: $found. " +
        "Either install Python $PythonVersion (offline installer), pass -Python <path to python.exe>, " +
        "or build a package for a version this server has, on the build machine: " +
        ".\scripts\build_release.ps1 -PythonVersion <version>")
}

# ---- 2. Prove the package installs, without touching InstallDir ------------------------------
$check = Join-Path $env:TEMP "tm1dd-install-check"
Remove-Item -Recurse -Force $check -ErrorAction SilentlyContinue
Invoke-Checked "Creating the check environment" { & $basePython -m venv $check }
$checkPython = Join-Path $check "Scripts\python.exe"
Invoke-Checked "Offline install check" {
    & $checkPython -m pip install --no-index --find-links $offline tm1_data_dictionary --quiet --disable-pip-version-check
}
$newVersion = & (Join-Path $check "Scripts\tm1dd.exe") --version
if ($LASTEXITCODE -ne 0) { throw "tm1dd did not start in the check environment." }
Remove-Item -Recurse -Force $check -ErrorAction SilentlyContinue
Write-Host "Package verified: $newVersion"

# ---- 3. Install / upgrade in InstallDir -------------------------------------------------------
$before = ""
if (Test-Path $venvPython) {
    $existing = Get-PythonInfo -Exe $venvPython
    $existingVer = if ($existing) { ($existing -split ' ')[0] } else { "unknown" }
    if ($existingVer -ne $PythonVersion) {
        $previous = Join-Path $InstallDir ".venv.previous"
        Remove-Item -Recurse -Force $previous -ErrorAction SilentlyContinue
        Rename-Item $venv ".venv.previous"
        Write-Host "Existing .venv uses Python $existingVer - kept as .venv.previous for rollback."
    } elseif (Test-Path (Join-Path $venv "Scripts\tm1dd.exe")) {
        $before = & (Join-Path $venv "Scripts\tm1dd.exe") --version
    }
}
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment with Python $PythonVersion in $venv"
    Invoke-Checked "Creating the virtual environment" { & $basePython -m venv $venv }
}
& $venvPython -m pip install --no-index --find-links $offline --upgrade pip --quiet --disable-pip-version-check
Invoke-Checked "Installing tm1dd" {
    & $venvPython -m pip install --no-index --find-links $offline --upgrade tm1_data_dictionary --disable-pip-version-check
}
$after = & (Join-Path $venv "Scripts\tm1dd.exe") --version
if ($before) { Write-Host "Upgraded: $before  ->  $after" } else { Write-Host "Installed: $after" }

# ---- 4. Starter files (never overwritten) ----------------------------------------------------
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
    Write-Host "  tm1dd set-credential --name <keyring entry named in config.yaml>   # only if not set before"
}
Write-Host "  tm1dd check --env <name>                  # Python, config, connection, permissions"
Write-Host "  tm1dd bootstrap --env <name> --check       # read-only: what the schema needs"
Write-Host "Then follow INSTALL.md (bootstrap, extract, extract-rules, extract-elements, create-views)."
