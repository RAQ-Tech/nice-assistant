param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('inspect', 'backup', 'deploy', 'health', 'logs', 'rollback')]
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
$keyPath = [string]$settings.key_path
if ($hostAlias -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') {
  throw 'The configured SSH host alias is invalid.'
}
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
  throw 'The dedicated deployment private key is unavailable.'
}
if ($Action -eq 'deploy') {
  if ($Digest -notmatch '^ghcr\.io/[a-z0-9_.-]+/nice-assistant@sha256:[0-9a-f]{64}$') {
    throw 'Deploy requires an immutable Nice Assistant GHCR digest.'
  }
  $remoteCommand = "deploy $Digest"
} elseif ($Digest) {
  throw 'Digest is accepted only for deploy.'
} else {
  $remoteCommand = $Action
}

& ssh.exe -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i $keyPath -- $hostAlias $remoteCommand
if ($LASTEXITCODE -ne 0) {
  throw "Guarded deployment action failed with exit code $LASTEXITCODE."
}
