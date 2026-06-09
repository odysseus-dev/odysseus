param(
    [switch]$SkipRuntime
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $repoRoot
try {
    if (-not $SkipRuntime) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File scripts\prepare-python-runtime.ps1
        & powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-python-runtime.ps1
    }

    python -m pytest python/tests
    npm run build

    $cargo = Get-Command cargo -ErrorAction SilentlyContinue
    if (-not $cargo) {
        $cargoPath = Join-Path $env:USERPROFILE ".cargo\bin"
        if (Test-Path -LiteralPath (Join-Path $cargoPath "cargo.exe")) {
            $env:PATH = "$cargoPath;$env:PATH"
            $cargo = Get-Command cargo -ErrorAction SilentlyContinue
        }
    }

    if ($cargo) {
        Push-Location src-tauri
        try {
            cargo check
        } finally {
            Pop-Location
        }
    } else {
        Write-Warning "cargo was not found; skipped Rust cargo check."
    }

    Write-Host "MVP smoke checks passed."
} finally {
    Pop-Location
}
