<#
.SYNOPSIS
    Isolate the VM and create a standard user to run the launcher.

.DESCRIPTION
    - Firewall: enable the profiles, block all inbound connections, allow outbound.
    - Create a standard local user (OdysseusUser) who is NOT an administrator.
    - Enable the local Guest account only if needed, but default is off.

.NOTES
    Run as Administrator (Vagrant handles this via privileged=true).
#>

$ErrorActionPreference = "Stop"

Write-Host "[*] Hardening VM isolation..." -ForegroundColor Cyan

# 1. Firewall policy - block inbound, allow outbound.
# This is the security-relevant setting, so it fails closed. -Enabled True is
# not redundant: a default action only applies while the profile is on.
Set-NetFirewallProfile -Profile Domain,Public,Private `
    -Enabled True `
    -DefaultInboundAction Block `
    -DefaultOutboundAction Allow | Out-Null

Write-Host "[+] Firewall: inbound blocked, outbound allowed."

# 2. Firewall logging - diagnostics, not policy.
# Windows caps the log at 32767 KB and rejects anything larger with
# "Windows System Error 87, Set-NetFirewallProfile". Keep the value in range,
# and if an image refuses the logging parameters for some other reason, report
# it and carry on rather than failing the whole provisioner over a log file.
$logDir = "C:\Windows\system32\LogFiles\Firewall"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

try {
    Set-NetFirewallProfile -Profile Domain,Public,Private `
        -LogFileName "$logDir\pfirewall.log" `
        -LogMaxSizeKilobytes 32767 `
        -LogBlocked True | Out-Null
    Write-Host "[+] Firewall logging: blocked packets -> $logDir\pfirewall.log"
} catch {
    Write-Host "[!] Firewall logging could not be configured: $($_.Exception.Message)"
    Write-Host "    Inbound is still blocked; only the packet log is unavailable."
}

# 3. Create a standard user for interactive testing
$userName = "OdysseusUser"
$password = ConvertTo-SecureString "Odysseus123!" -AsPlainText -Force

if (-not (Get-LocalUser -Name $userName -ErrorAction SilentlyContinue)) {
    New-LocalUser -Name $userName -Password $password `
        -FullName "Odysseus Test User" `
        -Description "Standard user for running the Odysseus launcher" | Out-Null
    # Explicitly remove from Administrators in case defaults differ
    Remove-LocalGroupMember -Group "Administrators" -Member $userName -ErrorAction SilentlyContinue
    Write-Host "[+] Standard user '$userName' created."
} else {
    Write-Host "[*] User '$userName' already exists."
}

# Allow RDP for this user (the eval box usually has RDP on, but enforce)
Add-LocalGroupMember -Group "Remote Desktop Users" -Member $userName -ErrorAction SilentlyContinue | Out-Null

Write-Host "[*] Isolation complete." -ForegroundColor Green
