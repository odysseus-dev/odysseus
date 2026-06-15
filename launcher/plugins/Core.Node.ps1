function Get-OdysseusPluginInfo {
    @{
        Id       = 'node'
        Name     = 'Node.js (Browser MCP)'
        Order    = 70
        Phase    = 'preflight'
        Required = $false
    }
}

function Test-OdysseusPlugin {
    param($Context)

    $node = Get-Command node -ErrorAction SilentlyContinue
    $npx = Get-Command npx -ErrorAction SilentlyContinue
    if ($node -and $npx) {
        return @{
            Status  = 'ok'
            Message = 'node + npx available'
            Detail  = $node.Source
            Fix     = ''
        }
    }

    return @{
        Status  = 'warn'
        Message = 'Not found'
        Detail  = 'Built-in Browser MCP will be skipped at startup'
        Fix     = 'Install Node.js: https://nodejs.org/'
    }
}