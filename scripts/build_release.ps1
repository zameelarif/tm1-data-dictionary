<#
.SYNOPSIS
    Build the offline deployment package for tm1dd, for a chosen server Python version.
.DESCRIPTION
    Run on the developer machine (internet access, venv active), from the repository root.
    Only this machine needs internet; the server installs with no internet at all.
      1. Checks the version is the same in pyproject.toml and __init__.py.
      2. Runs the test suite (skip with -SkipTests).
      3. Builds the tm1dd wheel (pure Python - works on any 3.10+).
      4. Downloads every dependency as a Windows x64 wheel for -PythonVersion into offline\.
         If that Python is installed on this machine (python or py -X.Y), it does the
         download itself, so version-specific dependencies are resolved exactly as on the
         server. Otherwise the build stops, unless -CrossVersion is given (see below).
      5. Proves the bundle installs with no internet, in a throwaway venv made with the
         server's Python version.
      6. Assembles release\tm1dd-<version>-py<XY>\ with the installer, python-version.txt,
         example config, public docs and offline\, and zips it.
    -CrossVersion downloads for another Python version from this machine's Python using
    pip's --python-version/--platform options. pip then evaluates dependency markers for
    THIS machine's Python, so a dependency needed only on the server's version could be
    missed. The bundle is still checked for completeness, but it cannot be run here.
    Prefer installing the server's Python version on this machine (for example
    "winget install Python.Python.3.12") and building without -CrossVersion.
.EXAMPLE
    .\scripts\build_release.ps1                      # for Python 3.12 servers (default)
    .\scripts\build_release.ps1 -PythonVersion 3.13
    .\scripts\build_release.ps1 -SkipTests
#>
param(
    [string]$PythonVersion = "3.12",
    [switch]$SkipTests,
    [switch]$CrossVersion
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Invoke-Checked {
    param([string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

# Return the path of a 64-bit Python of the given version, or $null.
# No double quotes inside the Python code: Windows PowerShell strips them from arguments.
function Find-Python {
    param([string]$Version)
    $code = "import sys, struct; print('%d.%d %d %s' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P') * 8, sys.executable))"
    $tries = @(@{ Exe = (Get-Command python).Source; Prefix = @() })
    if (Get-Command py -ErrorAction SilentlyContinue) { $tries += @{ Exe = "py"; Prefix = @("-$Version") } }
    foreach ($t in $tries) {
        $old = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $prefix = $t.Prefix
            $out = & $t.Exe @prefix -c $code 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $ver, $bits, $exe = ($out | Select-Object -Last 1).Trim() -split ' ', 3
                if ($ver -eq $Version -and $bits -eq "64") { return $exe }
            }
        } catch {
        } finally {
            $ErrorActionPreference = $old
        }
    }
    return $null
}

if ($PythonVersion -notmatch '^3\.(\d+)$' -or [int]$Matches[1] -lt 10) {
    throw "-PythonVersion must be 3.10 or later, for example 3.12 (got '$PythonVersion')."
}
$short = $PythonVersion -replace '\.', ''

# ---- 1. Version -----------------------------------------------------------------------
$pyproject = Get-Content "pyproject.toml" -Raw
$init = Get-Content "src\tm1_data_dictionary\__init__.py" -Raw
$projectVersion = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
$codeVersion = [regex]::Match($init, '__version__\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $projectVersion) { throw "No version found in pyproject.toml." }
if ($projectVersion -ne $codeVersion) {
    throw "Version mismatch: pyproject.toml has $projectVersion, __init__.py has $codeVersion."
}
$version = $projectVersion
$python = (Get-Command python).Source
$targetPython = Find-Python $PythonVersion
if (-not $targetPython -and -not $CrossVersion) {
    throw ("Python $PythonVersion (64-bit) is not installed on this machine. Install it " +
        "(for example: winget install Python.Python.$PythonVersion), then run again. " +
        "Or add -CrossVersion to download without it (see Get-Help .\scripts\build_release.ps1 -Detailed).")
}
Write-Host "Building tm1dd $version for Python $PythonVersion servers"
if ($targetPython) { Write-Host "  dependencies resolved with $targetPython" }
else { Write-Host "  -CrossVersion: dependencies resolved with $python" -ForegroundColor Yellow }

# ---- 2. Tests -------------------------------------------------------------------------
if (-not $SkipTests) {
    Invoke-Checked "Tests" { & $python -m pytest -q }
}

# ---- 3. Wheel -------------------------------------------------------------------------
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
Invoke-Checked "Build" { & $python -m build --wheel }
$wheel = Get-ChildItem "dist\tm1_data_dictionary-$version-*.whl" | Select-Object -First 1
if (-not $wheel) { throw "Built wheel for version $version not found in dist\." }

# ---- 4. Offline bundle ----------------------------------------------------------------
$releaseName = "tm1dd-$version-py$short"
$releaseDir = Join-Path $repo "release\$releaseName"
$offline = Join-Path $releaseDir "offline"
Remove-Item -Recurse -Force $releaseDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $offline | Out-Null
Copy-Item $wheel.FullName $offline

if ($targetPython) {
    Invoke-Checked "Dependency download" {
        & $targetPython -m pip download $wheel.FullName --dest $offline --prefer-binary --disable-pip-version-check
    }
    Invoke-Checked "pip/setuptools/wheel download" {
        & $targetPython -m pip download pip setuptools wheel --dest $offline --prefer-binary --disable-pip-version-check
    }
    foreach ($sdist in Get-ChildItem "$offline\*.tar.gz", "$offline\*.zip" -ErrorAction SilentlyContinue) {
        Write-Host "  converting source archive to wheel: $($sdist.Name)"
        Invoke-Checked "Wheel build for $($sdist.Name)" {
            & $targetPython -m pip wheel $sdist.FullName --no-deps --wheel-dir $offline --disable-pip-version-check
        }
        Remove-Item $sdist.FullName
    }
} else {
    $cross = @("--python-version", $PythonVersion, "--platform", "win_amd64",
        "--implementation", "cp", "--only-binary=:all:", "--disable-pip-version-check")
    Invoke-Checked "Dependency download (cross-version)" {
        & $python -m pip download $wheel.FullName --dest $offline @cross
    }
    Invoke-Checked "pip/setuptools/wheel download (cross-version)" {
        & $python -m pip download pip setuptools wheel --dest $offline @cross
    }
}
$left = Get-ChildItem $offline -File | Where-Object { $_.Extension -ne ".whl" }
if ($left) { throw "offline\ still contains non-wheel files: $($left.Name -join ', ')" }
$wrong = Get-ChildItem $offline -Filter *.whl |
    Where-Object { $_.Name -match '-cp3(\d+)-' -and "3.$($Matches[1])" -ne $PythonVersion -and $_.Name -notmatch 'abi3' }
if ($wrong) { throw "Wheels for the wrong Python version in offline\: $($wrong.Name -join ', ')" }

# ---- 5. Prove it installs offline ------------------------------------------------------
if ($targetPython) {
    $testVenv = Join-Path $env:TEMP "tm1dd-release-check"
    Remove-Item -Recurse -Force $testVenv -ErrorAction SilentlyContinue
    Invoke-Checked "Test venv" { & $targetPython -m venv $testVenv }
    $testPython = Join-Path $testVenv "Scripts\python.exe"
    Invoke-Checked "Offline install test" {
        & $testPython -m pip install --no-index --find-links $offline "tm1_data_dictionary==$version" --quiet --disable-pip-version-check
    }
    $installed = & (Join-Path $testVenv "Scripts\tm1dd.exe") --version
    if ($installed -notmatch [regex]::Escape($version)) {
        throw "Installed tm1dd reports '$installed', expected $version."
    }
    Remove-Item -Recurse -Force $testVenv
    Write-Host "  offline install verified with Python $PythonVersion`: $installed"
} else {
    # Cannot run Python $PythonVersion here: resolve the full dependency set offline for it.
    $target = Join-Path $env:TEMP "tm1dd-release-target"
    Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
    Invoke-Checked "Offline resolution check for Python $PythonVersion" {
        & $python -m pip install --no-index --find-links $offline "tm1_data_dictionary==$version" --target $target --quiet @cross
    }
    Remove-Item -Recurse -Force $target
    Write-Host "  offline resolution verified for Python $PythonVersion (not run - -CrossVersion)" -ForegroundColor Yellow
}

# ---- 6. Assemble and zip ------------------------------------------------------------------
Copy-Item "packaging\install.ps1" $releaseDir
Copy-Item "packaging\INSTALL.md" $releaseDir
Copy-Item "config.example.yaml" $releaseDir
Copy-Item "functions.example.txt" $releaseDir
$docs = Join-Path $releaseDir "docs"
New-Item -ItemType Directory -Path $docs | Out-Null
Copy-Item "docs\*.md" $docs
Set-Content -Path (Join-Path $releaseDir "python-version.txt") -Encoding ascii -Value $PythonVersion
$how = if ($targetPython) { "verified with Python $PythonVersion" } else { "cross-version build (not run)" }
Set-Content -Path (Join-Path $releaseDir "VERSION.txt") -Encoding ascii -Value @(
    "tm1dd $version",
    "Built $(Get-Date -Format 'yyyy-MM-dd HH:mm') - $how",
    "Requires Windows x64 and 64-bit Python $PythonVersion on the server. No internet needed."
)
$zip = Join-Path $repo "release\$releaseName.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $releaseDir -DestinationPath $zip
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash
Write-Host ""
Write-Host "Release ready: $zip" -ForegroundColor Green
Write-Host "  for:    Windows x64, Python $PythonVersion"
Write-Host "  wheels: $((Get-ChildItem $offline -Filter *.whl).Count)"
Write-Host "  SHA256: $hash"
Write-Host "Copy the zip to the server and follow INSTALL.md inside it."
