function Get-OdysseusPluginInfo {
    @{
        Id       = 'data_dir'
        Name     = 'Data directory'
        Order    = 40
        Phase    = 'preflight'
        Required = $true
    }
}

function Test-OdysseusPlugin {
    param($Context)

    $dataDir = Join-Path $Context.RepoRoot 'data'
    try {
        if (-not (Test-Path $dataDir)) {
            New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
        }
        $probe = Join-Path $dataDir ('.launcher_probe_{0}' -f ([guid]::NewGuid().ToString('N')))
        Set-Content -Path $probe -Value 'ok' -Encoding UTF8
        Remove-Item $probe -Force
        return @{
            Status  = 'ok'
            Message = 'Writable'
            Detail  = $dataDir
            Fix     = ''
        }
    } catch {
        return @{
            Status  = 'fail'
            Message = 'Not writable'
            Detail  = $_.Exception.Message
            Fix     = 'Check permissions on the data/ folder.'
        }
    }
}