param([string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)
$ErrorActionPreference = 'Stop'
# Read-only: does not process queues, send WhatsApps or synchronize inventory.
Push-Location $ProjectRoot
try {
    & (Join-Path $ProjectRoot 'venv\Scripts\python.exe') manage.py whatsapp_healthcheck
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
