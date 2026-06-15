function New-OdysseusLauncherContext {
    param([string]$RepoRoot)

    $port = 7000
    $bindHost = '127.0.0.1'
    $envFile = Join-Path $RepoRoot '.env'
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            if ($_ -match '^\s*APP_PORT\s*=\s*(\d+)\s*$') { $port = [int]$Matches[1] }
            if ($_ -match '^\s*APP_BIND\s*=\s*(\S+)\s*$') { $bindHost = $Matches[1] }
        }
    }

    [PSCustomObject]@{
        RepoRoot     = $RepoRoot
        LauncherRoot = Join-Path $RepoRoot 'launcher'
        PluginDir    = Join-Path $RepoRoot 'launcher\plugins'
        LogPath      = Join-Path $RepoRoot 'logs\launcher.log'
        ServerLog    = Join-Path $RepoRoot 'logs\odysseus-server.log'
        Port         = $port
        BindHost     = $bindHost
        BaseUrl      = "http://{0}:{1}" -f $bindHost, $port
        HealthUrl    = "http://127.0.0.1:{0}/api/health" -f $port
        ReadyUrl     = "http://127.0.0.1:{0}/api/ready" -f $port
        VenvPython   = Join-Path $RepoRoot 'venv\Scripts\python.exe'
        PythonExe    = $null
        PythonArgs   = @()
        PythonVersion = $null
        ServerProcess = $null
    }
}