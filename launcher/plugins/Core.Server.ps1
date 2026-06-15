function Get-OdysseusPluginInfo {
    @{
        Id       = 'server'
        Name     = 'Odysseus server'
        Order    = 100
        Phase    = 'runtime'
        Required = $true
    }
}

function Test-OdysseusPlugin {
    param($Context)

    $owned = $Context.ServerProcess -and -not $Context.ServerProcess.HasExited
    $listening = Test-OdysseusServerListening -Context $Context

    if (-not $listening) {
        if ($owned) {
            return @{
                Status  = 'pending'
                Message = 'Starting...'
                Detail  = ("Waiting on {0}" -f $Context.BaseUrl)
                Fix     = ''
            }
        }
        return @{
            Status  = 'pending'
            Message = 'Stopped'
            Detail  = ("Port {0} is free" -f $Context.Port)
            Fix     = 'Click Launch to start the server.'
        }
    }

    $health = Invoke-OdysseusHttpGet -Url $Context.HealthUrl -TimeoutSec 2
    if (-not $health -or $health.StatusCode -ge 400) {
        return @{
            Status  = 'warn'
            Message = 'Port open but health check failed'
            Detail  = $Context.HealthUrl
            Fix     = 'Check logs/odysseus-server.log'
        }
    }

    $ready = Invoke-OdysseusHttpGet -Url $Context.ReadyUrl -TimeoutSec 3
    $readyOk = $ready -and $ready.StatusCode -eq 200
    $detail = if ($readyOk) { 'Health + readiness OK' } else { 'Live but readiness check failed' }

    return @{
        Status  = if ($readyOk) { 'ok' } else { 'warn' }
        Message = if ($owned) { 'Running (launcher-managed)' } else { 'Running (external)' }
        Detail  = $detail
        Fix     = if ($readyOk) { '' } else { 'Database or data directory may be unhealthy - see /api/ready' }
    }
}