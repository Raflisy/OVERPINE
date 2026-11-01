$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Python virtual environment not found. Run .\run.ps1 once to create it."
}

& .\.venv\Scripts\python.exe .\desktop.py
