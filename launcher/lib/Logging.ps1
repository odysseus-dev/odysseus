function Initialize-OdysseusLauncherLog {
    param([string]$LogPath)
    $dir = Split-Path $LogPath -Parent
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    if (-not (Test-Path $LogPath)) {
        New-Item -ItemType File -Path $LogPath -Force | Out-Null
    }
}

function Write-OdysseusLauncherLog {
    param(
        [string]$LogPath,
        [string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR')]
        [string]$Level = 'INFO'
    )
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Level, $Message
    try {
        Add-Content -Path $LogPath -Value $line -Encoding UTF8
    } catch { }
    return $line
}