param([string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)
$ErrorActionPreference = 'Stop'
# This command sends queued WhatsApps. It never synchronizes the inventory.
Push-Location $ProjectRoot
try {
    $env:PYTHONUNBUFFERED = '1'
    & (Join-Path $ProjectRoot 'venv\Scripts\python.exe') manage.py whatsapp_worker
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
