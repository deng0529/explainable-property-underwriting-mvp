param([switch]$Smoke)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create .venv and install requirements.txt first. See README.md.'
}
Push-Location -LiteralPath $projectDirectory
try {
    & $projectPython -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Regression checks failed.' }
    if ($Smoke) {
        & $projectPython (Join-Path $PSScriptRoot 'smoke_test.py')
        if ($LASTEXITCODE -ne 0) { throw 'UI/document smoke check failed.' }
    }
} finally {
    Pop-Location
}
