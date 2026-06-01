$ErrorActionPreference = "Stop"

$required = @(
    "src-tauri/Cargo.toml",
    "src-tauri/tauri.conf.json",
    "src-tauri/src/main.rs",
    "src-tauri/icons/icon.ico",
    "scripts/ci/prepare-tauri-sidecar.ps1"
)

foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing Tauri desktop file: $path"
    }
}

$config = Get-Content -LiteralPath "src-tauri/tauri.conf.json" -Raw | ConvertFrom-Json
if (-not ($config.bundle.externalBin -contains "binaries/odysseus-backend")) {
    throw "Tauri config must bundle the Odysseus backend sidecar"
}

if (-not ($config.bundle.icon -contains "icons/icon.ico")) {
    throw "Tauri config must include the Windows desktop icon"
}

$main = Get-Content -LiteralPath "src-tauri/src/main.rs" -Raw
foreach ($pattern in @("sidecar(`"odysseus-backend`")", "ODYSSEUS_DATA_DIR", "DATABASE_URL", "wait_for_health", "WebviewWindowBuilder")) {
    if ($main -notlike "*$pattern*") {
        throw "Tauri main.rs missing expected pattern: $pattern"
    }
}

Write-Host "Tauri desktop config smoke passed."
