param([string]$Destination = '.local/deployment/nice_assistant_deploy_ed25519')

$ErrorActionPreference = 'Stop'
$directory = Split-Path -Parent $Destination
if (-not $directory) { throw 'Destination must include an ignored directory.' }
New-Item -ItemType Directory -Force -Path $directory | Out-Null
if ((Test-Path -LiteralPath $Destination) -or (Test-Path -LiteralPath "$Destination.pub")) {
  throw 'The dedicated deployment key already exists.'
}
& ssh-keygen.exe -q -t ed25519 -N '' -C 'nice-assistant-deploy-guard' -f $Destination
if ($LASTEXITCODE -ne 0) { throw 'Dedicated deployment key generation failed.' }
Write-Output "Dedicated deployment public key: $Destination.pub"
