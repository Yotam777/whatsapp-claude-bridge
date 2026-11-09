# Stops the bridge and the agent on this PC and packs everything personal (WhatsApp session + history, transcripts,
# settings, Claude's notes) into whatsapp-claude-export.zip on the Desktop, ready for the server.
# The PC must NOT run the service again afterwards: two copies of the same WhatsApp session conflict.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Write-Host "Stopping the scheduled tasks and the running bridge / agent..."
foreach ($t in "WhatsApp bridge", "WhatsApp agent") {
    try { Disable-ScheduledTask -TaskName $t | Out-Null; Stop-ScheduledTask -TaskName $t } catch { }
}
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*whatsapp-claude-bridge*" -and
    ($_.Name -eq "whatsapp-bridge.exe" -or $_.CommandLine -like "*agent.py*" -or $_.CommandLine -like "*run-*.cmd*") } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3

$stage = Join-Path $env:TEMP "whatsapp-claude-export"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force "$stage\app\whatsapp-bridge", "$stage\app\agent" | Out-Null
Copy-Item "$root\whatsapp-bridge\store" "$stage\app\whatsapp-bridge\store" -Recurse
foreach ($f in "config.json", "groups.json", "model_override.json") {
    if (Test-Path "$root\agent\$f") { Copy-Item "$root\agent\$f" "$stage\app\agent\" }
}
Get-ChildItem "$root\agent" -Filter "agent.db*" | Copy-Item -Destination "$stage\app\agent\"
if (Test-Path "$root\agent\home") { Copy-Item "$root\agent\home" "$stage\app\agent\home" -Recurse }

$zip = Join-Path ([Environment]::GetFolderPath("Desktop")) "whatsapp-claude-export.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$stage\*" -DestinationPath $zip
Remove-Item $stage -Recurse -Force
Write-Host ""
Write-Host "Done: $zip"
Write-Host "The service on this PC is stopped and will not start again at logon."
Write-Host "To undo (run it on the PC again): Enable-ScheduledTask 'WhatsApp bridge'; Enable-ScheduledTask 'WhatsApp agent'"
