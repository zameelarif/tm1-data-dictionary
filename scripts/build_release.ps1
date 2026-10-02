<#
.SYNOPSIS
    Build the offline deployment package for tm1dd.

.DESCRIPTION
    Run on the developer machine (internet access, venv active), from the repository root.

      1. Checks the version is the same in pyproject.toml and __init__.py.
      2. Runs the test suite (skip with -SkipTests).
      3. Builds the wheel.
      4. Downloads every dependency as a wheel into offline\ (source archives are
         converted to wheels; the build fails if any cannot be).
      5. Proves the bundle installs with no internet, in a throwaway venv.
      6. Assembles release\tm1dd-<version>\ with the installer, example config,
         public docs and offline\, and zips it.

    The wheels are built for this machine's platform and Python version. The server must
    match: Windows x64 and the same Python minor version (3.13).

.EXAMPLE
    .\scripts\build_release.ps1
    .\scripts\build_release.ps1 -SkipTests
#>
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Invoke-Checked {
    param([string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

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
$pyVersion = & $python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
Write-Host "Building tm1dd $version with Python $pyVersion ($python)"

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
$releaseName = "tm1dd-$version"
$releaseDir = Join-Path $repo "release\$releaseName"
$offline = Join-Path $releaseDir "offline"
Remove-Item -Recurse -Force $releaseDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $offline | Out-Null

Copy-Item $wheel.FullName $offline
Invoke-Checked "Dependency download" { & $python -m pip download $wheel.FullName --dest $offline }
Invoke-Checked "pip/setuptools/wheel download" {
    & $python -m pip download pip setuptools wheel --dest $offline
}

foreach ($sdist in Get-ChildItem "$offline\*.tar.gz", "$offline\*.zip" -ErrorAction SilentlyContinue) {
    Write-Host "  converting source archive to wheel: $($sdist.Name)"
    Invoke-Checked "Wheel build for $($sdist.Name)" {
        & $python -m pip wheel $sdist.FullName --no-deps --wheel-dir $offline
    }
    Remove-Item $sdist.FullName
}
$left = Get-ChildItem $offline -File | Where-Object { $_.Extension -ne ".whl" }
if ($left) { throw "offline\ still contains non-wheel files: $($left.Name -join ', ')" }

# ---- 5. Prove it installs offline ------------------------------------------------------
$testVenv = Join-Path $env:TEMP "tm1dd-release-check"
Remove-Item -Recurse -Force $testVenv -ErrorAction SilentlyContinue
Invoke-Checked "Test venv" { & $python -m venv $testVenv }
$testPython = Join-Path $testVenv "Scripts\python.exe"
Invoke-Checked "Offline install test" {
    & $testPython -m pip install --no-index --find-links $offline "tm1_data_dictionary==$version" --quiet
}
$installed = & (Join-Path $testVenv "Scripts\tm1dd.exe") --version
if ($installed -notmatch [regex]::Escape($version)) {
    throw "Installed tm1dd reports '$installed', expected $version."
}
Remove-Item -Recurse -Force $testVenv
Write-Host "  offline install verified: $installed"

# ---- 6. Assemble and zip ------------------------------------------------------------------
Copy-Item "packaging\install.ps1" $releaseDir
Copy-Item "packaging\INSTALL.md" $releaseDir
Copy-Item "config.example.yaml" $releaseDir
Copy-Item "functions.example.txt" $releaseDir
$docs = Join-Path $releaseDir "docs"
New-Item -ItemType Directory -Path $docs | Out-Null
Copy-Item "docs\*.md" $docs
Set-Content -Path (Join-Path $releaseDir "VERSION.txt") -Encoding ascii -Value @(
    "tm1dd $version",
    "Built $(Get-Date -Format 'yyyy-MM-dd HH:mm') with Python $pyVersion",
    "Requires Windows x64 and Python $pyVersion on the server."
)

$zip = Join-Path $repo "release\$releaseName.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $releaseDir -DestinationPath $zip
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash

Write-Host ""
Write-Host "Release ready: $zip" -ForegroundColor Green
Write-Host "  wheels: $((Get-ChildItem $offline -Filter *.whl).Count)"
Write-Host "  SHA256: $hash"
Write-Host "Copy the zip to the server and follow INSTALL.md inside it."
