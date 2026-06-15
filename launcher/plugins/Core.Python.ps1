function Get-OdysseusPluginInfo {
    @{
        Id       = 'python'
        Name     = 'Python 3.11+'
        Order    = 10
        Phase    = 'preflight'
        Required = $true
    }
}

function Test-OdysseusPlugin {
    param($Context)

    if (-not $Context.PythonExe) {
        $py = Get-OdysseusPythonLauncher
        if ($py) {
            $Context.PythonExe = $py.Exe
            $Context.PythonArgs = $py.Args
            $Context.PythonVersion = $py.Version
        }
    }

    if ($Context.PythonExe) {
        return @{
            Status  = 'ok'
            Message = ("Python {0}" -f $Context.PythonVersion)
            Detail  = $Context.PythonExe
            Fix     = ''
        }
    }

    return @{
        Status  = 'fail'
        Message = 'Python 3.11+ not found'
        Detail  = 'Install from https://www.python.org/downloads/'
        Fix     = 'Install Python 3.11+ and ensure py or python is on PATH.'
    }
}