function Get-OdysseusPluginInfo {
    @{
        Id       = 'venv'
        Name     = 'Virtual environment'
        Order    = 20
        Phase    = 'preflight'
        Required = $true
    }
}

function Test-OdysseusPlugin {
    param($Context)

    if (Test-Path $Context.VenvPython) {
        return @{
            Status  = 'ok'
            Message = 'venv ready'
            Detail  = $Context.VenvPython
            Fix     = ''
        }
    }

    return @{
        Status  = 'fail'
        Message = 'venv missing'
        Detail  = $Context.VenvPython
        Fix     = 'Click Setup to create the virtual environment.'
    }
}