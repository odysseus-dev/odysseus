# Phase 3c: Install and Register MCP Servers as Windows Services
#
# This script installs both Playwright MCP and PyMCP-FS as Windows services.
# Uses NSSM (Non-Sucking Service Manager) for service management.
#
# Prerequisites:
#   - Admin PowerShell
#   - Node.js 18+ (for Playwright MCP)
#   - Python 3.11 (already installed)
#   - NSSM will be downloaded automatically
#
# RUN AS ADMINISTRATOR

$ErrorActionPreference = "Stop"

Write-Host "Phase 3c: MCP Server Installation" -ForegroundColor Cyan
Write-Host "==================================`n"

# ============================================================================
# Configuration
# ============================================================================

$odysseusDir = "F:\Doc-SSD\Git-Repos\odysseus"
$nssmVersion = "2.24"
$nssmUrl = "https://nssm.cc/download/nssm-$nssmVersion-101.zip"
$nssmDir = "$odysseusDir\deploy\nssm"
$logsDir = "F:\odysseus\logs"

# Service 1: Playwright MCP
$service1Name = "Odysseus-PlaywrightMCP"
$service1Exe = "npx"
$service1Args = "@playwright/mcp@latest --browser chromium --port 9090"
$service1WorkDir = "$odysseusDir"
$service1LogFile = "$logsDir\playwright-mcp.log"

# Service 2: PyMCP-FS
$service2Name = "Odysseus-PyMCPFS"
$service2Exe = "python"
$service2Args = "-m pymcp_fs.server"
$service2WorkDir = "$odysseusDir\mcp\fs"
$service2LogFile = "$logsDir\pymcp-fs.log"

# ============================================================================
# Pre-flight Checks
# ============================================================================

Write-Host "Pre-flight Checks..." -ForegroundColor Yellow
Write-Host "-" * 50

# Check admin
$isAdmin = [Security.Principal.WindowsIdentity]::GetCurrent().Groups -contains 'S-1-5-32-544'
if (-not $isAdmin) {
    Write-Host "✗ ERROR: This script must run as Administrator" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Running as Administrator"

# Check Odysseus directory
if (-not (Test-Path $odysseusDir)) {
    Write-Host "✗ ERROR: Odysseus directory not found: $odysseusDir" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Odysseus directory exists"

# Check Node.js
$nodeCheck = & node --version 2>$null
if (-not $nodeCheck) {
    Write-Host "✗ WARNING: Node.js not found. Install from https://nodejs.org/" -ForegroundColor Yellow
    Write-Host "  Playwright MCP requires Node.js 18+"
} else {
    Write-Host "✓ Node.js found: $nodeCheck"
}

# Check Python
$pythonCheck = & python --version 2>$null
if (-not $pythonCheck) {
    Write-Host "✗ ERROR: Python not found" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Python found: $pythonCheck"

# Create logs directory
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
    Write-Host "✓ Created logs directory"
} else {
    Write-Host "✓ Logs directory exists"
}

Write-Host ""

# ============================================================================
# Step 1: Download and Install NSSM
# ============================================================================

Write-Host "Step 1: Download NSSM (Non-Sucking Service Manager)" -ForegroundColor Yellow
Write-Host "-" * 50

if (Test-Path "$nssmDir\nssm.exe") {
    Write-Host "✓ NSSM already installed"
} else {
    Write-Host "Downloading NSSM..."
    try {
        $tempZip = "$env:TEMP\nssm-$nssmVersion.zip"
        Invoke-WebRequest -Uri $nssmUrl -OutFile $tempZip -ErrorAction Stop
        Write-Host "✓ Downloaded NSSM"

        Write-Host "Extracting NSSM..."
        Expand-Archive -Path $tempZip -DestinationPath "$odysseusDir\deploy" -Force
        Rename-Item -Path "$odysseusDir\deploy\nssm-$nssmVersion" -NewName "nssm" -Force
        Write-Host "✓ Extracted to $nssmDir"

        Remove-Item $tempZip -Force
    } catch {
        Write-Host "✗ Failed to download NSSM: $_" -ForegroundColor Red
        exit 1
    }
}

$nssm = "$nssmDir\nssm.exe"
if (-not (Test-Path $nssm)) {
    Write-Host "✗ ERROR: NSSM executable not found" -ForegroundColor Red
    exit 1
}

Write-Host ""

# ============================================================================
# Step 2: Install Playwright MCP Service
# ============================================================================

Write-Host "Step 2: Install Playwright MCP Service" -ForegroundColor Yellow
Write-Host "-" * 50

# Stop service if it exists
$existingService = Get-Service -Name $service1Name -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "Stopping existing service..."
    Stop-Service -Name $service1Name -Force -ErrorAction SilentlyContinue
    Write-Host "Removing existing service..."
    & $nssm remove $service1Name confirm 2>$null
}

Write-Host "Installing $service1Name..."
try {
    & $nssm install $service1Name $service1Exe $service1Args 2>&1 | Out-Null
    & $nssm set $service1Name AppDirectory $service1WorkDir 2>&1 | Out-Null
    & $nssm set $service1Name AppStdout $service1LogFile 2>&1 | Out-Null
    & $nssm set $service1Name AppStderr $service1LogFile 2>&1 | Out-Null
    & $nssm set $service1Name AppRotateFiles 1 2>&1 | Out-Null
    & $nssm set $service1Name AppRotateOnline 1 2>&1 | Out-Null

    # Set service to start automatically
    Set-Service -Name $service1Name -StartupType Automatic -ErrorAction SilentlyContinue

    Write-Host "✓ $service1Name installed successfully"
    Write-Host "  Exe: $service1Exe"
    Write-Host "  Args: $service1Args"
    Write-Host "  Working Directory: $service1WorkDir"
    Write-Host "  Log: $service1LogFile"
} catch {
    Write-Host "✗ Failed to install $service1Name: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# ============================================================================
# Step 3: Install PyMCP-FS Service
# ============================================================================

Write-Host "Step 3: Install PyMCP-FS Service" -ForegroundColor Yellow
Write-Host "-" * 50

# First, clone and install PyMCP-FS
$pyMcpFsDir = "$odysseusDir\mcp\fs"
if (-not (Test-Path $pyMcpFsDir)) {
    Write-Host "Cloning PyMCP-FS..."
    try {
        & git clone https://github.com/hypercat/PyMCP-FS.git $pyMcpFsDir 2>&1 | Out-Null
        Write-Host "✓ Cloned PyMCP-FS"
    } catch {
        Write-Host "✗ Failed to clone PyMCP-FS: $_" -ForegroundColor Red
        Write-Host "  Make sure git is installed and in PATH"
        exit 1
    }

    Write-Host "Installing PyMCP-FS dependencies..."
    try {
        & pip install -e $pyMcpFsDir 2>&1 | Out-Null
        Write-Host "✓ Installed PyMCP-FS"
    } catch {
        Write-Host "✗ Failed to install PyMCP-FS: $_" -ForegroundColor Red
        exit 1
    }
}

# Create config file for PyMCP-FS
$configFile = "$pyMcpFsDir\config.json"
if (-not (Test-Path $configFile)) {
    $config = @{
        allowed_roots = @(
            "F:\odysseus\data",
            "F:\odysseus\skills",
            "F:\odysseus\logs"
        )
    }
    $config | ConvertTo-Json | Out-File $configFile -Encoding UTF8
    Write-Host "✓ Created PyMCP-FS config: $configFile"
}

# Stop service if it exists
$existingService = Get-Service -Name $service2Name -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "Stopping existing service..."
    Stop-Service -Name $service2Name -Force -ErrorAction SilentlyContinue
    Write-Host "Removing existing service..."
    & $nssm remove $service2Name confirm 2>$null
}

Write-Host "Installing $service2Name..."
try {
    & $nssm install $service2Name $service2Exe $service2Args 2>&1 | Out-Null
    & $nssm set $service2Name AppDirectory $service2WorkDir 2>&1 | Out-Null
    & $nssm set $service2Name AppStdout $service2LogFile 2>&1 | Out-Null
    & $nssm set $service2Name AppStderr $service2LogFile 2>&1 | Out-Null
    & $nssm set $service2Name AppRotateFiles 1 2>&1 | Out-Null
    & $nssm set $service2Name AppRotateOnline 1 2>&1 | Out-Null

    # Set service to start automatically
    Set-Service -Name $service2Name -StartupType Automatic -ErrorAction SilentlyContinue

    Write-Host "✓ $service2Name installed successfully"
    Write-Host "  Exe: $service2Exe"
    Write-Host "  Args: $service2Args"
    Write-Host "  Working Directory: $service2WorkDir"
    Write-Host "  Log: $service2LogFile"
} catch {
    Write-Host "✗ Failed to install $service2Name: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# ============================================================================
# Step 4: Start Services
# ============================================================================

Write-Host "Step 4: Start Services" -ForegroundColor Yellow
Write-Host "-" * 50

try {
    Write-Host "Starting $service1Name..."
    Start-Service -Name $service1Name -ErrorAction Stop
    Start-Sleep -Seconds 2
    $service1Status = (Get-Service -Name $service1Name).Status
    Write-Host "✓ $service1Name status: $service1Status"
} catch {
    Write-Host "✗ Failed to start $service1Name: $_" -ForegroundColor Red
}

try {
    Write-Host "Starting $service2Name..."
    Start-Service -Name $service2Name -ErrorAction Stop
    Start-Sleep -Seconds 2
    $service2Status = (Get-Service -Name $service2Name).Status
    Write-Host "✓ $service2Name status: $service2Status"
} catch {
    Write-Host "✗ Failed to start $service2Name: $_" -ForegroundColor Red
}

Write-Host ""

# ============================================================================
# Summary
# ============================================================================

Write-Host "Installation Summary" -ForegroundColor Cyan
Write-Host "===================="
Write-Host "✓ NSSM installed at: $nssm"
Write-Host "✓ Service 1: $service1Name (Playwright MCP on port 9090)"
Write-Host "✓ Service 2: $service2Name (PyMCP-FS)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Verify services: Get-Service $service1Name, $service2Name"
Write-Host "  2. View logs: Get-Content $service1LogFile -Tail 20"
Write-Host "  3. Test: .\deploy\03d_verify_mcp_servers.ps1"
Write-Host ""
Write-Host "To stop a service: Stop-Service -Name '$service1Name'"
Write-Host "To remove a service: & '$nssm' remove '$service1Name' confirm"
