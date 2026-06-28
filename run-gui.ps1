$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & .\install.ps1
}

$python = ".\.venv\Scripts\pythonw.exe"
if (-not (Test-Path $python)) {
    $python = ".\.venv\Scripts\python.exe"
}

Start-Process `
    -FilePath $python `
    -ArgumentList @(".\src\sts2_gui.py") `
    -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden

Write-Host "STS2 Drawbot GUI launched."
