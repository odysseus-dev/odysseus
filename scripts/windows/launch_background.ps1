param(
    [Parameter(Mandatory=$true)][string]$RunnerPath,
    [Parameter(Mandatory=$true)][string]$LogDir,
    [Parameter(Mandatory=$true)][string]$SessionId
)

$logPath = Join-Path $LogDir "$SessionId.log"
$errPath = Join-Path $LogDir "$SessionId.err.log"
$pidPath = Join-Path $LogDir "$SessionId.pid"

$proc = Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -Command `"$RunnerPath`"" -RedirectStandardOutput $logPath -RedirectStandardError $errPath -NoNewWindow -PassThru
$proc.Id | Out-File -FilePath $pidPath -Encoding ASCII
