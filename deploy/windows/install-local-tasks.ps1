param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [switch]$ActivateWorker
)
$ErrorActionPreference = 'Stop'
if (-not $ActivateWorker) { throw 'Explicit -ActivateWorker is required: this starts real queue processing.' }
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$RuntimeDirectory = (Resolve-Path $RuntimeDirectory).Path
$python = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
$launcher = Join-Path $ProjectRoot 'deploy\local_service.py'
$logs = Join-Path $RuntimeDirectory 'logs'
$receipt = Join-Path $RuntimeDirectory 'local-tasks.json'
if (-not (Test-Path $logs -PathType Container)) { throw 'Create a private logs directory first.' }
if (Test-Path $receipt) { throw 'Refusing to overwrite an existing deployment receipt.' }
Push-Location $ProjectRoot
try {
    $raw = & $python manage.py shell -c "import json; from applications.whatsapp.models import WhatsAppMessage, Outbox; print(json.dumps({'incoming': WhatsAppMessage.objects.filter(status__in=['pending','processing'], is_from_customer=True).count(), 'outgoing': Outbox.objects.filter(sent=False, failed_at__isnull=True).count()}))"
    if ($LASTEXITCODE -ne 0) { throw 'Queue preflight failed.' }
    $queues = $raw | ConvertFrom-Json
    if ($queues.incoming -gt 0 -or $queues.outgoing -gt 0) { throw 'Existing queues require explicit human review before startup. No tasks were created.' }
    $account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
    $names = @('Chatbot2026-Local-Web', 'Chatbot2026-Local-WhatsAppWorker', 'Chatbot2026-Local-WorkerHealth')
    foreach ($name in $names) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw ('Existing task will not be overwritten: ' + $name) }
    }
    $created = @()
    try {
        foreach ($role in @('web', 'worker', 'health')) {
            $index = @('web', 'worker', 'health').IndexOf($role)
            $action = New-ScheduledTaskAction -Execute $python -WorkingDirectory $ProjectRoot -Argument ('"' + $launcher + '" ' + $role + ' --log-dir "' + $logs + '"')
            if ($role -eq 'health') {
                $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1)
                $options = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
            } else {
                $trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
                $options = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            }
            Register-ScheduledTask -TaskName $names[$index] -Action $action -Trigger $trigger -Settings $options -Principal $principal -Description ('Chatbot2026 local-only ' + $role + '; no inventory synchronization.') | Out-Null
            $created += $names[$index]
        }
    } catch {
        foreach ($name in $created) { Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Continue }
        throw
    }
    $result = [pscustomobject]@{account=$account; scope='current-user local session'; tasks=$created; log_directory=$logs; url='http://127.0.0.1:8000'; inventory_synchronized=$false}
    $result | ConvertTo-Json -Depth 4 | Set-Content -Path $receipt -Encoding UTF8
    Start-ScheduledTask -TaskName $names[1]
    Start-ScheduledTask -TaskName $names[0]
    Start-ScheduledTask -TaskName $names[2]
    $result | ConvertTo-Json -Depth 4
} finally {
    Pop-Location
}
