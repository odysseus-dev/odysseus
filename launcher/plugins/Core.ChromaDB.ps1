function Get-OdysseusPluginInfo {
    @{
        Id       = 'chromadb'
        Name     = 'ChromaDB (vector memory)'
        Order    = 50
        Phase    = 'preflight'
        Required = $false
    }
}

function Test-OdysseusPlugin {
    param($Context)

    if (Test-OdysseusTcpPort -HostName '127.0.0.1' -Port 8100) {
        return @{
            Status  = 'ok'
            Message = 'Reachable on localhost:8100'
            Detail  = 'Vector memory and document RAG available'
            Fix     = ''
        }
    }

    return @{
        Status  = 'warn'
        Message = 'Not running'
        Detail  = 'Memory/RAG will run in degraded mode'
        Fix     = 'docker compose up -d chromadb'
    }
}