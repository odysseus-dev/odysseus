$env:HF_HOME = "D:\AI\HuggingFace"
$env:HF_HUB_CACHE = "D:\AI\HuggingFace\hub"
$env:HF_XET_CACHE = "D:\AI\HuggingFace\xet"

$env:Path = "D:\AI\llama.cpp\bin;" + $env:Path

$root = "D:\AI\Odysseus"
$launcher = Join-Path $root "launch-windows.ps1"

# Make your existing Python installation available
$env:Path = "D:\Python;" + $env:Path

# Check if the web server is already running
function Test-Odysseus {
    try {
        $client = [System.Net.Sockets.TcpClient]::new(
            "127.0.0.1", 7000
        )
        $client.Dispose()
        return $true
    }
    catch {
        return $false
    }
}

# Start Odysseus if it isn't already running
if (-not (Test-Odysseus)) {
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -NoExit -ExecutionPolicy Bypass -File `"$launcher`"" `
        -WorkingDirectory $root
}

# Wait for the server to become available
$ready = $false

for ($i = 0; $i -lt 300; $i++) {
    if (Test-Odysseus) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
}

# Open the application in the default browser
if ($ready) {
    Start-Process "http://localhost:7000"
}
else {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "Odysseus did not start. Check the server PowerShell window.",
        "Odysseus AI"
    )
}
