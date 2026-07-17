param(
  [string]$Destination = (
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssh\nice_assistant_deploy_ed25519'
  )
)

$ErrorActionPreference = 'Stop'
$destinationPath = [IO.Path]::GetFullPath($Destination)
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$repositoryPrefix = $repositoryRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if (
  $destinationPath.Equals($repositoryRoot, [StringComparison]::OrdinalIgnoreCase) -or
  $destinationPath.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)
) {
  throw 'The deployment private key must be stored outside the repository.'
}
$directory = Split-Path -Parent $destinationPath
if (-not $directory) { throw 'Destination must include a protected directory.' }
New-Item -ItemType Directory -Force -Path $directory | Out-Null
if ((Test-Path -LiteralPath $destinationPath) -or (Test-Path -LiteralPath "$destinationPath.pub")) {
  throw 'The dedicated deployment key already exists.'
}
& ssh-keygen.exe -q -t ed25519 -N '' -C 'nice-assistant-deploy-guard' -f $destinationPath
if ($LASTEXITCODE -ne 0) { throw 'Dedicated deployment key generation failed.' }
Write-Output "Dedicated deployment public key: $destinationPath.pub"
