$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & .\install.ps1
}

& .\.venv\Scripts\python.exe .\src\sts2_gui.py
