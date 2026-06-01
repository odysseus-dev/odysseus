param(
    [Parameter(Mandatory = $true)][string]$Family,
    [string]$Target = "",
    [string]$PatchRoot = "patches"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PatchRoot -PathType Container)) {
    throw "Patch root not found: $PatchRoot"
}

function Apply-PatchFile {
    param([Parameter(Mandatory = $true)][string]$PatchFile)

    Write-Host "Applying patch: $PatchFile"
    git apply --check $PatchFile
    if ($LASTEXITCODE -ne 0) {
        throw "Patch check failed: $PatchFile"
    }
    git apply $PatchFile
    if ($LASTEXITCODE -ne 0) {
        throw "Patch apply failed: $PatchFile"
    }
}

$commonDir = Join-Path $PatchRoot "common"
if (Test-Path -LiteralPath $commonDir -PathType Container) {
    Get-ChildItem -LiteralPath $commonDir -Filter *.patch -File | Sort-Object Name | ForEach-Object {
        Apply-PatchFile $_.FullName
    }
}

if ($Target) {
    $targetPatch = Join-Path (Join-Path $PatchRoot $Family) ($Target + ".patch")
    if (-not (Test-Path -LiteralPath $targetPatch -PathType Leaf)) {
        throw "Target patch not found: $targetPatch"
    }
    Apply-PatchFile $targetPatch
}

