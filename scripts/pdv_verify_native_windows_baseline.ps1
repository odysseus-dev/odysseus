#Requires -Version 5.1
<#
.SYNOPSIS
  Read-only verifier for the PDV native-Windows Odysseus boundary.

.DESCRIPTION
  Inspects repository provenance, license/dependency manifests, and local
  Windows prerequisites. It never starts Docker, model servers, or listeners.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedUpstreamCommit,

    [switch]$Json
)

$ErrorActionPreference = "Stop"
$canonicalOrigin = "https://github.com/odysseus-dev/odysseus.git"
$requiredManifests = @(
    "LICENSE",
    "requirements.txt",
    "requirements-optional.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "setup.py"
)

function Invoke-GitText {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $output = & git -C $script:root @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "git command failed: git -C $script:root $($Arguments -join ' ')"
    }
    return (($output | Out-String).Trim())
}

function Find-CompatiblePython {
    $candidates = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($selector in @("-3.13", "-3.12", "-3.11")) {
            $candidates += ,@($py.Source, $selector)
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $candidates += ,@($python.Source)
    }

    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $args = @($candidate | Select-Object -Skip 1)
        try {
            $raw = (& $exe @args -c "import json,sys; print(json.dumps({'version': list(sys.version_info[:3]), 'executable': sys.executable}))" 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -ne 0 -or -not $raw) { continue }
            $probe = $raw | ConvertFrom-Json
            if ([int]$probe.version[0] -gt 3 -or ([int]$probe.version[0] -eq 3 -and [int]$probe.version[1] -ge 11)) {
                return [ordered]@{
                    executable = [string]$probe.executable
                    version = (($probe.version | ForEach-Object { [string]$_ }) -join ".")
                    launcher = $exe
                    launcherArgs = @($args)
                }
            }
        } catch {
            continue
        }
    }
    return $null
}

function Find-GitBash {
    $bash = Get-Command bash.exe -ErrorAction SilentlyContinue
    if ($bash) {
        $candidate = [string]$bash.Source
        $lower = $candidate.ToLowerInvariant()
        if (-not ($lower.Contains("\system32\bash.exe") -or $lower.Contains("\sysnative\bash.exe") -or $lower.Contains("\windowsapps\bash.exe"))) {
            return $candidate
        }
    }
    foreach ($candidate in @(
        "C:\Program Files\Git\bin\bash.exe",
        "C:\Program Files\Git\usr\bin\bash.exe",
        "C:\Program Files (x86)\Git\bin\bash.exe"
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

$errors = [System.Collections.Generic.List[string]]::new()
$root = [System.IO.Path]::GetFullPath($RepositoryRoot)
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    $errors.Add("repository root exists")
}

$origin = ""
$head = ""
$branch = ""
$expectedIsAncestor = $false
$tags = @()
$submodules = @()

if ($errors.Count -eq 0) {
    try {
        $inside = Invoke-GitText rev-parse --is-inside-work-tree
        if ($inside -ne "true") { $errors.Add("repository root is a Git worktree") }
        $origin = Invoke-GitText remote get-url origin
        $head = Invoke-GitText rev-parse HEAD
        $branch = Invoke-GitText branch --show-current
        & git -C $root merge-base --is-ancestor $ExpectedUpstreamCommit HEAD 2>$null
        $expectedIsAncestor = $LASTEXITCODE -eq 0
        $tagText = Invoke-GitText tag --list
        if ($tagText) { $tags = @($tagText -split "`r?`n") }
        if (Test-Path -LiteralPath (Join-Path $root ".gitmodules")) {
            $submoduleText = Invoke-GitText submodule status
            if ($submoduleText) { $submodules = @($submoduleText -split "`r?`n") }
        }
    } catch {
        $errors.Add("repository metadata is readable")
    }
}

if ($origin.TrimEnd('/') -ne $canonicalOrigin.TrimEnd('/')) {
    $errors.Add("canonical origin")
}
if (-not $expectedIsAncestor) {
    $errors.Add("expected upstream commit is present in checkout history")
}

$manifestStatus = [ordered]@{}
foreach ($name in $requiredManifests) {
    $present = Test-Path -LiteralPath (Join-Path $root $name) -PathType Leaf
    $manifestStatus[$name] = $present
    if (-not $present) { $errors.Add("required manifest: $name") }
}

$licensePath = Join-Path $root "LICENSE"
$licenseHash = ""
$licenseSpdx = "unknown"
if (Test-Path -LiteralPath $licensePath -PathType Leaf) {
    $licenseHash = (Get-FileHash -LiteralPath $licensePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $licenseText = Get-Content -LiteralPath $licensePath -Raw
    if ($licenseText -match "GNU AFFERO GENERAL PUBLIC LICENSE" -and $licenseText -match "Version 3") {
        $licenseSpdx = "AGPL-3.0-or-later"
    } else {
        $errors.Add("AGPL-3.0-or-later license text")
    }
}

$python = Find-CompatiblePython
if ($null -eq $python) { $errors.Add("Python 3.11 or newer") }
$gitBash = Find-GitBash

$report = [ordered]@{
    ok = $errors.Count -eq 0
    repository = [ordered]@{
        root = $root
        origin = $origin
        head = $head
        branch = $branch
        expectedUpstreamCommit = $ExpectedUpstreamCommit.ToLowerInvariant()
        expectedCommitIsAncestor = $expectedIsAncestor
        tags = @($tags)
        submodules = @($submodules)
    }
    license = [ordered]@{
        spdx = $licenseSpdx
        sha256 = $licenseHash
        sourceAvailabilityRequired = $true
    }
    dependencies = [ordered]@{
        manifests = $manifestStatus
        pythonLockfilePresent = $false
        npmLockfilePresent = [bool]$manifestStatus["package-lock.json"]
    }
    runtime = [ordered]@{
        platform = [System.Environment]::OSVersion.VersionString
        powershell = $PSVersionTable.PSVersion.ToString()
        python = $python
        git = ((& git --version 2>$null | Out-String).Trim())
        gitBash = $gitBash
        gitBashOptionalForCore = $true
    }
    guardrails = [ordered]@{
        mode = "native-windows-read-only-preflight"
        dockerInvoked = $false
        providersContacted = @()
        credentialsLoaded = $false
        gpuProbed = $false
        portsTouched = @()
        reservedPorts = @(11435, 11436)
    }
    errors = @($errors)
}

if ($Json) {
    $report | ConvertTo-Json -Depth 8
} else {
    $report | Format-List
}

if (-not $report.ok) { exit 1 }
