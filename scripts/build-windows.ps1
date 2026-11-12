# Build the ROMLoom Windows bundle locally. Run from the repo root in PowerShell:
#   ./scripts/build-windows.ps1
# Produces backend/dist/romloom.exe. Requires Python 3.12 on PATH.
$ErrorActionPreference = "Stop"

Push-Location "$PSScriptRoot/../backend"
try {
    python -m venv .venv
    & .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    pip install -r requirements.txt pyinstaller
    pyinstaller romloom.spec
    Write-Host ""
    Write-Host "Built: backend/dist/romloom.exe" -ForegroundColor Green
    Write-Host "Copy romloom.toml.example next to it as romloom.toml and set your paths."
}
finally {
    Pop-Location
}
