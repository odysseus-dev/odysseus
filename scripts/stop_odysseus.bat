@echo off
setlocal

set "PORT=7000"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$connections = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; " ^
  "if (-not $connections) { Write-Host 'Odysseus is not running on port %PORT%.'; exit 0 }; " ^
  "$pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique; " ^
  "foreach ($pidValue in $pids) { " ^
  "  try { " ^
  "    $proc = Get-Process -Id $pidValue -ErrorAction Stop; " ^
  "    Write-Host ('Stopping ' + $proc.ProcessName + ' (PID ' + $pidValue + ') on port %PORT%...'); " ^
  "    Stop-Process -Id $pidValue -Force -ErrorAction Stop; " ^
  "  } catch { " ^
  "    Write-Host ('Could not stop PID ' + $pidValue + ': ' + $_.Exception.Message); " ^
  "    exit 1 " ^
  "  } " ^
  "}; " ^
  "Write-Host 'Odysseus stopped.'"

echo.
pause
