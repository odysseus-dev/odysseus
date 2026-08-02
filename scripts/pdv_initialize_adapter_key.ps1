#Requires -Version 5.1
param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [Alias("Target")]
    [string]$KeyFile = "",
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($RepositoryRoot)
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $root "data\pdv-integration-v1"))
if (-not $KeyFile) { $KeyFile = Join-Path $allowedRoot "adapter.key" }
$targetPath = [System.IO.Path]::GetFullPath($KeyFile)
$prefix = $allowedRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $targetPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    $failure = [ordered]@{ ok = $false; error = "key target must stay inside the PDV runtime directory" }
    if ($Json) { $failure | ConvertTo-Json -Compress } else { $failure | Format-List }
    exit 1
}

$created = $false
if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($targetPath)) -Force | Out-Null
    $bytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $keyText = [Convert]::ToHexString($bytes).ToLowerInvariant()
    $encoded = [System.Text.Encoding]::ASCII.GetBytes($keyText)
    $stream = [System.IO.File]::Open($targetPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try { $stream.Write($encoded, 0, $encoded.Length) } finally { $stream.Dispose() }
    $created = $true
}

function Test-KeyAclRestricted {
    param([string]$Path)
    try {
        $identityName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $candidate = Get-Acl -LiteralPath $Path -ErrorAction Stop
        $rules = @($candidate.Access)
        return $candidate.AreAccessRulesProtected -and $rules.Count -eq 1 -and
            -not $rules[0].IsInherited -and $rules[0].AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
            $rules[0].IdentityReference.Value -eq $identityName -and
            (($rules[0].FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -eq [System.Security.AccessControl.FileSystemRights]::FullControl)
    } catch { return $false }
}

$aclRestricted = Test-KeyAclRestricted $targetPath
if (-not $aclRestricted) {
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $security = Get-Acl -LiteralPath $targetPath
        $security.SetAccessRuleProtection($true, $false)
        foreach ($existingRule in @($security.Access)) { [void]$security.RemoveAccessRuleAll($existingRule) }
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($identity.Name, [System.Security.AccessControl.FileSystemRights]::FullControl, [System.Security.AccessControl.AccessControlType]::Allow)
        $security.AddAccessRule($rule)
        $security.SetOwner($identity.User)
        Set-Acl -LiteralPath $targetPath -AclObject $security
        $aclRestricted = Test-KeyAclRestricted $targetPath
    } catch { $aclRestricted = $false }
}

if (-not $aclRestricted) {
    if ($created) { Remove-Item -LiteralPath $targetPath -Force -ErrorAction SilentlyContinue }
    $failure = [ordered]@{ ok = $false; created = $created; aclRestricted = $false; error = "adapter key ACL restriction failed" }
    if ($Json) { $failure | ConvertTo-Json -Compress } else { $failure | Format-List }
    exit 1
}

$keyBytes = [System.IO.File]::ReadAllBytes($targetPath)
$keyText = [System.Text.Encoding]::ASCII.GetString($keyBytes)
if ($keyBytes.Length -ne 64 -or $keyText -notmatch '^[a-f0-9]{64}$') { throw "Existing adapter key is not a 32-byte hex credential" }
$fingerprint = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($keyBytes)).ToLowerInvariant()
$report = [ordered]@{ ok = $true; created = $created; fingerprintSha256 = $fingerprint; aclRestricted = $aclRestricted; keyBytes = $keyBytes.Length }
if ($Json) { $report | ConvertTo-Json -Compress } else { $report | Format-List }
