function Get-OdysseusPluginInfo {
    @{
        Id       = 'git_bash'
        Name     = 'Git Bash (Cookbook / shell)'
        Order    = 60
        Phase    = 'preflight'
        Required = $false
    }
}

function Test-OdysseusPlugin {
    param($Context)

    $bash = Get-Command bash -ErrorAction SilentlyContinue
    if ($bash) {
        return @{
            Status  = 'ok'
            Message = 'bash.exe on PATH'
            Detail  = $bash.Source
            Fix     = ''
        }
    }

    $candidates = @(
        'C:\Program Files\Git\bin\bash.exe',
        'C:\Program Files (x86)\Git\bin\bash.exe'
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) {
            return @{
                Status  = 'ok'
                Message = 'Git Bash installed'
                Detail  = $path
                Fix     = ''
            }
        }
    }

    return @{
        Status  = 'warn'
        Message = 'Not found'
        Detail  = 'Cookbook downloads and agent shell tool need bash'
        Fix     = 'Install Git for Windows: https://git-scm.com/download/win'
    }
}