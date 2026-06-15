#Requires -Version 5.1
# Odysseus desktop launcher - status dashboard + server control.
# Contributed by Wykeve (VoidCat) - 2026. https://github.com/pewdiepie-archdaemon/odysseus
# Run via Odysseus.vbs (no console flash) or: powershell -STA -File launcher\Odysseus.Launcher.ps1

$ErrorActionPreference = 'Stop'

$LauncherRoot = $PSScriptRoot
$RepoRoot = Split-Path $LauncherRoot -Parent

. (Join-Path $LauncherRoot 'lib\Logging.ps1')
. (Join-Path $LauncherRoot 'lib\Context.ps1')
. (Join-Path $LauncherRoot 'lib\Environment.ps1')
. (Join-Path $LauncherRoot 'lib\PluginHost.ps1')
. (Join-Path $LauncherRoot 'lib\Server.ps1')
. (Join-Path $LauncherRoot 'lib\Browser.ps1')

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$script:Context = New-OdysseusLauncherContext -RepoRoot $RepoRoot
Initialize-OdysseusLauncherLog -LogPath $script:Context.LogPath
$script:Plugins = Import-OdysseusPlugins -PluginDir $script:Context.PluginDir
$script:StatusRows = @{}
$script:Busy = $false

function Write-UiLog {
    param(
        [string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR')]
        [string]$Level = 'INFO'
    )
    $line = Write-OdysseusLauncherLog -LogPath $script:Context.LogPath -Message $Message -Level $Level
    if ($script:LogBox) {
        $script:LogBox.AppendText("$line`r`n")
        $script:LogBox.SelectionStart = $script:LogBox.Text.Length
        $script:LogBox.ScrollToCaret()
    }
}

function Get-StatusColor {
    param([string]$Status)
    switch ($Status) {
        'ok' { return [System.Drawing.Color]::FromArgb(72, 160, 96) }
        'warn' { return [System.Drawing.Color]::FromArgb(196, 140, 48) }
        'fail' { return [System.Drawing.Color]::FromArgb(196, 72, 72) }
        'pending' { return [System.Drawing.Color]::FromArgb(120, 120, 128) }
        default { return [System.Drawing.Color]::FromArgb(120, 120, 128) }
    }
}

function Get-StatusGlyph {
    param([string]$Status)
    switch ($Status) {
        'ok' { return [char]0x2713 }
        'warn' { return '!' }
        'fail' { return 'x' }
        default { return [char]0x25CB }
    }
}

function Update-StatusPanel {
    $preflight = Test-OdysseusPlugins -Context $script:Context -Plugins $script:Plugins -Phase 'preflight'
    $runtime = Test-OdysseusPlugins -Context $script:Context -Plugins $script:Plugins -Phase 'runtime'
    $all = @($preflight) + @($runtime)

    foreach ($result in $all) {
        if (-not $script:StatusRows.ContainsKey($result.Id)) { continue }
        $row = $script:StatusRows[$result.Id]
        $glyph = Get-StatusGlyph $result.Status
        $row.Label.Text = ("{0}  {1}" -f $glyph, $result.Name)
        $row.Detail.Text = if ($result.Fix) { "{0} - {1}" -f $result.Message, $result.Fix } else { $result.Message }
        $row.Detail.ForeColor = Get-StatusColor $result.Status
    }

    $blocked = Test-OdysseusLaunchBlocked -Results $preflight
    $listening = Test-OdysseusServerListening -Context $script:Context
    $owned = $script:Context.ServerProcess -and -not $script:Context.ServerProcess.HasExited

    $script:BtnLaunch.Enabled = (-not $script:Busy) -and (-not $blocked) -and (-not $listening)
    $script:BtnStop.Enabled = (-not $script:Busy) -and ($owned -or $listening)
    $script:BtnOpen.Enabled = (-not $script:Busy) -and $listening
    $script:BtnSetup.Enabled = -not $script:Busy
}

function Show-LauncherForm {
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Odysseus Launcher'
    $form.StartPosition = 'CenterScreen'
    $form.Size = New-Object System.Drawing.Size(720, 620)
    $form.MinimumSize = New-Object System.Drawing.Size(640, 520)
    $form.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $form.BackColor = [System.Drawing.Color]::FromArgb(24, 24, 28)
    $form.ForeColor = [System.Drawing.Color]::FromArgb(230, 230, 235)

    $title = New-Object System.Windows.Forms.Label
    $title.Text = 'Odysseus'
    $title.Font = New-Object System.Drawing.Font('Segoe UI', 16, [System.Drawing.FontStyle]::Bold)
    $title.AutoSize = $true
    $title.Location = New-Object System.Drawing.Point(20, 16)
    $title.ForeColor = [System.Drawing.Color]::FromArgb(240, 120, 120)
    $form.Controls.Add($title)

    $subtitle = New-Object System.Windows.Forms.Label
    $subtitle.Text = ("Local workspace - {0}" -f $script:Context.BaseUrl)
    $subtitle.AutoSize = $true
    $subtitle.Location = New-Object System.Drawing.Point(22, 48)
    $subtitle.ForeColor = [System.Drawing.Color]::FromArgb(160, 160, 168)
    $form.Controls.Add($subtitle)

    $statusPanel = New-Object System.Windows.Forms.Panel
    $statusPanel.Location = New-Object System.Drawing.Point(20, 80)
    $statusPanel.Size = New-Object System.Drawing.Size(660, 250)
    $statusPanel.AutoScroll = $true
    $statusPanel.BackColor = [System.Drawing.Color]::FromArgb(32, 32, 38)
    $form.Controls.Add($statusPanel)

    $y = 8
    foreach ($plugin in $script:Plugins) {
        $label = New-Object System.Windows.Forms.Label
        $label.Text = ("{0}  {1}" -f ([char]0x25CB), $plugin.Info.Name)
        $label.Location = New-Object System.Drawing.Point(12, $y)
        $label.Size = New-Object System.Drawing.Size(300, 22)
        $label.ForeColor = [System.Drawing.Color]::FromArgb(220, 220, 225)
        $statusPanel.Controls.Add($label)

        $detail = New-Object System.Windows.Forms.Label
        $detail.Text = 'Checking...'
        $detail.Location = New-Object System.Drawing.Point(320, $y)
        $detail.Size = New-Object System.Drawing.Size(320, 22)
        $detail.ForeColor = [System.Drawing.Color]::FromArgb(120, 120, 128)
        $statusPanel.Controls.Add($detail)

        $script:StatusRows[$plugin.Info.Id] = @{ Label = $label; Detail = $detail }
        $y += 26
    }

    $btnY = 340
    $script:BtnSetup = New-Object System.Windows.Forms.Button
    $script:BtnSetup.Text = 'Setup'
    $script:BtnSetup.Location = New-Object System.Drawing.Point(20, $btnY)
    $script:BtnSetup.Size = New-Object System.Drawing.Size(100, 34)
    $form.Controls.Add($script:BtnSetup)

    $script:BtnLaunch = New-Object System.Windows.Forms.Button
    $script:BtnLaunch.Text = 'Launch'
    $script:BtnLaunch.Location = New-Object System.Drawing.Point(130, $btnY)
    $script:BtnLaunch.Size = New-Object System.Drawing.Size(100, 34)
    $form.Controls.Add($script:BtnLaunch)

    $script:BtnStop = New-Object System.Windows.Forms.Button
    $script:BtnStop.Text = 'Stop'
    $script:BtnStop.Location = New-Object System.Drawing.Point(240, $btnY)
    $script:BtnStop.Size = New-Object System.Drawing.Size(100, 34)
    $form.Controls.Add($script:BtnStop)

    $script:BtnOpen = New-Object System.Windows.Forms.Button
    $script:BtnOpen.Text = 'Open'
    $script:BtnOpen.Location = New-Object System.Drawing.Point(350, $btnY)
    $script:BtnOpen.Size = New-Object System.Drawing.Size(100, 34)
    $form.Controls.Add($script:BtnOpen)

    $btnLogs = New-Object System.Windows.Forms.Button
    $btnLogs.Text = 'Logs'
    $btnLogs.Location = New-Object System.Drawing.Point(460, $btnY)
    $btnLogs.Size = New-Object System.Drawing.Size(100, 34)
    $form.Controls.Add($btnLogs)

    $logLabel = New-Object System.Windows.Forms.Label
    $logLabel.Text = 'Activity'
    $logLabel.Location = New-Object System.Drawing.Point(20, 386)
    $logLabel.AutoSize = $true
    $logLabel.ForeColor = [System.Drawing.Color]::FromArgb(160, 160, 168)
    $form.Controls.Add($logLabel)

    $script:LogBox = New-Object System.Windows.Forms.TextBox
    $script:LogBox.Multiline = $true
    $script:LogBox.ReadOnly = $true
    $script:LogBox.ScrollBars = 'Vertical'
    $script:LogBox.Location = New-Object System.Drawing.Point(20, 410)
    $script:LogBox.Size = New-Object System.Drawing.Size(660, 150)
    $script:LogBox.BackColor = [System.Drawing.Color]::FromArgb(18, 18, 22)
    $script:LogBox.ForeColor = [System.Drawing.Color]::FromArgb(200, 200, 205)
    $script:LogBox.Font = New-Object System.Drawing.Font('Consolas', 9)
    $form.Controls.Add($script:LogBox)

    $footer = New-Object System.Windows.Forms.Label
    $footer.Text = ("Logs: {0}  |  Server: {1}" -f $script:Context.LogPath, $script:Context.ServerLog)
    $footer.Location = New-Object System.Drawing.Point(20, 552)
    $footer.Size = New-Object System.Drawing.Size(660, 18)
    $footer.ForeColor = [System.Drawing.Color]::FromArgb(120, 120, 128)
    $form.Controls.Add($footer)

    $mark = New-Object System.Windows.Forms.Label
    $mark.Text = 'VoidCat / Wykeve'
    $mark.Location = New-Object System.Drawing.Point(20, 568)
    $mark.Size = New-Object System.Drawing.Size(200, 18)
    $mark.ForeColor = [System.Drawing.Color]::FromArgb(100, 100, 108)
    $mark.Font = New-Object System.Drawing.Font('Segoe UI', 8)
    $form.Controls.Add($mark)

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 2000
    $timer.Add_Tick({ Update-StatusPanel })
    $timer.Start()

    $script:BtnSetup.Add_Click({
        if ($script:Busy) { return }
        $script:Busy = $true
        try {
            Write-UiLog 'Running full setup...'
            $ok = Invoke-OdysseusFullSetup -Context $script:Context -OnStep {
                param($msg)
                Write-UiLog $msg
            }
            if (-not $ok) { Write-UiLog 'Setup failed - see messages above.' 'ERROR' }
        } finally {
            $script:Busy = $false
            Update-StatusPanel
        }
    })

    $script:BtnLaunch.Add_Click({
        if ($script:Busy) { return }
        $preflight = Test-OdysseusPlugins -Context $script:Context -Plugins $script:Plugins -Phase 'preflight'
        if (Test-OdysseusLaunchBlocked -Results $preflight) {
            Write-UiLog 'Cannot launch - fix required checks first (or run Setup).' 'ERROR'
            return
        }

        if (Test-OdysseusServerListening -Context $script:Context) {
            Write-UiLog 'Server already running - opening browser.'
            Open-OdysseusBrowser -Url $script:Context.BaseUrl -OnLog { param($m, $l='INFO') Write-UiLog $m $l }
            Update-StatusPanel
            return
        }

        $script:Busy = $true
        try {
            Write-UiLog 'Launching server...'
            Start-OdysseusServer -Context $script:Context -OnLog { param($m, $l='INFO') Write-UiLog $m $l }
            $ready = Wait-OdysseusServerReady -Context $script:Context -OnLog { param($m, $l='INFO') Write-UiLog $m $l }
            if ($ready) {
                Open-OdysseusBrowser -Url $script:Context.BaseUrl -OnLog { param($m, $l='INFO') Write-UiLog $m $l }
            } else {
                Write-UiLog ("Server failed to start. See {0}" -f $script:Context.ServerLog) 'ERROR'
            }
        } catch {
            Write-UiLog $_.Exception.Message 'ERROR'
        } finally {
            $script:Busy = $false
            Update-StatusPanel
        }
    })

    $script:BtnStop.Add_Click({
        if ($script:Busy) { return }
        Stop-OdysseusServer -Context $script:Context -OnLog { param($m, $l='INFO') Write-UiLog $m $l }
        Update-StatusPanel
    })

    $script:BtnOpen.Add_Click({
        Open-OdysseusBrowser -Url $script:Context.BaseUrl -OnLog { param($m, $l='INFO') Write-UiLog $m $l }
    })

    $btnLogs.Add_Click({
        $paths = @($script:Context.LogPath, $script:Context.ServerLog) | Where-Object { Test-Path $_ }
        if ($paths) { Start-Process 'explorer.exe' -ArgumentList ('/select,' + $paths[0]) }
    })

    $form.Add_FormClosing({
        if ($script:Context.ServerProcess -and -not $script:Context.ServerProcess.HasExited) {
            $answer = [System.Windows.Forms.MessageBox]::Show(
                'Stop the Odysseus server before closing the launcher?',
                'Odysseus Launcher',
                [System.Windows.Forms.MessageBoxButtons]::YesNoCancel,
                [System.Windows.Forms.MessageBoxIcon]::Question
            )
            if ($answer -eq [System.Windows.Forms.DialogResult]::Cancel) {
                $_.Cancel = $true
                return
            }
            if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {
                Stop-OdysseusServer -Context $script:Context -OnLog { param($m) }
            }
        }
    })

    Write-UiLog 'Launcher ready.'
    Update-StatusPanel
    [void]$form.ShowDialog()
}

Show-LauncherForm