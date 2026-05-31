param(
    [Parameter(Mandatory=$true)][string]$CmdScriptPath,
    [string]$HfToken = "",
    [string]$Gpus = "",
    [string]$EnvPrefix = ""
)

$sessionDir = "$env:TEMP\odysseus-sessions"
New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null

if ($HfToken) { $env:HF_TOKEN = $HfToken }
if ($Gpus) { $env:CUDA_VISIBLE_DEVICES = $Gpus }
if ($EnvPrefix) { Invoke-Expression $EnvPrefix }

$cmdContent = Get-Content -Raw -Path $CmdScriptPath
if ($cmdContent -match "vllm") {
    Write-Host "ERROR: vLLM is not supported on Windows. Use Ollama or llama.cpp instead."
    exit 1
}

# Safely invoke the raw user command script
& $CmdScriptPath

Write-Host "`n=== Process exited with code $LASTEXITCODE ==="
