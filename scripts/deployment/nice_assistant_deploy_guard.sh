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
PRESERVE_EXPLICIT_MAC=${NICE_DEPLOY_PRESERVE_EXPLICIT_MAC-false}

[[ "$NICE_CONTAINER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "invalid configured container name" 78
[[ "$NICE_APPROVED_IMAGE_PREFIX" =~ ^ghcr\.io/[a-z0-9_.-]+/nice-assistant$ ]] || die "invalid approved image prefix" 78
[[ "$PRESERVE_EXPLICIT_MAC" == true || "$PRESERVE_EXPLICIT_MAC" == false ]] ||
  die "invalid explicit MAC preservation policy" 78
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
BUNDLE_MANIFEST="$SCRIPT_DIR/guard_bundle_manifest.json"
for filter in "$CREATE_PAYLOAD_FILTER" "$NORMALIZE_CONFIG_FILTER"; do
  [[ -f "$filter" && $(stat -c '%u' "$filter") == 0 && $(stat -c '%a' "$filter") == 600 ]] ||
    die "deployment guard filter is missing or insecure" 78
done

read_guard_bundle_version() {
  [[ -f "$BUNDLE_MANIFEST" && ! -L "$BUNDLE_MANIFEST" &&
    $(stat -c '%u' "$BUNDLE_MANIFEST") == 0 &&
    $(stat -c '%a' "$BUNDLE_MANIFEST") == 600 &&
    $(stat -c '%h' "$BUNDLE_MANIFEST") == 1 ]] || return 1
  "$JQ" -er '
    if (
      type == "object" and
      (keys == ["bundle_version","files","launcher_protocol_version","schema_version"]) and
      (.schema_version == 1) and
      (.launcher_protocol_version == 1) and
      ((.bundle_version | type) == "number") and
      (.bundle_version >= 1) and
      (.bundle_version <= 2147483647) and
      (.bundle_version == (.bundle_version | floor)) and
      ((.files | type) == "object") and
      (.files | keys == [
        "create_container_payload.jq",
        "nice_assistant_deploy_guard.sh",
        "normalize_container_config.jq"
      ]) and
      ([.files[] |
        (type == "object") and
        (keys == ["mode","sha256"]) and
        (.sha256 | test("^[0-9a-f]{64}$"))] | all) and
      (.files["nice_assistant_deploy_guard.sh"].mode == "0700") and
      (.files["create_container_payload.jq"].mode == "0600") and
      (.files["normalize_container_config.jq"].mode == "0600")
    ) then
      .bundle_version
    else
      error("invalid deployment guard bundle manifest")
    end
  ' "$BUNDLE_MANIFEST" 2>/dev/null
}

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
  command -v dd >/dev/null || die "dd is unavailable" 69
  command -v wc >/dev/null || die "wc is unavailable" 69
}

require_runtime
if [[ ${NICE_DEPLOY_LAUNCHER_LOCKED:-} == 1 ]]; then
  [[ -e /proc/self/fd/9 && $(readlink /proc/self/fd/9) == "$LOCK_FILE" ]] ||
    die "deployment launcher lock was not inherited" 78
else
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "another Nice Assistant deployment action is active" 75
fi

container_exists() {
  "$DOCKER" container inspect "$1" >/dev/null 2>&1
}

current_repo_digest() {
  local container=$1 image_id configured_image resolved_id
  configured_image=$("$DOCKER" container inspect --format '{{.Config.Image}}' "$container") ||
    return 1
  image_id=$("$DOCKER" container inspect --format '{{.Image}}' "$container")
  if [[ "$configured_image" == "${NICE_APPROVED_IMAGE_PREFIX}@sha256:"* ]]; then
    validate_digest "$configured_image"
    resolved_id=$("$DOCKER" image inspect --format '{{.Id}}' "$configured_image") ||
      return 1
    [[ "$resolved_id" == "$image_id" ]] || return 1
    printf '%s\n' "$configured_image"
    return
  fi
  "$DOCKER" image inspect "$image_id" |
    "$JQ" -r --arg prefix "$NICE_APPROVED_IMAGE_PREFIX@" \
      '.[0].RepoDigests | map(select(startswith($prefix))) |
       if length == 1 then .[0] else error("ambiguous digest") end'
}

image_revision() {
  "$DOCKER" image inspect "$1" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
}

write_definition() {
  "$DOCKER" container inspect "$NICE_CONTAINER_NAME" >"$DEFINITION_FILE"
  chmod 600 "$DEFINITION_FILE"
}

save_previous_definition() {
  local target=$1 temporary="${1}.tmp"
  cp -- "$DEFINITION_FILE" "$temporary"
  chmod 600 "$temporary"
  mv -f -- "$temporary" "$target"
}

validate_explicit_mac_policy() {
  local definition=$1
  if [[ "$PRESERVE_EXPLICIT_MAC" == false ]]; then
    "$JQ" -e '
      .[0].Config as $config |
      ($config | type) == "object" and
      (
        ($config | has("MacAddress") | not) or
        $config.MacAddress == null or
        ($config.MacAddress | type) == "string"
      )
    ' "$definition" >/dev/null
    return
  fi
  "$JQ" -e '
    .[0] as $container |
    (($container.NetworkSettings.Networks // {}) | length) as $network_count |
    [
      ($container.NetworkSettings.Networks // {})[]?.MacAddress |
      select(type == "string" and length > 0)
    ] as $endpoint_macs |
    (($container.Config.MacAddress // "") | select(type == "string")) as $legacy_mac |
    $network_count == 1 and
    ($endpoint_macs | length) == 1 and
    ($legacy_mac == "" or $legacy_mac == $endpoint_macs[0])
  ' "$definition" >/dev/null
}

create_payload() {
  local definition=$1 image=$2 target=$3 image_labels
  validate_explicit_mac_policy "$definition" || return 1
  image_labels=$("$DOCKER" image inspect "$image" --format '{{json .Config.Labels}}') || return 1
  "$JQ" --arg image "$image" --argjson image_labels "$image_labels" \
    --argjson preserve_explicit_mac "$PRESERVE_EXPLICIT_MAC" \
    -f "$CREATE_PAYLOAD_FILTER" "$definition" >"$target"
  chmod 600 "$target"
}

normalized_config() {
  local definition=$1
  "$JQ" --arg managed_name "$NICE_CONTAINER_NAME" \
    --argjson preserve_explicit_mac "$PRESERVE_EXPLICIT_MAC" \
    -f "$NORMALIZE_CONFIG_FILTER" "$definition"
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

remove_stopped_created_container() {
  local name=$1 expected_image=$2 running configured_image
  container_exists "$name" || return 0
  running=$("$DOCKER" container inspect --format '{{.State.Running}}' "$name") ||
    return 1
  configured_image=$("$DOCKER" container inspect --format '{{.Config.Image}}' "$name") ||
    return 1
  [[ "$running" == false && "$configured_image" == "$expected_image" ]] ||
    return 1
  "$DOCKER" rm "$name" >/dev/null
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
  local image=$1 report="$NICE_DEPLOY_STATE_DIR/migration-drill.json" backup_name mounted_snapshot
  backup_name=$("$JQ" -er '.name' "$NICE_DEPLOY_STATE_DIR/backup-state.json")
  [[ "$backup_name" =~ ^nice-assistant-snapshot-[0-9]{8}_[0-9]{6}-[a-f0-9]{8}\.zip$ ]] ||
    die "verified backup state is invalid" 69
  mounted_snapshot="/$backup_name"
  "$DOCKER" run --rm \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --entrypoint python \
    -v "$NICE_DEPLOY_STATE_DIR/pre-deploy-backup.zip:${mounted_snapshot}:ro" \
    "$image" /opt/nice-assistant/scripts/backup_restore_drill.py "$mounted_snapshot" >"$report"
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
  local rollback_container=$1 previous_digest=$2 deployed_digest=$3 compatible=$4 previous_definition=$5
  local temporary="${STATE_FILE}.tmp"
  "$JQ" -n \
    --arg rollback_container "$rollback_container" \
    --arg previous_digest "$previous_digest" \
    --arg deployed_digest "$deployed_digest" \
    --arg previous_definition "$previous_definition" \
    --argjson database_compatible "$compatible" \
    --argjson preserve_explicit_mac "$PRESERVE_EXPLICIT_MAC" \
    '{state_version:3,rollback_container:$rollback_container,previous_digest:$previous_digest,deployed_digest:$deployed_digest,database_compatible:$database_compatible,previous_definition:$previous_definition,preserve_explicit_mac:$preserve_explicit_mac}' \
    >"$temporary"
  chmod 600 "$temporary"
  mv -f -- "$temporary" "$STATE_FILE"
}

previous_definition_path() {
  local name=$1 path
  [[ "$name" =~ ^previous-container-definition\.[0-9]{14}\.json$ ]] || return 1
  path="$NICE_DEPLOY_STATE_DIR/$name"
  [[ -f "$path" ]] || return 1
  [[ $(stat -c '%u' "$path") == 0 && $(stat -c '%a' "$path") == 600 ]] || return 1
  printf '%s\n' "$path"
}

remove_previous_definition() {
  local name=$1
  [[ "$name" =~ ^previous-container-definition\.[0-9]{14}\.json$ ]] || return 0
  rm -f -- "$NICE_DEPLOY_STATE_DIR/$name"
}

cleanup_previous_definitions_except() {
  local keep=$1 path name
  for path in "$NICE_DEPLOY_STATE_DIR"/previous-container-definition.*.json; do
    [[ -f "$path" ]] || continue
    name=${path##*/}
    [[ "$name" =~ ^previous-container-definition\.[0-9]{14}\.json$ ]] || continue
    [[ "$name" == "$keep" ]] && continue
    rm -f -- "$path" || return 1
  done
}

is_guard_rollback_name() {
  local name=$1 suffix
  case "$name" in
    "$NICE_CONTAINER_NAME".rollback.*) ;;
    *) return 1 ;;
  esac
  suffix=${name#"${NICE_CONTAINER_NAME}.rollback."}
  [[ "$suffix" =~ ^[0-9]{14}$ ]]
}

remove_guard_rollback_container() {
  local name=$1 running
  is_guard_rollback_name "$name" || return 0
  container_exists "$name" || return 0
  running=$("$DOCKER" container inspect --format '{{.State.Running}}' "$name") ||
    return 1
  if [[ "$running" == true ]]; then
    "$DOCKER" stop --time 30 "$name" >/dev/null || return 1
  fi
  "$DOCKER" rm "$name" >/dev/null || return 1
}

cleanup_guard_rollback_containers() {
  local id name containers
  containers=$("$DOCKER" container ls -aq --filter "name=${NICE_CONTAINER_NAME}.rollback.") ||
    return 1
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    name=$("$DOCKER" container inspect --format '{{.Name}}' "$id") || return 1
    name=${name#/}
    remove_guard_rollback_container "$name" || return 1
  done <<<"$containers"
}

perform_rollback() {
  [[ -f "$STATE_FILE" ]] || die "no guarded rollback is available" 69
  local rollback_container previous_digest compatible failed_name previous_definition previous_definition_file
  local state_policy
  local rollback_payload rollback_response rollback_inspect before after recreated
  state_policy=$("$JQ" -er '
    if .state_version != 3 then
      error("unsupported deployment state")
    elif (.preserve_explicit_mac | type) != "boolean" then
      error("invalid deployment MAC policy")
    else
      (.preserve_explicit_mac | tostring)
    end
  ' "$STATE_FILE") ||
    die "the guarded rollback MAC policy is unavailable; operator recovery is required" 76
  [[ "$state_policy" == "$PRESERVE_EXPLICIT_MAC" ]] ||
    die "the deployment MAC policy changed; operator approval is required before rollback" 76
  rollback_container=$("$JQ" -r '.rollback_container // empty' "$STATE_FILE")
  previous_digest=$("$JQ" -er '.previous_digest' "$STATE_FILE")
  compatible=$("$JQ" -er '.database_compatible' "$STATE_FILE")
  previous_definition=$("$JQ" -r '.previous_definition // empty' "$STATE_FILE")
  [[ "$compatible" == true ]] || die "database restore approval is required before rollback" 76
  validate_digest "$previous_digest"
  [[ -z "$rollback_container" ]] || is_guard_rollback_name "$rollback_container" ||
    die "the guarded rollback container name is invalid" 69
  recreated=false
  previous_definition_file=
  if [[ -z "$rollback_container" ]] || ! container_exists "$rollback_container"; then
    previous_definition_file=$(previous_definition_path "$previous_definition") ||
      die "the guarded rollback definition is unavailable" 69
    "$DOCKER" image inspect "$previous_digest" >/dev/null 2>&1 ||
      "$DOCKER" pull "$previous_digest" >/dev/null ||
      die "the prior immutable image is unavailable" 69
    rollback_payload="$NICE_DEPLOY_STATE_DIR/rollback-payload.json"
    rollback_response="$NICE_DEPLOY_STATE_DIR/rollback-response.json"
    create_payload "$previous_definition_file" "$previous_digest" "$rollback_payload" ||
      die "the guarded rollback definition could not be prepared" 70
    recreated=true
  fi
  failed_name="${NICE_CONTAINER_NAME}.failed.$(date -u +%Y%m%d%H%M%S)"
  if container_exists "$NICE_CONTAINER_NAME"; then
    "$DOCKER" stop --time 30 "$NICE_CONTAINER_NAME" >/dev/null || true
    "$DOCKER" rename "$NICE_CONTAINER_NAME" "$failed_name"
  fi
  if [[ "$recreated" == true ]]; then
    if ! create_container_from_payload "$NICE_CONTAINER_NAME" "$rollback_payload" "$rollback_response"; then
      remove_stopped_created_container "$NICE_CONTAINER_NAME" "$previous_digest" ||
        die "container rollback creation was ambiguous; operator recovery is required" 70
      if container_exists "$failed_name"; then
        "$DOCKER" rename "$failed_name" "$NICE_CONTAINER_NAME"
        "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null || true
      fi
      die "container rollback could not recreate the prior container; operator recovery is required" 70
    fi
  else
    "$DOCKER" rename "$rollback_container" "$NICE_CONTAINER_NAME"
  fi
  rollback_inspect="$NICE_DEPLOY_STATE_DIR/rollback-inspect.json"
  if ! "$DOCKER" container inspect "$NICE_CONTAINER_NAME" >"$rollback_inspect" ||
    ! validate_explicit_mac_policy "$rollback_inspect" ||
    { [[ "$recreated" == true ]] &&
      { ! before=$(normalized_config "$previous_definition_file") ||
        ! after=$(normalized_config "$rollback_inspect") ||
        [[ "$before" != "$after" ]]; }; } ||
    ! "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null ||
    ! wait_healthy "$NICE_CONTAINER_NAME" ||
    [[ $(current_repo_digest "$NICE_CONTAINER_NAME") != "$previous_digest" ]]; then
    "$DOCKER" stop --time 30 "$NICE_CONTAINER_NAME" >/dev/null || true
    if [[ "$recreated" == true ]]; then
      "$DOCKER" rm "$NICE_CONTAINER_NAME" >/dev/null || true
    else
      "$DOCKER" rename "$NICE_CONTAINER_NAME" "$rollback_container"
    fi
    if container_exists "$failed_name"; then
      "$DOCKER" rename "$failed_name" "$NICE_CONTAINER_NAME"
      "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null || true
    fi
    die "container rollback failed; operator recovery is required" 70
  fi
  container_exists "$failed_name" && "$DOCKER" rm "$failed_name" >/dev/null
  update_template_image "$previous_digest" ||
    die "container rollback succeeded but the Unraid template could not be updated" 70
  cleanup_guard_rollback_containers ||
    die "container rollback succeeded but an obsolete rollback container could not be removed" 70
  rm -f "$STATE_FILE"
  remove_previous_definition "$previous_definition"
  cleanup_previous_definitions_except "" ||
    die "container rollback succeeded but obsolete rollback state could not be removed" 70
}

inspect_action() {
  container_exists "$NICE_CONTAINER_NAME" || die "Nice Assistant container is unavailable" 69
  local digest revision guard_bundle_version
  digest=$(current_repo_digest "$NICE_CONTAINER_NAME")
  validate_digest "$digest"
  revision=$(image_revision "$digest")
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || die "container image has no valid source revision label" 69
  guard_bundle_version=$(read_guard_bundle_version) ||
    die "active deployment guard bundle manifest is invalid" 78
  write_definition
  "$JQ" -n \
    --arg digest "$digest" \
    --arg revision "$revision" \
    --argjson guard_bundle_version "$guard_bundle_version" \
    --argjson preserve_explicit_mac "$PRESERVE_EXPLICIT_MAC" \
    '{ok:true,action:"inspect",digest:$digest,revision:$revision,guard_bundle_version:$guard_bundle_version,preserve_explicit_mac:$preserve_explicit_mac}'
}

validate_definition_action() {
  inspect_action >/dev/null
  local digest payload response probe before after
  digest=$(current_repo_digest "$NICE_CONTAINER_NAME")
  payload="$NICE_DEPLOY_STATE_DIR/probe-payload.json"
  response="$NICE_DEPLOY_STATE_DIR/probe-response.json"
  probe="${NICE_CONTAINER_NAME}.definition-probe.$(date -u +%Y%m%d%H%M%S)"
  create_payload "$DEFINITION_FILE" "$digest" "$payload"
  if ! create_container_from_payload "$probe" "$payload" "$response"; then
    remove_stopped_created_container "$probe" "$digest" ||
      die "container definition creation was ambiguous" 70
    die "container definition could not be recreated" 70
  fi
  "$DOCKER" container inspect "$probe" >"$NICE_DEPLOY_STATE_DIR/probe-inspect.json"
  if ! validate_explicit_mac_policy "$NICE_DEPLOY_STATE_DIR/probe-inspect.json"; then
    "$DOCKER" rm "$probe" >/dev/null
    die "recreated container violated the configured MAC policy" 70
  fi
  before=$(normalized_config "$DEFINITION_FILE")
  after=$(normalized_config "$NICE_DEPLOY_STATE_DIR/probe-inspect.json")
  "$DOCKER" rm "$probe" >/dev/null
  [[ "$before" == "$after" ]] || die "recreated container definition did not match" 70
  printf '{"ok":true,"action":"validate-definition"}\n'
}

deploy_action() {
  local digest=$1 revision previous_digest old_revision new_revision compatible rollback_name payload response candidate_inspect before after
  local deployment_stamp previous_definition_name previous_definition_file
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
  deployment_stamp=$(date -u +%Y%m%d%H%M%S)
  previous_definition_name="previous-container-definition.${deployment_stamp}.json"
  previous_definition_file="$NICE_DEPLOY_STATE_DIR/$previous_definition_name"
  save_previous_definition "$previous_definition_file"
  payload="$NICE_DEPLOY_STATE_DIR/create-payload.json"
  response="$NICE_DEPLOY_STATE_DIR/create-response.json"
  create_payload "$DEFINITION_FILE" "$digest" "$payload"

  rollback_name="${NICE_CONTAINER_NAME}.rollback.${deployment_stamp}"
  "$DOCKER" stop --time 30 "$NICE_CONTAINER_NAME" >/dev/null
  "$DOCKER" rename "$NICE_CONTAINER_NAME" "$rollback_name"
  if ! create_container_from_payload "$NICE_CONTAINER_NAME" "$payload" "$response"; then
    remove_stopped_created_container "$NICE_CONTAINER_NAME" "$digest" ||
      die "candidate creation was ambiguous; operator recovery is required" 70
    "$DOCKER" rename "$rollback_name" "$NICE_CONTAINER_NAME"
    "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null ||
      die "candidate creation failed and the prior container could not restart" 70
    wait_healthy "$NICE_CONTAINER_NAME" ||
      die "candidate creation failed and the prior container did not recover" 70
    remove_previous_definition "$previous_definition_name"
    die "candidate container could not be created; prior container restored" 70
  fi

  candidate_inspect="$NICE_DEPLOY_STATE_DIR/candidate-inspect.json"
  if ! "$DOCKER" container inspect "$NICE_CONTAINER_NAME" >"$candidate_inspect" ||
    ! validate_explicit_mac_policy "$candidate_inspect" ||
    ! before=$(normalized_config "$DEFINITION_FILE") ||
    ! after=$(normalized_config "$candidate_inspect") ||
    [[ "$before" != "$after" ]]; then
    remove_stopped_created_container "$NICE_CONTAINER_NAME" "$digest" ||
      die "candidate definition failure was ambiguous; operator recovery is required" 70
    "$DOCKER" rename "$rollback_name" "$NICE_CONTAINER_NAME"
    "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null ||
      die "candidate definition failed and the prior container could not restart" 70
    wait_healthy "$NICE_CONTAINER_NAME" ||
      die "candidate definition failed and the prior container did not recover" 70
    remove_previous_definition "$previous_definition_name"
    die "candidate definition failed acceptance; prior container restored" 70
  fi

  write_state "$rollback_name" "$previous_digest" "$digest" "$compatible" "$previous_definition_name"
  if ! "$DOCKER" start "$NICE_CONTAINER_NAME" >/dev/null ||
    ! "$DOCKER" container inspect "$NICE_CONTAINER_NAME" >"$candidate_inspect" ||
    ! validate_explicit_mac_policy "$candidate_inspect" ||
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

  cleanup_guard_rollback_containers ||
    die "candidate passed acceptance but an obsolete rollback container could not be removed" 70
  write_state "" "$previous_digest" "$digest" "$compatible" "$previous_definition_name"
  cleanup_previous_definitions_except "$previous_definition_name" ||
    die "candidate passed acceptance but obsolete rollback state could not be removed" 70
  "$JQ" -n --arg digest "$digest" --arg revision "$revision" --argjson database_compatible "$compatible" \
    '{ok:true,action:"deploy",digest:$digest,revision:$revision,database_compatible:$database_compatible}'
}

logs_action() {
  local target="$NICE_DEPLOY_STATE_DIR/log-summary-source.tmp"
  local docker_status bytes lines errors warnings truncated=false
  set +o pipefail
  "$DOCKER" logs --tail 200 "$NICE_CONTAINER_NAME" 2>&1 |
    dd bs=1024 count=64 status=none >"$target"
  docker_status=${PIPESTATUS[0]}
  set -o pipefail
  [[ "$docker_status" == 0 || "$docker_status" == 141 ]] ||
    die "Nice Assistant logs are unavailable" 69
  chmod 600 "$target"
  bytes=$(wc -c <"$target")
  lines=$(wc -l <"$target")
  if ((bytes > 0 && lines == 0)); then
    lines=1
  fi
  errors=$(grep -Eic 'fatal|traceback|error|exception|unhealthy|failed' "$target" || true)
  warnings=$(grep -Eic 'warning|warn|degraded|timeout|unavailable' "$target" || true)
  ((bytes >= 65536)) && truncated=true
  rm -f -- "$target"
  "$JQ" -n \
    --argjson lines "$lines" \
    --argjson errors "$errors" \
    --argjson warnings "$warnings" \
    --argjson truncated "$truncated" \
    '{ok:true,action:"logs",sample_lines:$lines,error_lines:$errors,warning_lines:$warnings,truncated:$truncated}'
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
  logs) logs_action ;;
  deploy) deploy_action "${COMMAND_PARTS[1]}" ;;
  rollback)
    perform_rollback
    printf '{"ok":true,"action":"rollback"}\n'
    ;;
esac
