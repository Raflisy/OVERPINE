# OVERPINE launcher (PowerShell). Run from the project root:  .\run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..."
    python -m venv .venv
}

Write-Host "Installing dependencies..."
& .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

# Open the browser shortly after the server starts.
Start-Job { Start-Sleep -Seconds 2; Start-Process "http://127.0.0.1:8000" } | Out-Null

Write-Host "Starting OVERPINE at http://127.0.0.1:8000  (Ctrl+C to stop)"
& .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
