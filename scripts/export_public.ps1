<#
.SYNOPSIS
    Build a clean public copy of tm1-data-dictionary, with no history and no private files.

.DESCRIPTION
    Copies an allow-list of files and folders into a fresh destination folder, then scans
    every copied text file for the terms in internal\private_terms.txt. If any term is
    found, the export is reported as FAILED and must not be published.

    Nothing outside the allow-list is copied: internal\, config.yaml, .env, offline\,
    dist\, .venv\, data_flow.html and anything else are left behind.

.EXAMPLE
    .\scripts\export_public.ps1 -Destination C:\TM1_Models\tm1-data-dictionary-public
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

# ---- Allow-list: the ONLY things that become public ---------------------------------
$allow = @(
    "src",
    "tests",
    "docs",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    ".pre-commit-config.yaml",
    ".gitignore",
    ".github",
    "config.example.yaml",
    "functions.example.txt"
)

if (Test-Path $Destination) {
    $existing = Get-ChildItem -Force $Destination | Where-Object { $_.Name -ne ".git" }
    if ($existing) {
        throw "Destination '$Destination' is not empty (apart from .git). Use an empty folder."
    }
} else {
    New-Item -ItemType Directory -Path $Destination | Out-Null
}

foreach ($item in $allow) {
    $source = Join-Path $repo $item
    if (-not (Test-Path $source)) {
        Write-Host "  skip (not found)  $item"
        continue
    }
    Copy-Item -Path $source -Destination $Destination -Recurse -Force
    Write-Host "  copied            $item"
}

# Remove build and cache output that may sit inside allowed folders.
Get-ChildItem -Path $Destination -Recurse -Force -Directory |
    Where-Object { $_.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov") -or $_.Name -like "*.egg-info" } |
    Remove-Item -Recurse -Force

# ---- Scan for private terms -----------------------------------------------------------
$termsFile = Join-Path $repo "internal\private_terms.txt"
$terms = @()
if (Test-Path $termsFile) {
    $terms = Get-Content $termsFile |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
}

$hits = @()
if ($terms.Count -gt 0) {
    $files = Get-ChildItem -Path $Destination -Recurse -File |
        Where-Object { $_.Extension -in @(".py", ".md", ".toml", ".yaml", ".yml", ".txt", ".cfg", ".json", ".ps1") }
    foreach ($term in $terms) {
        $found = $files | Select-String -SimpleMatch -Pattern $term
        foreach ($f in $found) {
            $hits += "{0}:{1}  [{2}]" -f $f.Path.Substring($Destination.Length), $f.LineNumber, $term
        }
    }
}

Write-Host ""
if ($hits.Count -gt 0) {
    Write-Host "FAILED - private terms found. Fix them in the private repo and export again:" -ForegroundColor Red
    $hits | ForEach-Object { Write-Host "  $_" }
    exit 1
}

Write-Host "OK - clean public copy written to $Destination" -ForegroundColor Green
Write-Host "Next: cd `"$Destination`"; git init; git add -A; git commit -m `"Initial public release`""
