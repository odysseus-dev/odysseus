# Registers a logon task that boots the Ubuntu WSL distro and keeps it alive,
# so systemd inside WSL runs the odysseus service after every Windows reboot.
# Run as the regular user (no elevation needed): per-user logon task.

$ErrorActionPreference = "Stop"

$action = New-ScheduledTaskAction -Execute "wsl.exe" `
    -Argument '-d Ubuntu --exec /bin/sh -c "sleep infinity"'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "WSL-Odysseus-Keepalive" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Boots the Ubuntu WSL distro at logon and keeps it alive so systemd runs odysseus" `
    -Force

Write-Host "Task registered:"
Get-ScheduledTask -TaskName "WSL-Odysseus-Keepalive" | Format-List TaskName, State
