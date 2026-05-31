param(
    [string]$HfToken = "",
    [string]$EnvPrefix = "",
    [string]$HfCmd = "",
    [string]$RepoId = "",
    [string]$DlPyArg = ""
)

$sessionDir = "$env:TEMP\odysseus-sessions"
New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null

if ($HfToken) { $env:HF_TOKEN = $HfToken }
if ($EnvPrefix) { Invoke-Expression $EnvPrefix }

try {
  $hfPath = Get-Command hf -ErrorAction SilentlyContinue
  if ($hfPath) {
    Invoke-Expression "$null | $HfCmd"
  } else {
    python -c "import huggingface_hub" 2>$null
    if ($LASTEXITCODE -eq 0) {
      Write-Host "hf CLI not found, using Python huggingface_hub..."
      python -c "import os; from huggingface_hub import snapshot_download; snapshot_download('$RepoId'$DlPyArg, max_workers=8)"
    } else {
      Write-Host "Installing huggingface-hub..."
      python -m pip install -q huggingface-hub hf_transfer
      python -c "import os; from huggingface_hub import snapshot_download; snapshot_download('$RepoId'$DlPyArg, max_workers=8)"
    }
  }
  if ($LASTEXITCODE -eq 0) { Write-Host "`nDOWNLOAD_OK" }
  else { Write-Host "`nDOWNLOAD_FAILED (exit $LASTEXITCODE)" }
} catch {
  Write-Host "`nDOWNLOAD_FAILED ($_)"
}
