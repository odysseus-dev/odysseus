param(
    [string]$OutputDir = "release-assets/android"
)

$ErrorActionPreference = "Stop"

function Read-PropertiesFile {
    param([string]$Path)

    $props = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }
        $index = $trimmed.IndexOf("=")
        if ($index -lt 1) {
            continue
        }
        $key = $trimmed.Substring(0, $index).Trim()
        $value = $trimmed.Substring($index + 1).Trim()
        $props[$key] = $value
    }
    return $props
}

function Resolve-AndroidSdk {
    if ($env:ANDROID_HOME -and (Test-Path -LiteralPath $env:ANDROID_HOME)) {
        return (Resolve-Path -LiteralPath $env:ANDROID_HOME).Path
    }
    if ($env:ANDROID_SDK_ROOT -and (Test-Path -LiteralPath $env:ANDROID_SDK_ROOT)) {
        return (Resolve-Path -LiteralPath $env:ANDROID_SDK_ROOT).Path
    }

    $defaultSdk = Join-Path $env:LOCALAPPDATA "Android/Sdk"
    if (Test-Path -LiteralPath $defaultSdk) {
        return (Resolve-Path -LiteralPath $defaultSdk).Path
    }

    throw "Android SDK not found. Set ANDROID_HOME or ANDROID_SDK_ROOT."
}

function Resolve-ApkSigner {
    param([string]$SdkRoot)

    $buildToolsRoot = Join-Path $SdkRoot "build-tools"
    if (-not (Test-Path -LiteralPath $buildToolsRoot)) {
        throw "Android SDK build-tools not found under $buildToolsRoot."
    }

    $buildToolsDir = Get-ChildItem -LiteralPath $buildToolsRoot -Directory |
        Sort-Object Name -Descending |
        Select-Object -First 1
    if (-not $buildToolsDir) {
        throw "No Android SDK build-tools versions are installed."
    }

    $apkSigner = Join-Path $buildToolsDir.FullName "apksigner.bat"
    if (-not (Test-Path -LiteralPath $apkSigner)) {
        throw "apksigner.bat not found in $($buildToolsDir.FullName)."
    }
    return $apkSigner
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$androidDir = Join-Path $repoRoot "android"
$appBuildGradle = Join-Path $androidDir "app/build.gradle"
$keystorePropsPath = Join-Path $androidDir "keystore.properties"

if (-not (Test-Path -LiteralPath $keystorePropsPath)) {
    throw "Missing android/keystore.properties. Copy android/keystore.properties.example and fill in the local sideload signing values."
}

$props = Read-PropertiesFile -Path $keystorePropsPath
$requiredKeys = @("storeFile", "storePassword", "keyAlias", "keyPassword")
foreach ($key in $requiredKeys) {
    if (-not $props.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($props[$key])) {
        throw "android/keystore.properties is missing required key: $key"
    }
}

$storePath = $props["storeFile"]
if (-not [System.IO.Path]::IsPathRooted($storePath)) {
    $storePath = Join-Path $androidDir $storePath
}
if (-not (Test-Path -LiteralPath $storePath)) {
    throw "Release keystore file not found: $storePath"
}

$buildText = Get-Content -LiteralPath $appBuildGradle -Raw
if ($buildText -notmatch 'versionName\s*=\s*"([^"]+)"') {
    throw "Could not read versionName from android/app/build.gradle."
}
$versionName = $Matches[1]

$gradleWrapper = Join-Path $androidDir "gradlew.bat"
if (-not (Test-Path -LiteralPath $gradleWrapper)) {
    throw "Gradle wrapper not found: $gradleWrapper"
}

if ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $resolvedOutputDir = $OutputDir
} else {
    $resolvedOutputDir = Join-Path $repoRoot $OutputDir
}
New-Item -ItemType Directory -Force -Path $resolvedOutputDir | Out-Null

Push-Location $androidDir
try {
    & $gradleWrapper assembleRelease
    if ($LASTEXITCODE -ne 0) {
        throw "Gradle release build failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

$releaseApkDir = Join-Path $androidDir "app/build/outputs/apk/release"
$sourceApk = Join-Path $releaseApkDir "app-release.apk"
if (-not (Test-Path -LiteralPath $sourceApk)) {
    $sourceApk = Get-ChildItem -LiteralPath $releaseApkDir -Filter "app-release*.apk" |
        Where-Object { $_.Name -notmatch "unsigned" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $sourceApk -or -not (Test-Path -LiteralPath $sourceApk)) {
    throw "Signed release APK not found. Refusing to use unsigned release output."
}
if ((Split-Path -Leaf $sourceApk) -match "unsigned") {
    throw "Refusing to package unsigned APK: $sourceApk"
}

$targetName = "Odysseus-Simple-Signal-$versionName-sideload.apk"
$targetApk = Join-Path $resolvedOutputDir $targetName
Copy-Item -LiteralPath $sourceApk -Destination $targetApk -Force

$apkSigner = Resolve-ApkSigner -SdkRoot (Resolve-AndroidSdk)
$verifyOutput = & $apkSigner verify --verbose --print-certs $targetApk 2>&1
if ($LASTEXITCODE -ne 0) {
    $verifyOutput | ForEach-Object { Write-Host $_ }
    throw "apksigner verification failed for $targetApk."
}

$verifyText = ($verifyOutput | Out-String)
if ($verifyText -match "CN=Android Debug") {
    $verifyOutput | ForEach-Object { Write-Host $_ }
    throw "Refusing to publish debug-signed APK."
}

$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $targetApk
$shaFile = Join-Path $resolvedOutputDir "$targetName.sha256"
Set-Content -LiteralPath $shaFile -Value "$($hash.Hash.ToLowerInvariant())  $targetName"

$latestName = "Odysseus-Simple-Signal-latest-sideload.apk"
$latestApk = Join-Path $resolvedOutputDir $latestName
Copy-Item -LiteralPath $targetApk -Destination $latestApk -Force
$latestShaFile = Join-Path $resolvedOutputDir "$latestName.sha256"
Set-Content -LiteralPath $latestShaFile -Value "$($hash.Hash.ToLowerInvariant())  $latestName"

Write-Host "Built sideload APK: $targetApk"
Write-Host "Latest copy: $latestApk"
Write-Host "SHA-256: $($hash.Hash.ToLowerInvariant())"
Write-Host "Signature:"
$verifyOutput | ForEach-Object { Write-Host $_ }
