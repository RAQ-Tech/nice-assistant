#!/usr/bin/env bash
# Root-only forced-command guard for a single Nice Assistant container.

set -Eeuo pipefail
set -f
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -n ${SSH_ORIGINAL_COMMAND:-} ]]; then
  CONFIG_FILE="$SCRIPT_DIR/guard.conf"
else
  CONFIG_FILE=${NICE_DEPLOY_GUARD_CONFIG:-"$SCRIPT_DIR/guard.conf"}
fi

die() {
  printf '{"ok":false,"error":"%s"}\n' "$1" >&2
  exit "${2:-1}"
}

[[ -f "$CONFIG_FILE" ]] || die "deployment guard is not configured" 78
[[ $(stat -c '%u' "$CONFIG_FILE") == 0 ]] || die "deployment guard configuration must be root-owned" 78
[[ $(stat -c '%a' "$CONFIG_FILE") == 600 ]] || die "deployment guard configuration must use mode 0600" 78
# shellcheck disable=SC1090
source "$CONFIG_FILE"

: "${NICE_CONTAINER_NAME:?missing NICE_CONTAINER_NAME}"
: "${NICE_APPROVED_IMAGE_PREFIX:?missing NICE_APPROVED_IMAGE_PREFIX}"
: "${NICE_DEPLOY_STATE_DIR:?missing NICE_DEPLOY_STATE_DIR}"

[[ "$NICE_CONTAINER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "invalid configured container name" 78
[[ "$NICE_APPROVED_IMAGE_PREFIX" =~ ^ghcr\.io/[a-z0-9_.-]+/nice-assistant$ ]] || die "invalid approved image prefix" 78
mkdir -p -- "$NICE_DEPLOY_STATE_DIR"
chmod 700 -- "$NICE_DEPLOY_STATE_DIR"

DOCKER=${NICE_DEPLOY_DOCKER_BIN:-docker}
CURL=${NICE_DEPLOY_CURL_BIN:-curl}
JQ=${NICE_DEPLOY_JQ_BIN:-jq}
STATE_FILE="$NICE_DEPLOY_STATE_DIR/deployment-state.json"
DEFINITION_FILE="$NICE_DEPLOY_STATE_DIR/container-definition.json"
LOCK_FILE="$NICE_DEPLOY_STATE_DIR/deploy.lock"
UNRAID_TEMPLATE=${NICE_UNRAID_TEMPLATE:-}
CREATE_PAYLOAD_FILTER="$SCRIPT_DIR/create_container_payload.jq"
NORMALIZE_CONFIG_FILTER="$SCRIPT_DIR/normalize_container_config.jq"
for filter in "$CREATE_PAYLOAD_FILTER" "$NORMALIZE_CONFIG_FILTER"; do
  [[ -f "$filter" && $(stat -c '%u' "$filter") == 0 && $(stat -c '%a' "$filter") == 600 ]] ||
    die "deployment guard filter is missing or insecure" 78
done

if [[ -n ${SSH_ORIGINAL_COMMAND:-} ]]; then
  read -r -a COMMAND_PARTS <<<"$SSH_ORIGINAL_COMMAND"
else
  COMMAND_PARTS=("$@")
fi
[[ ${#COMMAND_PARTS[@]} -gt 0 ]] || die "an allowed deployment action is required" 64
ACTION=${COMMAND_PARTS[0]}

case "$ACTION" in
  inspect | backup | health | logs | rollback)
    [[ ${#COMMAND_PARTS[@]} -eq 1 ]] || die "invalid deployment command" 64
    ;;
  deploy)
    [[ ${#COMMAND_PARTS[@]} -eq 2 ]] || die "deploy requires one immutable image digest" 64
    ;;
  validate-definition)
    [[ -z ${SSH_ORIGINAL_COMMAND:-} && ${#COMMAND_PARTS[@]} -eq 1 ]] || die "invalid deployment command" 64
    ;;
  *) die "deployment command is not allowed" 64 ;;
esac

validate_digest() {
  local digest=$1 suffix
  [[ "$digest" == "${NICE_APPROVED_IMAGE_PREFIX}@sha256:"* ]] ||
    die "only an immutable digest from the approved repository is allowed" 64
  suffix=${digest#"${NICE_APPROVED_IMAGE_PREFIX}@sha256:"}
  [[ "$suffix" =~ ^[0-9a-f]{64}$ ]] ||
    die "only an immutable digest from the approved repository is allowed" 64
}

require_runtime() {
  command -v "$DOCKER" >/dev/null || die "docker is unavailable" 69
  command -v "$CURL" >/dev/null || die "curl is unavailable" 69
  command -v "$JQ" >/dev/null || die "jq is unavailable" 69
  command -v flock >/dev/null || die "flock is unavailable" 69
}

require_runtime
exec 9>"$LOCK_FILE"
flock -n 9 || die "another Nice Assistant deployment action is active" 75

container_exists() {
  "$DOCKER" container inspect "$1" >/dev/null 2>&1
}

current_repo_digest() {
  local container=$1 image_id
  image_id=$("$DOCKER" container inspect --format '{{.Image}}' "$container")
  "$DOCKER" image inspect "$image_id" |
    "$JQ" -r --arg prefix "$NICE_APPROVED_IMAGE_PREFIX@" \
      '.[0].RepoDigests[]? | select(startswith($prefix))' | head -n 1
}

image_revision() {
  "$DOCKER" image inspect "$1" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
}

write_definition() {
  "$DOCKER" container inspect "$NICE_CONTAINER_NAME" >"$DEFINITION_FILE"
  chmod 600 "$DEFINITION_FILE"
}

create_payload() {
  local definition=$1 image=$2 target=$3 image_labels
  image_labels=$("$DOCKER" image inspect "$image" --format '{{json .Config.Labels}}') || return 1
  "$JQ" --arg image "$image" --argjson image_labels "$image_labels" \
    -f "$CREATE_PAYLOAD_FILTER" "$definition" >"$target"
  chmod 600 "$target"
}

normalized_config() {
  local definition=$1
  "$JQ" -f "$NORMALIZE_CONFIG_FILTER" "$definition"
}

create_container_from_payload() {
  local name=$1 payload=$2 response=$3 api_version
  api_version=$("$DOCKER" version --format '{{.Server.APIVersion}}')
  if ! "$CURL" --silent --show-error --fail-with-body \
    --unix-socket /var/run/docker.sock \
    -H 'Content-Type: application/json' \
    -X POST --data-binary "@$payload" \
    "http://localhost/v${api_version}/containers/create?name=${name}" >"$response"; then
    return 1
  fi
  "$JQ" -e '.Id | type == "string" and length > 0' "$response" >/dev/null
}

wait_healthy() {
  local container=$1 deadline status
  deadline=$((SECONDS + ${NICE_DEPLOY_HEALTH_TIMEOUT_SECONDS:-150}))
  while ((SECONDS < deadline)); do
    status=$("$DOCKER" container inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
    if [[ "$status" == healthy || "$status" == running ]]; then
      if "$DOCKER" exec "$container" python -c \
        'import json,os,urllib.request; p=os.environ.get("PORT","3000"); assert urllib.request.urlopen(f"http://127.0.0.1:{p}/health",timeout=4).status==200; r=json.load(urllib.request.urlopen(f"http://127.0.0.1:{p}/ready",timeout=4)); assert r.get("ready") is True' \
        >/dev/null 2>&1; then
        return 0
      fi
    fi
    [[ "$status" == unhealthy || "$status" == exited || "$status" == dead ]] && return 1
    sleep 2
  done
  return 1
}

check_startup_logs() {
  local container=$1 target="$NICE_DEPLOY_STATE_DIR/startup.log"
  "$DOCKER" logs --tail 300 "$container" >"$target" 2>&1 || return 1
  chmod 600 "$target"
  ! grep -Eiq 'Fatal Python error|Traceback \(most recent call last\)|migration[^[:cntrl:]]*failed' "$target"
}

database_revision() {
  "$DOCKER" exec "$1" python -c \
    'import sqlite3; from app.runtime import AppConfig; c=AppConfig.from_env(); db=sqlite3.connect(c.database_path); print(db.execute("SELECT version_num FROM alembic_version").fetchone()[0])'
}

create_verified_backup() {
  local container=$1 output name archive_path copied
  output=$("$DOCKER" exec "$container" python -c '
import json
from app.operations_service import OperationsService
from app.runtime import AppConfig
class Logger:
    def info(self,*args,**kwargs): pass
    def warning(self,*args,**kwargs): pass
config=AppConfig.from_env()
item=OperationsService(config,Logger()).create_backup(False)
verified=OperationsService(config,Logger()).verify_backup(item["name"])
assert verified["ok"] and verified["database_integrity"] == "ok"
print(json.dumps({"name": item["name"], "path": str(config.backup_dir / item["name"])}))
')
  name=$("$JQ" -er '.name' <<<"$output")
  archive_path=$("$JQ" -er '.path' <<<"$output")
  copied="$NICE_DEPLOY_STATE_DIR/pre-deploy-backup.zip"
  "$DOCKER" cp "$container:$archive_path" "$copied" >/dev/null
  chmod 600 "$copied"
  "$JQ" -n --arg name "$name" '{verified:true, name:$name}' >"$NICE_DEPLOY_STATE_DIR/backup-state.json"
  chmod 600 "$NICE_DEPLOY_STATE_DIR/backup-state.json"
}

candidate_migration_revision() {
  local image=$1 report="$NICE_DEPLOY_STATE_DIR/migration-drill.json"
  "$DOCKER" run --rm --entrypoint python \
    -v "$NICE_DEPLOY_STATE_DIR/pre-deploy-backup.zip:/candidate.zip:ro" \
    "$image" /opt/nice-assistant/scripts/backup_restore_drill.py /candidate.zip >"$report"
  chmod 600 "$report"
  "$JQ" -er '.migration_revision' "$report"
}

update_template_image() {
  local image=$1 temporary
  [[ -n "$UNRAID_TEMPLATE" ]] || return 0
  validate_template || return 1
  temporary="$NICE_DEPLOY_STATE_DIR/template.tmp"
  sed -E "s|<Repository>[^<]*</Repository>|<Repository>${image}</Repository>|" "$UNRAID_TEMPLATE" >"$temporary" || return 1
  [[ $(grep -c '<Repository>[^<]*</Repository>' "$temporary") -eq 1 ]] || {
    rm -f "$temporary"
    return 1
  }
  cat "$temporary" >"$UNRAID_TEMPLATE" || return 1
  rm -f "$temporary"
}

validate_template() {
  [[ -n "$UNRAID_TEMPLATE" ]] || return 0
  [[ -f "$UNRAID_TEMPLATE" ]] || return 1
  [[ $(grep -c '<Repository>[^<]*</Repository>' "$UNRAID_TEMPLATE") -eq 1 ]]
}

write_state() {
  local rollback_container=$1 previous_digest=$2 deployed_digest=$3 compatible=$4
  "$JQ" -n \
    --arg rollback_container "$rollback_container" \
    --arg previous_digest "$previous_digest" \
    --arg deployed_digest "$deployed_digest" \
    --argjson database_compatible "$compatible" \
    '{rollback_container:$rollback_container,previous_digest:$previous_digest,deployed_digest:$deployed_digest,database_compatible:$database_compatible}' \
    >"$STATE_FILE"
  chmod 600 "$STATE_FILE"
}

perform_rollback() {
  [[ -f "$STATE_FILE" ]] || die "no guarded rollback is available" 69
  local rollback_container previous_digest compatible failed_name
  rollback_container=$("$JQ" -er '.rollback_container' "$STATE_FILE")
  previous_digest=$("$JQ" -er '.previous_digest' "$STATE_FILE")
  compatible=$("$JQ" -er '.database_compatible' "$STATE_FILE")
  [[ "$compatible" == true ]] || die "database restore approval is required before rollback" 76
  validate_digest "$previous_digest"
  container_exists "$rollback_container" || die "the guarded rollback container is unavailable" 69
  failed_name="${NICE_CONTAINER_NAME}.failed.$(date -u +%Y%m%d%H%M%S)"
  if container_exists "$NICE_CONTAINER_NAME"; then
    "$DOCKER" stop --time 30 "$NICE_CONTAINER_NAME" >/dev/null || true
    "$DOCKER" rename "$NICE_CONTAINER_NAME" "$failed_name"
  fi
  "$DOCKER" rename "$rollback_container" "$NICE_CONTAINER_NAME"
  "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null
  if ! wait_healthy "$NICE_CONTAINER_NAME"; then
    "$DOCKER" stop --time 30 "$NICE_CONTAINER_NAME" >/dev/null || true
    "$DOCKER" rename "$NICE_CONTAINER_NAME" "$rollback_container"
    if container_exists "$failed_name"; then
      "$DOCKER" rename "$failed_name" "$NICE_CONTAINER_NAME"
      "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null || true
    fi
    die "container rollback failed; operator recovery is required" 70
  fi
  container_exists "$failed_name" && "$DOCKER" rm "$failed_name" >/dev/null
  update_template_image "$previous_digest" ||
    die "container rollback succeeded but the Unraid template could not be updated" 70
  rm -f "$STATE_FILE"
}

inspect_action() {
  container_exists "$NICE_CONTAINER_NAME" || die "Nice Assistant container is unavailable" 69
  local digest revision
  digest=$(current_repo_digest "$NICE_CONTAINER_NAME")
  validate_digest "$digest"
  revision=$(image_revision "$digest")
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || die "container image has no valid source revision label" 69
  write_definition
  "$JQ" -n --arg digest "$digest" --arg revision "$revision" \
    '{ok:true,action:"inspect",digest:$digest,revision:$revision}'
}

validate_definition_action() {
  inspect_action >/dev/null
  local digest payload response probe before after
  digest=$(current_repo_digest "$NICE_CONTAINER_NAME")
  payload="$NICE_DEPLOY_STATE_DIR/probe-payload.json"
  response="$NICE_DEPLOY_STATE_DIR/probe-response.json"
  probe="${NICE_CONTAINER_NAME}.definition-probe.$(date -u +%Y%m%d%H%M%S)"
  create_payload "$DEFINITION_FILE" "$digest" "$payload"
  create_container_from_payload "$probe" "$payload" "$response" || die "container definition could not be recreated" 70
  "$DOCKER" container inspect "$probe" >"$NICE_DEPLOY_STATE_DIR/probe-inspect.json"
  before=$(normalized_config "$DEFINITION_FILE")
  after=$(normalized_config "$NICE_DEPLOY_STATE_DIR/probe-inspect.json")
  "$DOCKER" rm "$probe" >/dev/null
  [[ "$before" == "$after" ]] || die "recreated container definition did not match" 70
  printf '{"ok":true,"action":"validate-definition"}\n'
}

deploy_action() {
  local digest=$1 revision previous_digest old_revision new_revision compatible rollback_name payload response candidate_inspect before after
  validate_digest "$digest"
  container_exists "$NICE_CONTAINER_NAME" || die "Nice Assistant container is unavailable" 69
  [[ $("$DOCKER" container inspect --format '{{.State.Running}}' "$NICE_CONTAINER_NAME") == true ]] ||
    die "Nice Assistant must be running before deployment" 69
  "$DOCKER" pull "$digest" >/dev/null
  "$DOCKER" image inspect "$digest" >/dev/null
  revision=$(image_revision "$digest")
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || die "candidate image has no valid source revision label" 69
  validate_template || die "configured Unraid template is unavailable or ambiguous" 78
  previous_digest=$(current_repo_digest "$NICE_CONTAINER_NAME")
  validate_digest "$previous_digest"
  old_revision=$(database_revision "$NICE_CONTAINER_NAME")
  create_verified_backup "$NICE_CONTAINER_NAME"
  new_revision=$(candidate_migration_revision "$digest")
  compatible=false
  [[ "$old_revision" == "$new_revision" ]] && compatible=true
  write_definition
  payload="$NICE_DEPLOY_STATE_DIR/create-payload.json"
  response="$NICE_DEPLOY_STATE_DIR/create-response.json"
  create_payload "$DEFINITION_FILE" "$digest" "$payload"

  if [[ -f "$STATE_FILE" ]]; then
    local obsolete
    obsolete=$("$JQ" -r '.rollback_container // empty' "$STATE_FILE")
    [[ -n "$obsolete" ]] && container_exists "$obsolete" && "$DOCKER" rm "$obsolete" >/dev/null
  fi

  rollback_name="${NICE_CONTAINER_NAME}.rollback.$(date -u +%Y%m%d%H%M%S)"
  "$DOCKER" stop --time 30 "$NICE_CONTAINER_NAME" >/dev/null
  "$DOCKER" rename "$NICE_CONTAINER_NAME" "$rollback_name"
  if ! create_container_from_payload "$NICE_CONTAINER_NAME" "$payload" "$response"; then
    "$DOCKER" rename "$rollback_name" "$NICE_CONTAINER_NAME"
    "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null ||
      die "candidate creation failed and the prior container could not restart" 70
    wait_healthy "$NICE_CONTAINER_NAME" ||
      die "candidate creation failed and the prior container did not recover" 70
    die "candidate container could not be created; prior container restored" 70
  fi
  write_state "$rollback_name" "$previous_digest" "$digest" "$compatible"

  candidate_inspect="$NICE_DEPLOY_STATE_DIR/candidate-inspect.json"
  if ! "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null ||
    ! "$DOCKER" container inspect "$NICE_CONTAINER_NAME" >"$candidate_inspect" ||
    ! before=$(normalized_config "$DEFINITION_FILE") ||
    ! after=$(normalized_config "$candidate_inspect") ||
    [[ "$before" != "$after" ]] ||
    ! wait_healthy "$NICE_CONTAINER_NAME" ||
    ! check_startup_logs "$NICE_CONTAINER_NAME" ||
    [[ $(current_repo_digest "$NICE_CONTAINER_NAME") != "$digest" ]] ||
    [[ $(image_revision "$digest") != "$revision" ]] ||
    ! update_template_image "$digest"; then
    if [[ "$compatible" == true ]]; then
      perform_rollback
      die "candidate failed acceptance and the prior container was restored" 70
    fi
    die "candidate failed after a schema change; database restore requires operator approval" 76
  fi

  "$JQ" -n --arg digest "$digest" --arg revision "$revision" --argjson database_compatible "$compatible" \
    '{ok:true,action:"deploy",digest:$digest,revision:$revision,database_compatible:$database_compatible}'
}

case "$ACTION" in
  inspect) inspect_action ;;
  validate-definition) validate_definition_action ;;
  backup)
    create_verified_backup "$NICE_CONTAINER_NAME"
    printf '{"ok":true,"action":"backup"}\n'
    ;;
  health)
    wait_healthy "$NICE_CONTAINER_NAME" || die "Nice Assistant health acceptance failed" 70
    inspect_action | "$JQ" '.action = "health"'
    ;;
  logs)
    "$DOCKER" logs --tail 200 "$NICE_CONTAINER_NAME" 2>&1 |
      sed -E \
        -e 's/(authorization[=": ]+Bearer[[:space:]]+)[^ ,"}]+/\1[REDACTED]/Ig' \
        -e 's/(([A-Za-z0-9_-]*(_key|token|password|secret)|authorization)[=": ]+)[^ ,"}]+/\1[REDACTED]/Ig'
    ;;
  deploy) deploy_action "${COMMAND_PARTS[1]}" ;;
  rollback)
    perform_rollback
    printf '{"ok":true,"action":"rollback"}\n'
    ;;
esac
