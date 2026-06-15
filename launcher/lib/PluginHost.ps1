function Import-OdysseusPlugins {
    param([string]$PluginDir)

    $loaded = @()
    if (-not (Test-Path $PluginDir)) { return $loaded }

    Get-ChildItem -Path $PluginDir -Filter '*.ps1' | Sort-Object Name | ForEach-Object {
        . $_.FullName
        $info = Get-OdysseusPluginInfo
        $testFn = ${function:Test-OdysseusPlugin}
        if (-not $testFn) {
            throw "Plugin missing Test-OdysseusPlugin: $($_.FullName)"
        }
        $loaded += [PSCustomObject]@{
            File = $_.FullName
            Info = $info
            Test = $testFn
        }
        Remove-Item "Function:Test-OdysseusPlugin" -ErrorAction SilentlyContinue
        Remove-Item "Function:Get-OdysseusPluginInfo" -ErrorAction SilentlyContinue
    }

    return $loaded | Sort-Object { $_.Info.Order }
}

function Test-OdysseusPlugins {
    param(
        $Context,
        $Plugins,
        [ValidateSet('preflight', 'runtime', 'all')]
        [string]$Phase = 'all'
    )

    $results = @()
    foreach ($plugin in $Plugins) {
        $phase = $plugin.Info.Phase
        if ($Phase -ne 'all' -and $phase -ne $Phase) { continue }

        try {
            $raw = & $plugin.Test -Context $Context
            $result = [PSCustomObject]@{
                Id       = $plugin.Info.Id
                Name     = $plugin.Info.Name
                Required = [bool]$plugin.Info.Required
                Phase    = $phase
                Status   = $raw.Status
                Message  = $raw.Message
                Detail   = $raw.Detail
                Fix      = $raw.Fix
            }
            $results += $result
        } catch {
            $results += [PSCustomObject]@{
                Id       = $plugin.Info.Id
                Name     = $plugin.Info.Name
                Required = [bool]$plugin.Info.Required
                Phase    = $phase
                Status   = 'fail'
                Message  = 'Plugin check crashed'
                Detail   = $_.Exception.Message
                Fix      = 'See logs/launcher.log'
            }
        }
    }
    return $results
}

function Test-OdysseusLaunchBlocked {
    param($Results)
    return [bool]($Results | Where-Object { $_.Required -and $_.Status -eq 'fail' })
}