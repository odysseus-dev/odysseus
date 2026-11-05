
# ==========================================
# Stop Odysseus AI and llama.cpp
# ==========================================

$root = "D:\AI\Odysseus"
$llamaPath = "D:\AI\llama.cpp\bin\llama-server.exe"

$results = [System.Collections.Generic.List[string]]::new()

function Stop-VerifiedServer {
    param(
        [int]$Port,
        [string]$Service
    )

    $listeners = @(Get-NetTCPConnection `
        -LocalPort $Port `
        -State Listen `
        -ErrorAction SilentlyContinue |
        Sort-Object OwningProcess -Unique)

    if ($listeners.Count -eq 0) {
        $results.Add("$Service is already stopped.")
        return
    }

    foreach ($listener in $listeners) {

        $processId = [int]$listener.OwningProcess

        $proc = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $processId" `
            -ErrorAction SilentlyContinue

        if (-not $proc) {
            $results.Add("Could not identify $Service.")
            continue
        }

        # Verify the correct application
        if ($Port -eq 7000) {

            $valid = (
                $proc.Name -in @("python.exe", "pythonw.exe") -and
                $proc.CommandLine -match "uvicorn\s+app:app" -and
                $proc.CommandLine -match "--port\s+7000"
            )

        }
        elseif ($Port -eq 8000) {

            $valid = (
                $proc.Name -eq "llama-server.exe" -and
                $proc.ExecutablePath -ieq $llamaPath
            )

        }
        else {
            $valid = $false
        }

        if (-not $valid) {
            $results.Add(
                "$Service not stopped: unrecognized process $($proc.Name) (PID $processId)."
            )
            continue
        }

        # Identify the parent before stopping the server
        $parentId = $proc.ParentProcessId

        $parent = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $parentId" `
            -ErrorAction SilentlyContinue

        try {
            Stop-Process -Id $processId `
                -Force -ErrorAction Stop

            $results.Add("$Service stopped successfully.")

            # Close only the associated Odysseus launcher window
            if ($Port -eq 7000 -and $parent) {

                $isLauncher = (
                    $parent.Name -in @("powershell.exe", "pwsh.exe") -and
                    $parent.CommandLine -like "*$root\launch-windows.ps1*"
                )

                if ($isLauncher) {
                    Stop-Process -Id $parentId `
                        -Force -ErrorAction SilentlyContinue
                }
            }
        }
        catch {
            $results.Add(
                "Failed to stop $Service : $($_.Exception.Message)"
            )
        }
    }
}

# Stop both servers
Stop-VerifiedServer -Port 7000 -Service "Odysseus"
Stop-VerifiedServer -Port 8000 -Service "llama.cpp"
