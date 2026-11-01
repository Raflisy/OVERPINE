$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install -r .\requirements-desktop.txt
if ($LASTEXITCODE -ne 0) {
    throw "Installing desktop build dependencies failed."
}

& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean .\overpine.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$outputPath = Join-Path $PSScriptRoot "dist\OVERPINE\OVERPINE.exe"
if (-not (Test-Path $outputPath)) {
    throw "Build finished without the expected executable: $outputPath"
}
Write-Host "OVERPINE executable: $outputPath"
