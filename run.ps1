# OVERPINE launcher (PowerShell). Run from the project root:  .\run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Starting OVERPINE with the Python standard library..."
python -m backend
