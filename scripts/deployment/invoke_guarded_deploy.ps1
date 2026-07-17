param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('inspect', 'backup', 'deploy', 'health', 'logs', 'rollback', 'update-guard', 'rollback-guard')]
  [string]$Action,
  [string]$Digest = '',
  [string]$Config = '.local/deployment/remote.json'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
  throw 'The ignored deployment remote configuration is missing.'
}
$settings = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
if (-not $settings.host_alias -or -not $settings.key_path) {
  throw 'The deployment remote configuration requires host_alias and key_path.'
}
$hostAlias = [string]$settings.host_alias
$keySetting = [string]$settings.key_path
if ($hostAlias -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') {
  throw 'The configured SSH host alias is invalid.'
}
$userProfile = [Environment]::GetFolderPath('UserProfile')
if ($keySetting -match '^(?:~|\$HOME)[\\/](.+)$') {
  $keyPath = Join-Path $userProfile $Matches[1]
} elseif ([IO.Path]::IsPathRooted($keySetting)) {
  $keyPath = $keySetting
} else {
  throw 'The deployment key path must be absolute or start with $HOME.'
}
$keyPath = [IO.Path]::GetFullPath($keyPath)
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$repositoryPrefix = $repositoryRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if (
  $keyPath.Equals($repositoryRoot, [StringComparison]::OrdinalIgnoreCase) -or
  $keyPath.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)
) {
  throw 'The deployment private key must be stored outside the repository.'
}
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
  throw 'The dedicated deployment private key is unavailable.'
}
if ($Action -in @('deploy', 'update-guard')) {
  if ($Digest -notmatch '^ghcr\.io/[a-z0-9_.-]+/nice-assistant@sha256:[0-9a-f]{64}$') {
    throw "$Action requires an immutable Nice Assistant GHCR digest."
  }
  $remoteCommand = "$Action $Digest"
} elseif ($Digest) {
  throw 'Digest is accepted only for deploy and update-guard.'
} else {
  $remoteCommand = $Action
}

& ssh.exe -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i $keyPath -- $hostAlias $remoteCommand
if ($LASTEXITCODE -ne 0) {
  throw "Guarded deployment action failed with exit code $LASTEXITCODE."
}
