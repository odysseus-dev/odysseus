# Odysseus Launcher Plugins

Windows launcher plugin contract. Contributed by Wykeve (VoidCat) - 2026.

Drop a `.ps1` file in this folder to add a preflight or runtime check.

## Contract

Each plugin must export:

```powershell
function Get-OdysseusPluginInfo {
    @{
        Id       = 'my_plugin'      # unique id
        Name     = 'Human label'
        Order    = 200              # sort order (lower = earlier)
        Phase    = 'preflight'      # preflight | runtime
        Required = $false             # fail blocks Launch when true
    }
}

function Test-OdysseusPlugin {
    param($Context)
    @{
        Status  = 'ok'              # ok | warn | fail | pending
        Message = 'Short status'
        Detail  = 'Extra context'
        Fix     = 'Actionable hint (empty when ok)'
    }
}
```

`$Context` fields: `RepoRoot`, `Port`, `BindHost`, `BaseUrl`, `HealthUrl`, `ReadyUrl`, `VenvPython`, `ServerProcess`, `ServerLog`, `LogPath`.

Optional future hook:

```powershell
function Invoke-OdysseusPluginAction {
    param($Context)
    # Run setup/repair for this plugin
}
```

Plugins are loaded alphabetically by filename; use numeric prefixes (`10.Foo.ps1`) if order matters beyond `Order`.