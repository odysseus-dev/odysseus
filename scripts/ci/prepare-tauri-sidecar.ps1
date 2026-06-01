param(
    [Parameter(Mandatory = $true)][string]$BackendExe,
    [string]$BinaryName = "odysseus-backend"
)

$ErrorActionPreference = "Stop"

$resolvedBackend = Resolve-Path -LiteralPath $BackendExe -ErrorAction Stop
$targetTriple = (rustc --print host-tuple).Trim()
if (-not $targetTriple) {
    throw "Unable to determine Rust target triple"
}

$binaryDir = Join-Path "src-tauri" "binaries"
New-Item -ItemType Directory -Force $binaryDir | Out-Null

$extension = if ($IsWindows -or $env:OS -eq "Windows_NT") { ".exe" } else { "" }
$target = Join-Path $binaryDir "$BinaryName-$targetTriple$extension"
Copy-Item -LiteralPath $resolvedBackend -Destination $target -Force

Write-Host "Prepared Tauri sidecar: $target"
