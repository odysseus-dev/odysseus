#Requires -Version 5.1
param(
    [string]$Version = "0.1.0",
    [switch]$AcceptWixEula
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\packaging\windows"
$PayloadDir = Join-Path $BuildRoot "payload"
$GeneratedWxs = Join-Path $BuildRoot "GeneratedPayload.wxs"
$DistDir = Join-Path $RepoRoot "dist\windows"
$MsiOut = Join-Path $DistDir "Odysseus.msi"

function Fail($Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Get-Wix {
    $localWix = Join-Path $RepoRoot "build\tools\wix.exe"
    if (Test-Path $localWix) { return $localWix }

    $cmd = Get-Command wix -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Fail "WiX Toolset CLI was not found. Install it with: dotnet tool install --tool-path .\build\tools wix"
    }
    return $cmd.Source
}

function Escape-Xml([string]$Value) {
    return [System.Security.SecurityElement]::Escape($Value)
}

function Get-RelativePathCompat([string]$BasePath, [string]$TargetPath) {
    $baseFull = (Resolve-Path $BasePath).Path
    $targetFull = (Resolve-Path $TargetPath).Path
    if (-not $baseFull.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $baseFull += [System.IO.Path]::DirectorySeparatorChar
    }
    $baseUri = New-Object System.Uri($baseFull)
    $targetUri = New-Object System.Uri($targetFull)
    return [System.Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString()).Replace("/", "\")
}

function New-WixId([string]$Prefix, [string]$Value) {
    $safe = [regex]::Replace($Value, "[^A-Za-z0-9_]", "_")
    if ($safe.Length -gt 58) {
        $hash = Get-StableGuid $Value
        $safe = $safe.Substring(0, 32) + "_" + $hash.Substring(1, 8)
    }
    if ($safe -match "^[0-9]") { $safe = "_" + $safe }
    return $Prefix + $safe
}

function Get-StableGuid([string]$Value) {
    $md5 = [System.Security.Cryptography.MD5]::Create()
    try {
        $bytes = $md5.ComputeHash([System.Text.Encoding]::UTF8.GetBytes("odysseus:" + $Value.ToLowerInvariant()))
        $hex = -join ($bytes | ForEach-Object { $_.ToString("x2") })
        return "{" + $hex.Substring(0, 8) + "-" + $hex.Substring(8, 4) + "-" + $hex.Substring(12, 4) + "-" + $hex.Substring(16, 4) + "-" + $hex.Substring(20, 12) + "}"
    } finally {
        $md5.Dispose()
    }
}

function Copy-Payload {
    if (Test-Path $PayloadDir) { Remove-Item -Recurse -Force -LiteralPath $PayloadDir }
    New-Item -ItemType Directory -Force -Path $PayloadDir | Out-Null

    $excludeDirs = @(".git", ".github", "venv", ".venv", "data", "logs", "dist", "build", "node_modules", ".pytest_cache", "__pycache__", "packaging", "tests")
    $excludeFiles = @(".env", "*.pyc", "*.pyo", "*.log", "*.db", "*.sqlite", "*.sqlite3")
    $args = @($RepoRoot, $PayloadDir, "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/XD") + $excludeDirs + @("/XF") + $excludeFiles
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -gt 7) { Fail "robocopy failed while staging payload." }

    Copy-Item -Recurse -Force -Path (Join-Path $PSScriptRoot "runtime") -Destination (Join-Path $PayloadDir "runtime")
    Copy-Item -Force -Path (Join-Path $PSScriptRoot "docker-compose.windows.yml") -Destination (Join-Path $PayloadDir "runtime\docker-compose.yml.template")
}

function Write-GeneratedPayloadWxs {
    $files = Get-ChildItem -Path $PayloadDir -Recurse -File | Sort-Object FullName
    $dirs = Get-ChildItem -Path $PayloadDir -Recurse -Directory | Sort-Object FullName

    $dirIds = @{ "" = "INSTALLFOLDER" }
    foreach ($dir in $dirs) {
        $rel = Get-RelativePathCompat $PayloadDir $dir.FullName
        $dirIds[$rel] = New-WixId "dir_" $rel
    }

    $children = @{}
    foreach ($rel in $dirIds.Keys) { $children[$rel] = New-Object System.Collections.ArrayList }
    foreach ($dir in $dirs) {
        $rel = Get-RelativePathCompat $PayloadDir $dir.FullName
        $parent = Split-Path $rel -Parent
        if ($null -eq $parent) { $parent = "" }
        [void]$children[$parent].Add($rel)
    }

    function Write-DirectoryTree([string]$ParentRel, [int]$Indent) {
        $pad = " " * $Indent
        foreach ($childRel in ($children[$ParentRel] | Sort-Object)) {
            $name = Split-Path $childRel -Leaf
            $id = $dirIds[$childRel]
            if ($children.ContainsKey($childRel) -and $children[$childRel].Count -gt 0) {
                $script:lines.Add("$pad<Directory Id=`"$id`" Name=`"$(Escape-Xml $name)`">") | Out-Null
                Write-DirectoryTree $childRel ($Indent + 2)
                $script:lines.Add("$pad</Directory>") | Out-Null
            } else {
                $script:lines.Add("$pad<Directory Id=`"$id`" Name=`"$(Escape-Xml $name)`" />") | Out-Null
            }
        }
    }

    $script:lines = New-Object System.Collections.ArrayList
    $script:lines.Add('<?xml version="1.0" encoding="UTF-8"?>') | Out-Null
    $script:lines.Add('<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">') | Out-Null
    $script:lines.Add('  <Fragment>') | Out-Null
    $script:lines.Add('    <DirectoryRef Id="INSTALLFOLDER">') | Out-Null
    Write-DirectoryTree "" 6
    $script:lines.Add('    </DirectoryRef>') | Out-Null
    $script:lines.Add('  </Fragment>') | Out-Null
    $script:lines.Add('  <Fragment>') | Out-Null
    $script:lines.Add('    <ComponentGroup Id="PayloadFiles">') | Out-Null

    foreach ($file in $files) {
        $rel = Get-RelativePathCompat $PayloadDir $file.FullName
        $parent = Split-Path $rel -Parent
        if ($null -eq $parent) { $parent = "" }
        $componentId = New-WixId "cmp_" $rel
        $fileId = New-WixId "fil_" $rel
        $source = '$(var.PayloadDir)\' + $rel
        $guid = Get-StableGuid $rel
        $script:lines.Add("      <Component Id=`"$componentId`" Directory=`"$($dirIds[$parent])`" Guid=`"$guid`">") | Out-Null
        $script:lines.Add("        <File Id=`"$fileId`" Source=`"$(Escape-Xml $source)`" KeyPath=`"yes`" />") | Out-Null
        $script:lines.Add("      </Component>") | Out-Null
    }

    $script:lines.Add('    </ComponentGroup>') | Out-Null
    $script:lines.Add('  </Fragment>') | Out-Null
    $script:lines.Add('</Wix>') | Out-Null

    New-Item -ItemType Directory -Force -Path (Split-Path $GeneratedWxs) | Out-Null
    Set-Content -Path $GeneratedWxs -Value $script:lines -Encoding UTF8
}

New-Item -ItemType Directory -Force -Path $BuildRoot, $DistDir | Out-Null
Copy-Payload
Write-GeneratedPayloadWxs

$wix = Get-Wix
$wixArgs = @("build")
if ($AcceptWixEula) { $wixArgs += @("-acceptEula", "wix7") }
$wixArgs += @(
    (Join-Path $PSScriptRoot "Product.wxs"),
    $GeneratedWxs,
    "-arch", "x64",
    "-d", "PayloadDir=$PayloadDir",
    "-d", "ProductVersion=$Version",
    "-o", $MsiOut
)

& $wix @wixArgs
if ($LASTEXITCODE -ne 0) { Fail "WiX MSI build failed." }

Write-Host "Built $MsiOut" -ForegroundColor Green
