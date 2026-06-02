#requires -Version 5.1
<#
.SYNOPSIS
  Assembles the bundled payload for the Odysseus desktop app, then builds the installer.

.DESCRIPTION
  The payload (standalone Python runtime, the app's deps, PortableGit, and the app source) is NOT
  committed to git — this script reproduces it, then runs `tauri build`. Expect a few hundred MB of
  downloads and ~10 minutes on a cold run; re-runs skip anything already present.

.EXAMPLE
  $env:ODYSSEUS_SRC = "C:\path\to\odysseus"   # optional: use a local clone instead of cloning fresh
  .\build.ps1

.OUTPUTS
  src-tauri\target\release\bundle\nsis\Odysseus_*-setup.exe
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "src-tauri\backend"
$PY_MAJOR = "3.13"

function Get-Json($url) { Invoke-RestMethod -Uri $url -Headers @{ "User-Agent" = "odysseus-build" } }

# 1. App source — clone upstream unless a local copy was provided.
$src = $env:ODYSSEUS_SRC
if (-not $src) {
    $src = Join-Path $env:TEMP "odysseus-src"
    if (-not (Test-Path $src)) {
        git clone --depth 1 https://github.com/pewdiepie-archdaemon/odysseus.git $src
    }
}
Write-Host "App source: $src"

# 2. Standalone Python -> backend\runtime
$runtime = Join-Path $backend "runtime"
$py = Join-Path $runtime "python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Downloading standalone Python $PY_MAJOR ..."
    $rel = Get-Json "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
    $asset = $rel.assets |
        Where-Object { $_.name -match "cpython-$($PY_MAJOR)\.\d+\+.*-x86_64-pc-windows-msvc-install_only\.tar\.gz$" } |
        Select-Object -First 1
    if (-not $asset) { throw "No matching standalone Python $PY_MAJOR asset found." }
    $tgz = Join-Path $env:TEMP $asset.name
    Invoke-WebRequest $asset.browser_download_url -OutFile $tgz
    $tmp = Join-Path $env:TEMP "ody_py"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $tmp | Out-Null
    tar -xzf $tgz -C $tmp                      # archive contains a top-level 'python' dir
    New-Item -ItemType Directory -Force $runtime | Out-Null
    Copy-Item (Join-Path $tmp "python\*") $runtime -Recurse -Force
}

# 3. App source overlay -> backend\  (the runtime/git/data dirs and our launchers stay)
$copy = @(
    "app.py", "core", "src", "services", "routes", "static", "mcp_servers",
    "config", "scripts", "licenses", "requirements.txt", "requirements-optional.txt",
    "pyproject.toml", "LICENSE", "ACKNOWLEDGMENTS.md"
)
foreach ($item in $copy) {
    $s = Join-Path $src $item
    if (Test-Path $s) {
        $d = Join-Path $backend $item
        Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item $s $d -Recurse -Force
    }
}

# 4. Python deps (app requirements + desktop extras)
& $py -m pip install --upgrade pip
& $py -m pip install -r (Join-Path $backend "requirements.txt")
& $py -m pip install chromadb duckduckgo-search "uvicorn[standard]"

# 5. PortableGit (the agent bash tool) -> backend\git
$git = Join-Path $backend "git"
if (-not (Test-Path (Join-Path $git "bin\bash.exe"))) {
    Write-Host "Downloading PortableGit ..."
    $rel = Get-Json "https://api.github.com/repos/git-for-windows/git/releases/latest"
    $asset = $rel.assets |
        Where-Object { $_.name -match "PortableGit-.*-64-bit\.7z\.exe$" } |
        Select-Object -First 1
    if (-not $asset) { throw "No PortableGit 64-bit asset found." }
    $exe = Join-Path $env:TEMP $asset.name
    Invoke-WebRequest $asset.browser_download_url -OutFile $exe
    New-Item -ItemType Directory -Force $git | Out-Null
    & $exe -y -o"$git"                         # self-extracting 7-Zip archive
}

# 6. Build the installer
Push-Location $root
try {
    npm install
    npx tauri build
}
finally { Pop-Location }

Write-Host "`nDone -> src-tauri\target\release\bundle\nsis\"
