param([ValidateRange(1024, 65535)][int]$Port = 8502)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create .venv and install requirements.txt first. See README.md.'
}
Push-Location -LiteralPath $projectDirectory
try {
    & $projectPython -m streamlit run (Join-Path $projectDirectory 'app.py') --server.address 127.0.0.1 --server.port $Port --browser.gatherUsageStats false
} finally {
    Pop-Location
}
