# One-time setup for Windows PowerShell. Cross-platform equivalents are listed in README.md.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) { python -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e "apps/api[dev]"

Push-Location apps\web
pnpm install --ignore-workspace
Pop-Location

if (-not (Test-Path ".env")) { Copy-Item .env.example .env; Write-Host "Created .env - add MODEL_API_KEY for Real Mode." }
.\.venv\Scripts\python scripts\diagnose.py
