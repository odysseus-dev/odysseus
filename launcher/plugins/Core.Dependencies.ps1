function Get-OdysseusPluginInfo {
    @{
        Id       = 'dependencies'
        Name     = 'Python dependencies'
        Order    = 30
        Phase    = 'preflight'
        Required = $true
    }
}

function Test-OdysseusPlugin {
    param($Context)

    if (-not (Test-Path $Context.VenvPython)) {
        return @{
            Status  = 'fail'
            Message = 'Cannot check - venv missing'
            Detail  = ''
            Fix     = 'Run Setup first.'
        }
    }

    $probe = & $Context.VenvPython -c "import fastapi, uvicorn, sqlalchemy; print('ok')" 2>&1
    if ($LASTEXITCODE -eq 0 -and "$probe" -eq 'ok') {
        return @{
            Status  = 'ok'
            Message = 'Core packages installed'
            Detail  = 'fastapi, uvicorn, sqlalchemy'
            Fix     = ''
        }
    }

    return @{
        Status  = 'fail'
        Message = 'Dependencies missing or broken'
        Detail  = ($probe | Out-String).Trim()
        Fix     = 'Click Setup to pip install -r requirements.txt'
    }
}