# Registers two logon tasks (current user, hidden windows): the WhatsApp bridge and the agent.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
foreach ($name in "bridge", "agent") {
    $action = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless cmd.exe /c `"$root\run-$name.cmd`""
    Register-ScheduledTask -TaskName "WhatsApp $name" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Output "registered: WhatsApp $name"
}
