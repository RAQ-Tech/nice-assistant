param([string]$Image = 'nice-assistant:local')

$ErrorActionPreference = 'Stop'
$csrfHeaders = @{ 'X-Nice-Assistant-CSRF' = '1' }
$PSDefaultParameterValues['Invoke-RestMethod:Headers'] = $csrfHeaders
$PSDefaultParameterValues['Invoke-WebRequest:Headers'] = $csrfHeaders

function Get-FreePort {
  $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  $listener.Start()
  $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
  $listener.Stop()
  return $port
}

function Wait-NiceJob {
  param(
    [string]$Base,
    [Microsoft.PowerShell.Commands.WebRequestSession]$Session,
    [string]$JobId,
    [int]$TimeoutSeconds = 20
  )
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    $job = Invoke-RestMethod -Uri "$Base/api/v1/jobs/$JobId" -WebSession $Session
    if ($job.status -in @('completed', 'failed', 'cancelled')) { return $job }
    Start-Sleep -Milliseconds 100
  } while ([DateTime]::UtcNow -lt $deadline)
  return $job
}

$name = 'nice-assistant-container-smoke-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$fakePort = Get-FreePort
$appPort = Get-FreePort
$tempRoot = Join-Path $env:TEMP $name
$dataPath = Join-Path $tempRoot 'data'
$archivePath = Join-Path $tempRoot 'archive'
New-Item -ItemType Directory -Path $dataPath, $archivePath -Force | Out-Null
$referencePath = Join-Path $tempRoot 'identity-reference.ppm'
$header = [Text.Encoding]::ASCII.GetBytes("P6`n64 64`n255`n")
$pixels = [byte[]]::new(64 * 64 * 3)
for ($offset = 0; $offset -lt $pixels.Length; $offset += 3) {
  $pixels[$offset] = 180
  $pixels[$offset + 1] = 80
  $pixels[$offset + 2] = 70
}
$referenceBytes = [byte[]]::new($header.Length + $pixels.Length)
$header.CopyTo($referenceBytes, 0)
$pixels.CopyTo($referenceBytes, $header.Length)
[IO.File]::WriteAllBytes($referencePath, $referenceBytes)
$fake = Start-Process `
  -FilePath 'py' `
  -ArgumentList @('-3', 'scripts/smoke_check.py', '--fake-ollama-port', $fakePort) `
  -PassThru `
  -WindowStyle Hidden `
  -WorkingDirectory $PSScriptRoot\..
$containerExists = $false
$containerRunning = $false

try {
  docker run -d `
    --name $name `
    -p "127.0.0.1:${appPort}:3000" `
    -e "OLLAMA_BASE_URL=http://host.docker.internal:$fakePort" `
    -e 'NICE_ASSISTANT_MASTER_KEY=container-smoke-key' `
    -e 'ALLOW_PUBLIC_SIGNUP=0' `
    -e 'SYNC_PROJECT_ON_START=0' `
    -v "${dataPath}:/data" `
    -v "${archivePath}:/archives" `
    $Image | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'docker run failed' }
  $containerExists = $true
  $containerRunning = $true
  $base = "http://127.0.0.1:$appPort"
  $deadline = [DateTime]::UtcNow.AddSeconds(30)
  $health = $null
  do {
    try {
      $health = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 2
      if ($health.ok) { break }
    } catch {
      $health = $null
    }
    Start-Sleep -Milliseconds 250
  } while ([DateTime]::UtcNow -lt $deadline)
  if (-not $health.ok) { throw 'container did not become healthy' }
  $deploymentReady = Invoke-RestMethod -Uri "$base/ready"
  if (-not $deploymentReady.ready) { throw 'container readiness failed' }

  $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
  $credentials = @{ username = 'owner'; password = 'pass1234' } | ConvertTo-Json
  $created = Invoke-RestMethod -Method Post -Uri "$base/api/v1/users" -ContentType 'application/json' -Body $credentials
  Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/session" `
    -ContentType 'application/json' `
    -Body $credentials `
    -WebSession $session | Out-Null
  $observability = Invoke-RestMethod -Uri "$base/api/v1/admin/observability" -WebSession $session
  if (-not $observability.requests -or -not $observability.queues -or -not $observability.storage) {
    throw 'admin observability contract failed'
  }

  $profiles = Invoke-RestMethod -Uri "$base/api/v1/task-models" -WebSession $session
  if ($profiles.items.Count -ne 4) { throw 'task model profiles were not seeded' }
  $ready = Invoke-RestMethod -Method Post -Uri "$base/api/v1/task-models/title_generation/check" -WebSession $session
  if (-not $ready.ready -or $ready.effective_model -ne 'smoke-model') { throw 'task model readiness failed' }

  $coordinationPolicy = @{
    mode = 'observe'
    reserve_vram_mb = 512
    max_wait_seconds = 30
    poll_interval_seconds = 1
    authorizations = @()
  }
  $coordination = Invoke-RestMethod `
    -Method Put `
    -Uri "$base/api/v1/admin/resource-coordination" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body ($coordinationPolicy | ConvertTo-Json)
  if ($coordination.settings.mode -ne 'observe') { throw 'resource coordination mode did not change' }
  $coordinationPolicy.mode = 'disabled'
  $coordination = Invoke-RestMethod `
    -Method Put `
    -Uri "$base/api/v1/admin/resource-coordination" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body ($coordinationPolicy | ConvertTo-Json)
  if ($coordination.settings.mode -ne 'disabled') { throw 'resource coordination disabled mode was not restored' }

  $catalog = Invoke-RestMethod -Uri "$base/api/v1/media-catalog" -WebSession $session
  if ($catalog.settings.vram_budget_mb -ne 10240 -or $catalog.resources.Count -ne 0) {
    throw 'media catalog migration defaults failed'
  }
  $catalogResource = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/media-catalog/resources" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      resource_type = 'model'
      kind = 'image'
      name = 'Container fantasy model'
      provider_key = 'local-image'
      backend = 'automatic1111'
      external_id = 'container-fantasy.safetensors'
      enabled = $true
      priority = 90
      operations = @('generate')
      domains = @('fantasy')
      content_tags = @('general')
      features = @('text_to_image')
      estimated_vram_mb = 4096
      estimated_load_seconds = 3
      default_settings = @{ steps = 20; cfg_scale = 7 }
      notes = 'Container smoke resource'
      compatible_model_ids = @()
    } | ConvertTo-Json -Depth 5)
  $plan = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/media-catalog/plan-previews" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      kind = 'image'
      operation = 'generate'
      domains = @('fantasy')
      content_tags = @('general')
      required_features = @('text_to_image')
    } | ConvertTo-Json -Depth 4)
  if ($plan.status -ne 'ready' -or $plan.source -ne 'coordinator' -or
      $plan.selected_resources[0].id -ne $catalogResource.id -or $plan.estimated_vram_mb -ne 4096) {
    throw 'media catalog deterministic planning failed'
  }

  $workspace = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/workspaces" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{ name = 'Container Workspace' } | ConvertTo-Json)
  $persona = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/personas" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      workspace_id = $workspace.id
      name = 'Container Persona'
      system_prompt = 'Be concise.'
      default_model = 'smoke-model'
    } | ConvertTo-Json)

  $identitySettings = Invoke-RestMethod -Uri "$base/api/v1/identity-validation/settings" -WebSession $session
  if ($identitySettings.provider -ne 'disabled' -or $identitySettings.ready) {
    throw 'identity verifier defaults were not truthful'
  }
  $identityCheck = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/identity-validation/check" `
    -WebSession $session
  if ($identityCheck.ready -or $identityCheck.status -ne 'unavailable') {
    throw 'disabled identity verifier readiness was not truthful'
  }
  $identity = Invoke-RestMethod `
    -Uri "$base/api/v1/personas/$($persona.id)/visual-identity" `
    -WebSession $session
  if ($identity.consent_status -ne 'not_granted' -or $identity.validation_ready) {
    throw 'persona identity profile defaults failed'
  }
  $identity = Invoke-RestMethod `
    -Method Put `
    -Uri "$base/api/v1/personas/$($persona.id)/visual-identity" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      appearance_description = 'short copper hair and green eyes'
      acceptance_threshold = 0.78
      max_generation_attempts = 2
      failure_policy = 'block_claim'
      conditioning_fallback = 'require_conditioning'
    } | ConvertTo-Json)
  if ($identity.appearance_description -ne 'short copper hair and green eyes' -or
      $identity.conditioning_fallback -ne 'require_conditioning') {
    throw 'persona identity profile did not persist'
  }
  Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/personas/$($persona.id)/visual-identity/consent" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{ attested = $true } | ConvertTo-Json) | Out-Null
  $reference = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/personas/$($persona.id)/visual-identity/references" `
    -WebSession $session `
    -Form @{
      file = Get-Item -LiteralPath $referencePath
      provenance = 'user_upload'
      attested = 'true'
    }
  if ($reference.review_status -ne 'pending' -or $reference.content_type -ne 'image/jpeg') {
    throw 'identity reference normalization or review state failed'
  }
  $approvedReference = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/identity-references/$($reference.id)/approval" `
    -WebSession $session
  if ($approvedReference.review_status -ne 'approved') { throw 'identity reference approval failed' }
  $protectedReference = Invoke-WebRequest -Uri "$base$($reference.content_url)" -WebSession $session
  if ($protectedReference.StatusCode -ne 200 -or $protectedReference.RawContentLength -le 0) {
    throw 'protected identity reference delivery failed'
  }

  $fallbackIdentityChat = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/chats" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      workspace_id = $workspace.id
      persona_id = $persona.id
      memory_mode = 'off'
      title = 'Identity fallback smoke'
    } | ConvertTo-Json)
  $fallbackIdentityTurn = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/chats/$($fallbackIdentityChat.id)/turns" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      text = 'Create the container identity portrait before setup'
      memory_mode = 'off'
      model = 'smoke-model'
    } | ConvertTo-Json)
  $fallbackIdentityJob = Wait-NiceJob $base $session $fallbackIdentityTurn.job.id
  if ($fallbackIdentityJob.status -ne 'completed') { throw 'identity fallback planning turn failed' }
  $fallbackIdentityFollowup = Wait-NiceJob $base $session $fallbackIdentityJob.result.followup_job_id
  if ($fallbackIdentityFollowup.status -ne 'completed') { throw 'identity fallback follow-up failed' }
  $fallbackIdentityRequests = Invoke-RestMethod `
    -Uri "$base/api/v1/capability-requests?chat_id=$($fallbackIdentityChat.id)" `
    -WebSession $session
  if ($fallbackIdentityRequests.items.Count -ne 1) {
    throw 'identity fallback capability request was not created'
  }
  $blockedIdentityRequest = $fallbackIdentityRequests.items[0]
  if ($blockedIdentityRequest.media_plan.status -ne 'blocked' -or
      $blockedIdentityRequest.media_plan.identity_conditioning.persona_id -ne $persona.id) {
    throw 'strict missing-workflow identity plan was not blocked with its originating persona'
  }
  $identity = Invoke-RestMethod `
    -Method Put `
    -Uri "$base/api/v1/personas/$($persona.id)/visual-identity" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      appearance_description = 'short copper hair and green eyes'
      acceptance_threshold = 0.78
      max_generation_attempts = 2
      failure_policy = 'block_claim'
      conditioning_fallback = 'allow_unconditioned'
    } | ConvertTo-Json)
  if ($identity.conditioning_fallback -ne 'allow_unconditioned') {
    throw 'identity missing-conditioning fallback did not persist'
  }
  $fallbackReplan = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/capability-requests/$($blockedIdentityRequest.id)/replan" `
    -WebSession $session
  $fallbackPlan = $fallbackReplan.media_plan
  $fallbackWarnings = $fallbackPlan.explanation.warnings -join ' '
  if ($fallbackPlan.status -ne 'ready' -or
      $fallbackPlan.identity_conditioning.status -ne 'unconditioned' -or
      $fallbackPlan.identity_conditioning.claim_status -ne 'unverified' -or
      $fallbackPlan.identity_conditioning.conditioning_fallback -ne 'allow_unconditioned' -or
      $fallbackWarnings -notmatch 'No persona identity reference will be applied') {
    throw 'identity fallback replan was not ready, disclosed, and explicitly unverified'
  }
  $fallbackEvents = Invoke-RestMethod `
    -Uri "$base/api/v1/capability-requests/$($blockedIdentityRequest.id)/events" `
    -WebSession $session
  if (@($fallbackEvents.events)[-1].action -ne 'replanned') {
    throw 'identity fallback replan audit was not recorded'
  }

  $identityModel = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/media-catalog/resources" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      resource_type = 'model'
      kind = 'image'
      name = 'Container identity model'
      provider_key = 'local-image'
      backend = 'comfyui'
      external_id = 'container-identity.safetensors'
      enabled = $true
      priority = 95
      operations = @('generate')
      domains = @('fantasy')
      content_tags = @('general')
      features = @('text_to_image')
      estimated_vram_mb = 6144
      estimated_load_seconds = 4
      default_settings = @{}
      notes = 'Container identity smoke model'
      compatible_model_ids = @()
    } | ConvertTo-Json -Depth 6)
  $identityWorkflow = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/media-catalog/resources" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      resource_type = 'workflow'
      kind = 'image'
      name = 'Container reviewed identity workflow'
      provider_key = 'local-image'
      backend = 'comfyui'
      external_id = 'container-reviewed-identity'
      enabled = $true
      priority = 100
      operations = @('generate')
      domains = @('fantasy')
      content_tags = @('general')
      features = @('identity_control')
      estimated_vram_mb = 0
      estimated_load_seconds = 1
      default_settings = @{
        workflow_patch = @{
          '100' = @{ class_type = 'LoadImage'; inputs = @{ image = 'placeholder.jpg' } }
          '101' = @{ class_type = 'IdentityAdapter'; inputs = @{ reference = @('100', 0) } }
        }
        identity_image_bindings = @(@{ node_id = '100'; input_name = 'image' })
      }
      notes = 'Container reviewed identity workflow'
      compatible_model_ids = @($identityModel.id)
    } | ConvertTo-Json -Depth 10)
  if ($identityWorkflow.features -notcontains 'identity_control') {
    throw 'identity workflow was not stored with its declared feature'
  }

  $identityChat = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/chats" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      workspace_id = $workspace.id
      persona_id = $persona.id
      memory_mode = 'off'
      title = 'Identity smoke'
    } | ConvertTo-Json)
  $identityTurn = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/chats/$($identityChat.id)/turns" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      text = 'Create the container identity portrait'
      memory_mode = 'off'
      model = 'smoke-model'
    } | ConvertTo-Json)
  $identityJob = Wait-NiceJob $base $session $identityTurn.job.id
  if ($identityJob.status -ne 'completed') { throw 'identity planning turn failed' }
  $identityFollowup = Wait-NiceJob $base $session $identityJob.result.followup_job_id
  if ($identityFollowup.status -ne 'completed') { throw 'identity planning follow-up failed' }
  $identityRequests = Invoke-RestMethod `
    -Uri "$base/api/v1/capability-requests?chat_id=$($identityChat.id)" `
    -WebSession $session
  if ($identityRequests.items.Count -ne 1) { throw 'identity capability request was not created' }
  $identityPlan = $identityRequests.items[0].media_plan
  if ($identityPlan.status -ne 'ready' -or
      $identityPlan.identity_conditioning.status -ne 'ready' -or
      $identityPlan.identity_conditioning.reference_id -ne $approvedReference.id -or
      $identityPlan.identity_conditioning.workflow_resource_id -ne $identityWorkflow.id -or
      $identityPlan.identity_conditioning.verification_status -ne 'not_evaluated') {
    throw 'installed identity-conditioned plan was incomplete or misleading'
  }

  $schemaRaw = docker exec $name python -c "import sqlite3,json; c=sqlite3.connect('/data/nice_assistant.db'); print(json.dumps({'version':c.execute('SELECT version_num FROM alembic_version').fetchone()[0],'plan_columns':[r[1] for r in c.execute('PRAGMA table_info(media_execution_plans)')],'media_columns':[r[1] for r in c.execute('PRAGMA table_info(media_files)')],'attempt_columns':[r[1] for r in c.execute('PRAGMA table_info(media_generation_attempts)')]}))"
  if ($LASTEXITCODE -ne 0) { throw 'media correction schema inspection failed' }
  $schema = $schemaRaw | ConvertFrom-Json
  if ($schema.version -ne '0016_identity_fallback' -or
      $schema.plan_columns -notcontains 'identity_conditioning_json' -or
      $schema.media_columns -notcontains 'generation_plan_id' -or
      $schema.attempt_columns -notcontains 'attempt_number') {
    throw 'installed image did not migrate to the media correction schema'
  }

  $withdrawnIdentity = Invoke-RestMethod `
    -Method Delete `
    -Uri "$base/api/v1/personas/$($persona.id)/visual-identity/consent" `
    -WebSession $session
  if ($withdrawnIdentity.consent_status -ne 'withdrawn') { throw 'identity consent withdrawal failed' }
  $deletedReference = Invoke-WebRequest `
    -Uri "$base$($reference.content_url)" `
    -WebSession $session `
    -SkipHttpErrorCheck
  if ($deletedReference.StatusCode -ne 404) { throw 'withdrawn identity reference remained accessible' }

  $chat = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/chats" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      workspace_id = $workspace.id
      persona_id = $persona.id
      memory_mode = 'off'
      title = 'New chat'
    } | ConvertTo-Json)
  $accepted = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/chats/$($chat.id)/turns" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      text = 'Container smoke conversation'
      memory_mode = 'off'
      model = 'smoke-model'
    } | ConvertTo-Json)
  $job = Wait-NiceJob $base $session $accepted.job.id
  if ($job.status -ne 'completed' -or $job.result.text -ne 'Smoke model reply.') { throw 'container chat job failed' }
  $titleFollowup = Wait-NiceJob $base $session $job.result.followup_job_id
  if ($titleFollowup.status -ne 'completed') { throw 'container title follow-up failed' }
  $runs = Invoke-RestMethod -Uri "$base/api/v1/task-model-runs?role=title_generation" -WebSession $session
  if ($runs.items.Count -ne 1 -or $runs.items[0].status -ne 'fallback') {
    throw 'title fallback audit was not durable'
  }

  $cancelTurn = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/chats/$($chat.id)/turns" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{
      text = 'hold cancellation'
      memory_mode = 'off'
      model = 'smoke-model'
    } | ConvertTo-Json)
  $deadline = [DateTime]::UtcNow.AddSeconds(10)
  do {
    $cancelJob = Invoke-RestMethod -Uri "$base/api/v1/jobs/$($cancelTurn.job.id)" -WebSession $session
    if ($cancelJob.status -eq 'running') { break }
    Start-Sleep -Milliseconds 50
  } while ([DateTime]::UtcNow -lt $deadline)
  $cancelled = Invoke-RestMethod -Method Delete -Uri "$base/api/v1/jobs/$($cancelTurn.job.id)" -WebSession $session
  if ($cancelled.status -ne 'cancelled') { throw 'container cancellation failed' }

  $insertTemplate = @'
import sqlite3,time,pathlib
p=pathlib.Path('/data/images/container-media.bin')
p.parent.mkdir(parents=True,exist_ok=True)
p.write_bytes(b'protected-container-media')
c=sqlite3.connect('/data/nice_assistant.db')
c.execute("INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) VALUES(?,?,?,?,?,?,?)",('container-media','USER_ID',None,'image','container-media.bin',str(p),int(time.time())))
c.commit()
c.close()
'@
  $insert = $insertTemplate.Replace('USER_ID', $created.id)
  docker exec $name python -c $insert
  if ($LASTEXITCODE -ne 0) { throw 'protected media setup failed' }
  $media = Invoke-WebRequest -Uri "$base/api/v1/media/container-media" -WebSession $session
  if ($media.StatusCode -ne 200 -or $media.RawContentLength -ne 25) { throw 'protected media request failed' }

  $backup = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/admin/backups" `
    -WebSession $session `
    -ContentType 'application/json' `
    -Body (@{ include_media = $false } | ConvertTo-Json)
  $backupCheck = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/api/v1/admin/backups/$($backup.name)/verify" `
    -WebSession $session
  if (-not $backupCheck.ok -or $backupCheck.database_integrity -ne 'ok') {
    throw 'container backup restore drill failed'
  }
  Invoke-RestMethod -Method Delete -Uri "$base/api/v1/admin/backups/$($backup.name)" -WebSession $session | Out-Null

  docker stop -t 10 $name | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'container stop failed' }
  $containerRunning = $false
  $exitCode = docker inspect -f '{{.State.ExitCode}}' $name
  if ([int]$exitCode -ne 0) { throw "container did not shut down cleanly: $exitCode" }

  [ordered]@{
    health = 'ok'
    readiness_and_observability = 'ok'
    task_profiles = 'ok'
    task_readiness = 'ok'
    resource_coordination = 'ok'
    media_catalog = 'ok'
    persona_visual_identity = 'ok'
    identity_fallback_replan = 'ok'
    identity_conditioned_planning = 'ok'
    media_correction_migration = 'ok'
    chat_and_title_fallback = 'ok'
    cancellation = 'ok'
    protected_media = 'ok'
    backup_restore_drill = 'ok'
    clean_shutdown = 'ok'
  } | ConvertTo-Json
} catch {
  if ($containerExists) { docker logs $name }
  throw
} finally {
  if ($containerExists) {
    if ($containerRunning) { docker rm -f $name | Out-Null }
    else { docker rm $name | Out-Null }
  }
  if ($fake -and -not $fake.HasExited) { Stop-Process -Id $fake.Id -Force }
  $resolvedTemp = [IO.Path]::GetFullPath($tempRoot)
  $resolvedBase = [IO.Path]::GetFullPath($env:TEMP)
  $safeName = (Split-Path $resolvedTemp -Leaf).StartsWith('nice-assistant-container-smoke-')
  if ($safeName -and $resolvedTemp.StartsWith($resolvedBase, [StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
  }
}
