function Get-OdysseusPythonLauncher {
    function Get-PythonVersionText($launcher, $launcherArgs) {
        try {
            return (& $launcher @launcherArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim()
        } catch {
            return $null
        }
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($v in @('-3.13', '-3.12', '-3.11')) {
            $ver = Get-PythonVersionText $pyLauncher.Source @($v)
            if ($ver) {
                return [PSCustomObject]@{
                    Exe     = $pyLauncher.Source
                    Args    = @($v)
                    Version = $ver
                }
            }
        }
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $ver = Get-PythonVersionText $pythonCmd.Source @()
        if ($ver) {
            $parts = $ver.Split('.')
            $major = [int]$parts[0]
            $minor = [int]$parts[1]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                return [PSCustomObject]@{
                    Exe     = $pythonCmd.Source
                    Args    = @()
                    Version = $ver
                }
            }
        }
    }

    return $null
}

function Ensure-OdysseusVirtualEnv {
    param($Context)

    if (Test-Path $Context.VenvPython) {
        return $true
    }
    if (-not $Context.PythonExe) {
        return $false
    }

    Push-Location $Context.RepoRoot
    try {
        & $Context.PythonExe $Context.PythonArgs -m venv venv
        return (Test-Path $Context.VenvPython)
    } finally {
        Pop-Location
    }
}

function Install-OdysseusDependencies {
    param($Context)

    if (-not (Test-Path $Context.VenvPython)) { return $false }
    Push-Location $Context.RepoRoot
    try {
        & $Context.VenvPython -m pip install --upgrade pip --quiet
        & $Context.VenvPython -m pip install -r requirements.txt
        return ($LASTEXITCODE -eq 0)
    } finally {
        Pop-Location
    }
}

function Invoke-OdysseusSetup {
    param($Context)

    if (-not (Test-Path $Context.VenvPython)) { return $false }
    Push-Location $Context.RepoRoot
    try {
        & $Context.VenvPython setup.py
        return ($LASTEXITCODE -eq 0)
    } finally {
        Pop-Location
    }
}

function Invoke-OdysseusFullSetup {
    param(
        $Context,
        [scriptblock]$OnStep
    )

    $py = Get-OdysseusPythonLauncher
    if (-not $py) {
        & $OnStep 'Python 3.11+ not found on PATH.'
        return $false
    }
    $Context.PythonExe = $py.Exe
    $Context.PythonArgs = $py.Args
    $Context.PythonVersion = $py.Version

    & $OnStep ("Using Python {0}" -f $py.Version)
    & $OnStep 'Ensuring virtual environment...'
    if (-not (Ensure-OdysseusVirtualEnv $Context)) {
        & $OnStep 'Failed to create virtual environment.'
        return $false
    }

    & $OnStep 'Installing dependencies (first run can take a few minutes)...'
    if (-not (Install-OdysseusDependencies $Context)) {
        & $OnStep 'Dependency install failed.'
        return $false
    }

    & $OnStep 'Running setup.py...'
    if (-not (Invoke-OdysseusSetup $Context)) {
        & $OnStep 'setup.py failed.'
        return $false
    }

    & $OnStep 'Setup complete.'
    return $true
}

function Test-OdysseusTcpPort {
    param(
        [string]$HostName = '127.0.0.1',
        [int]$Port,
        [int]$TimeoutMs = 1500
    )
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if ($ok -and $client.Connected) {
            $client.EndConnect($iar)
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Invoke-OdysseusHttpGet {
    param(
        [string]$Url,
        [int]$TimeoutSec = 3
    )
    try {
        return Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    } catch {
        return $null
    }
}