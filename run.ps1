param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ArgsForDrawbot
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & .\install.ps1
}

& .\.venv\Scripts\python.exe .\src\sts2_drawbot.py @ArgsForDrawbot
