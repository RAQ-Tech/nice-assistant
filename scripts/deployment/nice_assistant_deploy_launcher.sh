#!/bin/bash
# Permanent root-owned forced-command launcher for versioned deployment guards.

set -Eeuo pipefail
set -f
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
unset BASH_ENV ENV CDPATH GLOBIGNORE

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE="$SCRIPT_DIR/guard.conf"
if [[ -z ${SSH_ORIGINAL_COMMAND+x} && -n ${NICE_DEPLOY_LAUNCHER_CONFIG:-} ]]; then
  CONFIG_FILE=$NICE_DEPLOY_LAUNCHER_CONFIG
fi

die() {
  printf '{"ok":false,"error":"%s"}\n' "$1" >&2
  exit "${2:-1}"
}

secure_regular_file() {
  local path=$1 mode=$2
  [[ -f "$path" && ! -L "$path" ]] || return 1
  [[ $(stat -c '%F' "$path") == "regular file" ]] || return 1
  [[ $(stat -c '%u' "$path") == 0 ]] || return 1
  [[ $(stat -c '%a' "$path") == "$mode" ]] || return 1
}

[[ $EUID -eq 0 ]] || die "deployment launcher requires root" 77
secure_regular_file "$CONFIG_FILE" 600 ||
  die "deployment launcher configuration is missing or insecure" 78
# shellcheck disable=SC1090
source "$CONFIG_FILE"

: "${NICE_CONTAINER_NAME:?missing NICE_CONTAINER_NAME}"
: "${NICE_APPROVED_IMAGE_PREFIX:?missing NICE_APPROVED_IMAGE_PREFIX}"
: "${NICE_DEPLOY_STATE_DIR:?missing NICE_DEPLOY_STATE_DIR}"

[[ "$NICE_CONTAINER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] ||
  die "invalid configured container name" 78
[[ "$NICE_APPROVED_IMAGE_PREFIX" =~ ^ghcr\.io/[a-z0-9_.-]+/nice-assistant$ ]] ||
  die "invalid approved image prefix" 78
[[ "$NICE_DEPLOY_STATE_DIR" =~ ^/[A-Za-z0-9_./-]+$ &&
  "$NICE_DEPLOY_STATE_DIR" != / &&
  "$NICE_DEPLOY_STATE_DIR" != */ &&
  "$NICE_DEPLOY_STATE_DIR" != *"//"* &&
  "$NICE_DEPLOY_STATE_DIR" != *"/./"* &&
  "$NICE_DEPLOY_STATE_DIR" != *"/." &&
  "$NICE_DEPLOY_STATE_DIR" != *"/../"* &&
  "$NICE_DEPLOY_STATE_DIR" != *"/.." ]] ||
  die "invalid deployment state directory" 78
[[ $(readlink -m -- "$NICE_DEPLOY_STATE_DIR") == "$NICE_DEPLOY_STATE_DIR" ]] ||
  die "deployment state directory must be canonical" 78

DOCKER=${NICE_DEPLOY_DOCKER_BIN:-docker}
CURL=${NICE_DEPLOY_CURL_BIN:-curl}
JQ=${NICE_DEPLOY_JQ_BIN:-jq}
BUNDLE_ROOT="$SCRIPT_DIR/guard-bundles"
RELEASE_ROOT="$BUNDLE_ROOT/releases"
CURRENT_LINK="$BUNDLE_ROOT/current"
PREVIOUS_LINK="$BUNDLE_ROOT/previous"
LOCK_FILE="$NICE_DEPLOY_STATE_DIR/deploy.lock"
UPDATE_JOURNAL="$NICE_DEPLOY_STATE_DIR/guard-update.json"
UPDATE_JOURNAL_TMP="${UPDATE_JOURNAL}.tmp"
EXPECTED_SOURCE="https://github.com/${NICE_APPROVED_IMAGE_PREFIX#ghcr.io/}"
MAX_GUARD_BYTES=524288
MAX_FILTER_BYTES=131072
MAX_MANIFEST_BYTES=32768

for command in "$DOCKER" "$CURL" "$JQ" flock stat sha256sum install readlink; do
  command -v "$command" >/dev/null 2>&1 ||
    die "deployment launcher runtime is unavailable" 69
done

mkdir -p -- "$NICE_DEPLOY_STATE_DIR" "$BUNDLE_ROOT" "$RELEASE_ROOT"
chmod 700 -- "$NICE_DEPLOY_STATE_DIR" "$BUNDLE_ROOT" "$RELEASE_ROOT"
[[ ! -L "$NICE_DEPLOY_STATE_DIR" && ! -L "$BUNDLE_ROOT" && ! -L "$RELEASE_ROOT" ]] ||
  die "deployment launcher directories must not be symlinks" 78
[[ $(stat -c '%u' "$NICE_DEPLOY_STATE_DIR") == 0 &&
  $(stat -c '%u' "$BUNDLE_ROOT") == 0 &&
  $(stat -c '%u' "$RELEASE_ROOT") == 0 ]] ||
  die "deployment launcher directories must be root-owned" 78
if [[ -e "$LOCK_FILE" && ( ! -f "$LOCK_FILE" || -L "$LOCK_FILE" ) ]]; then
  die "deployment lock is insecure" 78
fi
touch "$LOCK_FILE"
chown root:root "$LOCK_FILE"
chmod 600 "$LOCK_FILE"
if [[ ${NICE_DEPLOY_INSTALLER_LOCKED:-} == 1 ]]; then
  [[ -e /proc/self/fd/9 && $(readlink /proc/self/fd/9) == "$LOCK_FILE" ]] ||
    die "deployment installer lock was not inherited" 78
else
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "another Nice Assistant deployment action is active" 75
fi

validate_digest() {
  local digest=$1 suffix
  [[ "$digest" == "${NICE_APPROVED_IMAGE_PREFIX}@sha256:"* ]] ||
    die "only an immutable digest from the approved repository is allowed" 64
  suffix=${digest#"${NICE_APPROVED_IMAGE_PREFIX}@sha256:"}
  [[ "$suffix" =~ ^[0-9a-f]{64}$ ]] ||
    die "only an immutable digest from the approved repository is allowed" 64
}

parse_command() {
  local original=${SSH_ORIGINAL_COMMAND-}
  ACTION=
  DIGEST=
  if [[ -n ${SSH_ORIGINAL_COMMAND+x} ]]; then
    case "$original" in
      inspect | backup | health | logs | rollback | rollback-guard)
        ACTION=$original
        ;;
      deploy\ * | update-guard\ *)
        ACTION=${original%% *}
        DIGEST=${original#* }
        [[ "$original" == "$ACTION $DIGEST" && "$DIGEST" != *[[:space:]]* ]] ||
          die "invalid deployment command" 64
        validate_digest "$DIGEST"
        ;;
      *) die "deployment command is not allowed" 64 ;;
    esac
    return
  fi

  (($# >= 1)) || die "an allowed deployment action is required" 64
  ACTION=$1
  shift
  case "$ACTION" in
    inspect | backup | health | logs | rollback | rollback-guard)
      (($# == 0)) || die "invalid deployment command" 64
      ;;
    deploy | update-guard | bootstrap-guard)
      (($# == 1)) || die "deployment command requires one immutable image digest" 64
      DIGEST=$1
      validate_digest "$DIGEST"
      ;;
    *) die "deployment command is not allowed" 64 ;;
  esac
}

bundle_target() {
  local link=$1 target
  [[ -L "$link" ]] || return 1
  target=$(readlink "$link") || return 1
  [[ "$target" =~ ^releases/sha256-[0-9a-f]{64}$ ]] || return 1
  [[ -d "$BUNDLE_ROOT/$target" && ! -L "$BUNDLE_ROOT/$target" ]] || return 1
  printf '%s\n' "$target"
}

cleanup_pointer_temps() {
  local current_next="$BUNDLE_ROOT/.current.next"
  local previous_next="$BUNDLE_ROOT/.previous.next"
  local next_target current_target= previous_target=
  local has_current_next=false has_previous_next=false
  [[ -e "$current_next" || -L "$current_next" ]] && has_current_next=true
  [[ -e "$previous_next" || -L "$previous_next" ]] && has_previous_next=true
  [[ "$has_current_next" == true || "$has_previous_next" == true ]] || return 0

  if [[ "$has_current_next" == true ]]; then
    next_target=$(bundle_target "$current_next") || return 1
    validate_bundle "$BUNDLE_ROOT/$next_target" || return 1
  fi
  if [[ "$has_previous_next" == true ]]; then
    bundle_target "$previous_next" >/dev/null || return 1
  fi

  if [[ "$has_current_next" == true && "$has_previous_next" == true ]]; then
    # Neither permanent pointer has changed yet.
    rm -f -- "$current_next" "$previous_next"
    return
  fi
  [[ "$has_current_next" == true ]] || return 1

  if [[ -e "$CURRENT_LINK" || -L "$CURRENT_LINK" ]]; then
    current_target=$(bundle_target "$CURRENT_LINK") || return 1
  fi
  if [[ -e "$PREVIOUS_LINK" || -L "$PREVIOUS_LINK" ]]; then
    previous_target=$(bundle_target "$PREVIOUS_LINK") || return 1
  fi
  if [[ -z "$current_target" ]]; then
    # Initial activation was interrupted after validation.
    mv -Tf -- "$current_next" "$CURRENT_LINK"
  elif [[ -n "$previous_target" && "$current_target" == "$previous_target" ]]; then
    # The previous pointer moved first; finish the intended two-pointer switch.
    mv -Tf -- "$current_next" "$CURRENT_LINK"
  else
    # The process stopped after staging current.next but before changing state.
    rm -f -- "$current_next"
  fi
}

manifest_is_strict() {
  local manifest=$1
  "$JQ" -e '
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
  ' "$manifest" >/dev/null 2>&1
}

validate_bundle() {
  local directory=$1 manifest file expected actual mode
  [[ -d "$directory" && ! -L "$directory" ]] || return 1
  [[ $(stat -c '%u' "$directory") == 0 && $(stat -c '%a' "$directory") == 700 ]] ||
    return 1
  manifest="$directory/guard_bundle_manifest.json"
  secure_regular_file "$manifest" 600 || return 1
  [[ $(stat -c '%h' "$manifest") == 1 ]] || return 1
  manifest_is_strict "$manifest" || return 1
  for file in \
    nice_assistant_deploy_guard.sh \
    create_container_payload.jq \
    normalize_container_config.jq; do
    mode=$("$JQ" -er --arg file "$file" '.files[$file].mode' "$manifest") || return 1
    secure_regular_file "$directory/$file" "${mode#0}" || return 1
    [[ $(stat -c '%h' "$directory/$file") == 1 ]] || return 1
    expected=$("$JQ" -er --arg file "$file" '.files[$file].sha256' "$manifest") || return 1
    actual=$(sha256sum "$directory/$file")
    actual=${actual%% *}
    [[ "$actual" == "$expected" ]] || return 1
  done
}

safe_copied_file() {
  local path=$1 limit=$2 mode=$3
  [[ -f "$path" && ! -L "$path" ]] || return 1
  [[ $(stat -c '%F' "$path") == "regular file" ]] || return 1
  [[ $(stat -c '%h' "$path") == 1 ]] || return 1
  [[ $(stat -c '%s' "$path") -le "$limit" ]] || return 1
  [[ $(stat -c '%a' "$path") == "$mode" ]] || return 1
}

current_repo_digest() {
  local image_id configured_image resolved_id
  configured_image=$("$DOCKER" container inspect --format '{{.Config.Image}}' \
    "$NICE_CONTAINER_NAME" 2>/dev/null) || return 1
  image_id=$("$DOCKER" container inspect --format '{{.Image}}' "$NICE_CONTAINER_NAME" 2>/dev/null) ||
    return 1
  if [[ "$configured_image" == "${NICE_APPROVED_IMAGE_PREFIX}@sha256:"* ]]; then
    validate_digest "$configured_image"
    resolved_id=$("$DOCKER" image inspect --format '{{.Id}}' "$configured_image" 2>/dev/null) ||
      return 1
    [[ "$resolved_id" == "$image_id" ]] || return 1
    printf '%s\n' "$configured_image"
    return
  fi
  "$DOCKER" image inspect "$image_id" 2>/dev/null |
    "$JQ" -er --arg prefix "$NICE_APPROVED_IMAGE_PREFIX@" \
      '.[0].RepoDigests | map(select(startswith($prefix))) |
       if length == 1 then .[0] else error("ambiguous digest") end'
}

helper_is_owned() {
  local name=$1 hex=$2 label running
  "$DOCKER" container inspect "$name" >/dev/null 2>&1 || return 0
  label=$("$DOCKER" container inspect --format \
    '{{index .Config.Labels "com.nice-assistant.guard-update"}}' "$name" 2>/dev/null) ||
    return 1
  running=$("$DOCKER" container inspect --format '{{.State.Running}}' "$name" 2>/dev/null) ||
    return 1
  [[ "$label" == "$hex" && "$running" == false ]]
}

remove_helper() {
  local name=$1 hex=$2
  [[ "$name" =~ ^${NICE_CONTAINER_NAME//./\\.}\.guard-(extract|probe)\.[0-9a-f]{12}\.[0-9]+$ ]] ||
    return 1
  "$DOCKER" container inspect "$name" >/dev/null 2>&1 || return 0
  helper_is_owned "$name" "$hex" || return 1
  "$DOCKER" rm "$name" >/dev/null 2>&1
}

cleanup_update_artifacts() {
  if [[ -e "$UPDATE_JOURNAL_TMP" ]]; then
    secure_regular_file "$UPDATE_JOURNAL_TMP" 600 || return 1
    rm -f -- "$UPDATE_JOURNAL_TMP"
  fi
  [[ -e "$UPDATE_JOURNAL" ]] || return 0
  secure_regular_file "$UPDATE_JOURNAL" 600 || return 1
  local hex extract probe staging
  hex=$("$JQ" -er '.digest_hex | select(test("^[0-9a-f]{64}$"))' "$UPDATE_JOURNAL") ||
    return 1
  extract=$("$JQ" -er '.extract | select(type == "string")' "$UPDATE_JOURNAL") ||
    return 1
  probe=$("$JQ" -er '.probe | select(type == "string")' "$UPDATE_JOURNAL") ||
    return 1
  staging=$("$JQ" -er '.staging | select(type == "string")' "$UPDATE_JOURNAL") ||
    return 1
  remove_helper "$extract" "$hex" || return 1
  remove_helper "$probe" "$hex" || return 1
  [[ "$staging" == "$BUNDLE_ROOT/.guard-update-${hex}."* ]] || return 1
  [[ "${staging#"$BUNDLE_ROOT/"}" != */* && ! -L "$staging" ]] || return 1
  [[ ! -e "$staging" || -d "$staging" ]] || return 1
  rm -rf -- "$staging"
  rm -f -- "$UPDATE_JOURNAL"
}

write_update_journal() {
  local hex=$1 extract=$2 probe=$3 staging=$4
  "$JQ" -n \
    --arg digest_hex "$hex" \
    --arg extract "$extract" \
    --arg probe "$probe" \
    --arg staging "$staging" \
    '{schema_version:1,digest_hex:$digest_hex,extract:$extract,probe:$probe,staging:$staging}' \
    >"$UPDATE_JOURNAL_TMP"
  chmod 600 "$UPDATE_JOURNAL_TMP"
  chown root:root "$UPDATE_JOURNAL_TMP"
  mv -f -- "$UPDATE_JOURNAL_TMP" "$UPDATE_JOURNAL"
}

create_container_from_payload() {
  local name=$1 payload=$2 response=$3 api_version
  api_version=$("$DOCKER" version --format '{{.Server.APIVersion}}' 2>/dev/null) ||
    return 1
  "$CURL" --silent --fail \
    --unix-socket /var/run/docker.sock \
    -H 'Content-Type: application/json' \
    -X POST --data-binary "@$payload" \
    "http://localhost/v${api_version}/containers/create?name=${name}" \
    >"$response" 2>/dev/null || return 1
  "$JQ" -e '.Id | type == "string" and length > 0' "$response" >/dev/null 2>&1
}

CANONICAL_FILTER='
  .[0] as $container |
  {
    Config: ($container.Config
      | del(.Image)
      | if .Hostname == ($container.Id[0:12]) then .Hostname = "__docker_default__" else . end
      | .Labels = ((.Labels // {})
        | del(.["com.nice-assistant.guard-update"])
        | with_entries(select(.key | startswith("org.opencontainers.image.") | not)))),
    HostConfig: ($container.HostConfig
      | .OomKillDisable = (.OomKillDisable // false)),
    Networks: (($container.NetworkSettings.Networks // {}) | with_entries(.value |= {
      Aliases: ((.Aliases // [])
        | map(select(. != $container.Id and . != ($container.Id[0:12])
          and . != ($container.Name | ltrimstr("/"))
          and . != $managed_name))
        | sort
        | if length == 0 then null else . end),
      Links: (.Links // null),
      DriverOpts: (.DriverOpts // null),
      IPAMConfig: (.IPAMConfig // null),
      MacAddress: ((.MacAddress // "") | if . == "" then null else . end),
      GwPriority: (.GwPriority // 0)
    }))
  }
'

EXPECTED_PAYLOAD_FILTER='
  def clean_endpoint($container):
    {
      Aliases: ((.Aliases // [])
        | map(select(. != $container.Id and . != ($container.Id[0:12])))
        | if length == 0 then null else . end),
      Links: (.Links // null),
      DriverOpts: (.DriverOpts // null),
      IPAMConfig: (.IPAMConfig // null),
      MacAddress: ((.MacAddress // "") | if . == "" then null else . end),
      GwPriority: ((.GwPriority // 0) | if . == 0 then null else . end)
    } | with_entries(select(.value != null and .value != {}));

  .[0] as $container |
  (($image_labels // {})
    | with_entries(select(.key | startswith("org.opencontainers.image.")))) as $image_labels |
  ($container.Config
    | if .Hostname == ($container.Id[0:12]) then del(.Hostname) else . end
    | .Labels = ((.Labels // {}) + $image_labels)
    | .Image = $image) +
  {
    HostConfig: $container.HostConfig,
    NetworkingConfig: {
      EndpointsConfig: (($container.NetworkSettings.Networks // {})
        | with_entries(.value |= clean_endpoint($container)))
    }
  }
'

verify_definition_probe() {
  local release=$1 digest=$2 hex=$3 probe=$4 staging=$5
  local live payload expected_payload candidate_payload response inspected before after image_labels
  local candidate_before candidate_after expected_serialized candidate_serialized
  live="$staging/live.json"
  payload="$staging/probe-payload.json"
  expected_payload="$staging/expected-payload.json"
  candidate_payload="$staging/candidate-payload.json"
  response="$staging/probe-response.json"
  inspected="$staging/probe-inspect.json"
  "$DOCKER" container inspect "$NICE_CONTAINER_NAME" >"$live" 2>/dev/null || return 1
  image_labels=$("$DOCKER" image inspect "$digest" 2>/dev/null |
    "$JQ" -cer '.[0].Config.Labels // {}') || return 1
  "$JQ" \
    --arg image "$digest" \
    --argjson image_labels "$image_labels" \
    -f "$release/create_container_payload.jq" "$live" >"$candidate_payload" ||
    return 1
  "$JQ" \
    --arg image "$digest" \
    --argjson image_labels "$image_labels" \
    "$EXPECTED_PAYLOAD_FILTER" "$live" >"$expected_payload" ||
    return 1
  candidate_serialized=$("$JQ" -cS . "$candidate_payload") || return 1
  expected_serialized=$("$JQ" -cS . "$expected_payload") || return 1
  [[ "$candidate_serialized" == "$expected_serialized" ]] ||
    return 1
  "$JQ" --arg label "$hex" \
      '.Labels = ((.Labels // {}) + {"com.nice-assistant.guard-update":$label})' \
      "$candidate_payload" >"$payload" || return 1
  chmod 600 "$payload"
  create_container_from_payload "$probe" "$payload" "$response" || return 1
  "$DOCKER" container inspect "$probe" >"$inspected" 2>/dev/null || return 1
  before=$("$JQ" -cS --arg managed_name "$NICE_CONTAINER_NAME" \
    "$CANONICAL_FILTER" "$live") || return 1
  after=$("$JQ" -cS --arg managed_name "$NICE_CONTAINER_NAME" \
    "$CANONICAL_FILTER" "$inspected") || return 1
  [[ "$before" == "$after" ]] || return 1
  candidate_before=$("$JQ" -cS --arg managed_name "$NICE_CONTAINER_NAME" \
    -f "$release/normalize_container_config.jq" "$live") || return 1
  candidate_after=$("$JQ" -cS --arg managed_name "$NICE_CONTAINER_NAME" \
    -f "$release/normalize_container_config.jq" "$inspected") || return 1
  [[ "$candidate_before" == "$candidate_after" &&
    "$candidate_before" == "$before" ]] || return 1
  remove_helper "$probe" "$hex"
}

validate_candidate_programs() {
  local release=$1 digest=$2 fixture
  fixture="$release/.fixture.json"
  printf '%s\n' \
    '[{"Id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","Name":"/nice-assistant","Config":{"Hostname":"aaaaaaaaaaaa","Labels":{}},"HostConfig":{},"NetworkSettings":{"Networks":{}}}]' \
    >"$fixture"
  bash -n "$release/nice_assistant_deploy_guard.sh" >/dev/null 2>&1 || return 1
  "$JQ" --arg image "$digest" --argjson image_labels '{}' \
    -f "$release/create_container_payload.jq" "$fixture" >/dev/null 2>&1 || return 1
  "$JQ" --arg managed_name "$NICE_CONTAINER_NAME" \
    -f "$release/normalize_container_config.jq" "$fixture" >/dev/null 2>&1 ||
    return 1
  rm -f -- "$fixture"
}

activate_bundle() {
  local target=$1 old_target= current_next previous_next
  if [[ -e "$CURRENT_LINK" || -L "$CURRENT_LINK" ]]; then
    old_target=$(bundle_target "$CURRENT_LINK") ||
      die "active deployment guard bundle is invalid" 78
  fi
  if [[ "$old_target" == "$target" ]]; then
    return 0
  fi
  current_next="$BUNDLE_ROOT/.current.next"
  previous_next="$BUNDLE_ROOT/.previous.next"
  cleanup_pointer_temps ||
    die "deployment guard pointer staging is insecure" 78
  ln -s "$target" "$current_next"
  if [[ -n "$old_target" ]]; then
    ln -s "$old_target" "$previous_next"
    mv -Tf -- "$previous_next" "$PREVIOUS_LINK"
  fi
  mv -Tf -- "$current_next" "$CURRENT_LINK"
}

install_guard_bundle() {
  local digest=$1 bootstrap=$2 hex revision source_label image_json extract probe staging raw candidate
  local destination target current_target current_version candidate_version current_manifest candidate_manifest
  local candidate_serialized current_serialized
  validate_digest "$digest"
  if [[ "$bootstrap" != true ]]; then
    local running_digest
    running_digest=$(current_repo_digest) ||
      die "running Nice Assistant digest is unavailable" 69
    [[ "$digest" == "$running_digest" ]] ||
      die "guard updates require the exact running Nice Assistant digest" 64
  fi

  "$DOCKER" pull "$digest" >/dev/null 2>&1 ||
    die "approved guard image could not be pulled" 69
  hex=${digest##*:}
  staging="$BUNDLE_ROOT/.guard-update-${hex}.$$"
  extract="${NICE_CONTAINER_NAME}.guard-extract.${hex:0:12}.$$"
  probe="${NICE_CONTAINER_NAME}.guard-probe.${hex:0:12}.$$"
  [[ ! -e "$staging" && ! -L "$staging" ]] ||
    die "guard update staging path already exists" 78
  write_update_journal "$hex" "$extract" "$probe" "$staging"
  trap 'cleanup_update_artifacts >/dev/null 2>&1 || true' EXIT HUP INT TERM
  mkdir -m 700 -- "$staging"
  raw="$staging/raw"
  candidate="$staging/release"
  mkdir -m 700 -- "$raw" "$candidate"

  image_json="$staging/image.json"
  "$DOCKER" image inspect "$digest" >"$image_json" 2>/dev/null ||
    die "approved guard image metadata is unavailable" 69
  revision=$("$JQ" -er '.[0].Config.Labels["org.opencontainers.image.revision"]' "$image_json") ||
    die "guard image has no source revision" 69
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] ||
    die "guard image has no valid source revision" 69
  source_label=$("$JQ" -er '.[0].Config.Labels["org.opencontainers.image.source"]' "$image_json") ||
    die "guard image has no source label" 69
  [[ "${source_label,,}" == "$EXPECTED_SOURCE" ]] ||
    die "guard image source is not approved" 69
  "$JQ" -e '((.[0].Config.Volumes // {}) | length) == 0' "$image_json" >/dev/null ||
    die "guard image declares unsupported volumes" 69

  "$DOCKER" create \
    --name "$extract" \
    --label "com.nice-assistant.guard-update=$hex" \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --entrypoint /bin/false \
    "$digest" >/dev/null 2>&1 ||
    die "guard extraction container could not be created" 70

  "$DOCKER" cp \
    "$extract:/opt/nice-assistant/scripts/deployment/guard_bundle_manifest.json" \
    "$raw/guard_bundle_manifest.json" >/dev/null 2>&1 ||
    die "guard bundle manifest is unavailable" 70
  "$DOCKER" cp \
    "$extract:/opt/nice-assistant/scripts/deployment/nice_assistant_deploy_guard.sh" \
    "$raw/nice_assistant_deploy_guard.sh" >/dev/null 2>&1 ||
    die "guard program is unavailable" 70
  "$DOCKER" cp \
    "$extract:/opt/nice-assistant/scripts/deployment/create_container_payload.jq" \
    "$raw/create_container_payload.jq" >/dev/null 2>&1 ||
    die "guard create filter is unavailable" 70
  "$DOCKER" cp \
    "$extract:/opt/nice-assistant/scripts/deployment/normalize_container_config.jq" \
    "$raw/normalize_container_config.jq" >/dev/null 2>&1 ||
    die "guard normalization filter is unavailable" 70
  remove_helper "$extract" "$hex" ||
    die "guard extraction container could not be removed" 70

  safe_copied_file "$raw/guard_bundle_manifest.json" "$MAX_MANIFEST_BYTES" 600 &&
    safe_copied_file "$raw/nice_assistant_deploy_guard.sh" "$MAX_GUARD_BYTES" 700 &&
    safe_copied_file "$raw/create_container_payload.jq" "$MAX_FILTER_BYTES" 600 &&
    safe_copied_file "$raw/normalize_container_config.jq" "$MAX_FILTER_BYTES" 600 ||
    die "guard bundle contains an unsafe file" 70
  manifest_is_strict "$raw/guard_bundle_manifest.json" ||
    die "guard bundle manifest is invalid" 70

  install -o root -g root -m 0600 \
    "$raw/guard_bundle_manifest.json" "$candidate/guard_bundle_manifest.json"
  install -o root -g root -m 0700 \
    "$raw/nice_assistant_deploy_guard.sh" "$candidate/nice_assistant_deploy_guard.sh"
  install -o root -g root -m 0600 \
    "$raw/create_container_payload.jq" "$candidate/create_container_payload.jq"
  install -o root -g root -m 0600 \
    "$raw/normalize_container_config.jq" "$candidate/normalize_container_config.jq"
  validate_bundle "$candidate" ||
    die "guard bundle checksums or modes are invalid" 70
  validate_candidate_programs "$candidate" "$digest" ||
    die "guard bundle programs are invalid" 70
  verify_definition_probe "$candidate" "$digest" "$hex" "$probe" "$staging" ||
    die "guard bundle did not preserve the Nice Assistant container definition" 70

  candidate_manifest="$candidate/guard_bundle_manifest.json"
  candidate_version=$("$JQ" -er '.bundle_version' "$candidate_manifest")
  if [[ -e "$CURRENT_LINK" || -L "$CURRENT_LINK" ]]; then
    current_target=$(bundle_target "$CURRENT_LINK") ||
      die "active deployment guard bundle is invalid" 78
    validate_bundle "$BUNDLE_ROOT/$current_target" ||
      die "active deployment guard bundle is invalid" 78
    current_manifest="$BUNDLE_ROOT/$current_target/guard_bundle_manifest.json"
    current_version=$("$JQ" -er '.bundle_version' "$current_manifest")
    ((candidate_version >= current_version)) ||
      die "deployment guard bundle downgrade is not allowed" 76
    if ((candidate_version == current_version)); then
      candidate_serialized=$("$JQ" -cS . "$candidate_manifest")
      current_serialized=$("$JQ" -cS . "$current_manifest")
      [[ "$candidate_serialized" == "$current_serialized" ]] ||
        die "equal-version deployment guard bundles must be identical" 76
    fi
  fi

  destination="$RELEASE_ROOT/sha256-$hex"
  target="releases/sha256-$hex"
  if [[ -e "$destination" ]]; then
    validate_bundle "$destination" ||
      die "existing deployment guard release is invalid" 78
    candidate_serialized=$("$JQ" -cS . "$candidate_manifest")
    current_serialized=$("$JQ" -cS . "$destination/guard_bundle_manifest.json")
    [[ "$candidate_serialized" == "$current_serialized" ]] ||
      die "existing deployment guard release does not match" 78
    rm -rf -- "$candidate"
  else
    mv -- "$candidate" "$destination"
  fi
  validate_bundle "$destination" ||
    die "installed deployment guard release is invalid" 78
  cleanup_update_artifacts ||
    die "guard update cleanup failed before activation" 70
  trap - EXIT HUP INT TERM
  activate_bundle "$target"
  printf '{"ok":true,"action":"%s","digest":"%s","revision":"%s","bundle_version":%s}\n' \
    "$([[ "$bootstrap" == true ]] && printf bootstrap-guard || printf update-guard)" \
    "$digest" "$revision" "$candidate_version"
}

rollback_guard_bundle() {
  local current_target previous_target current_next previous_next version
  current_target=$(bundle_target "$CURRENT_LINK") ||
    die "active deployment guard bundle is invalid" 78
  previous_target=$(bundle_target "$PREVIOUS_LINK") ||
    die "no previous deployment guard bundle is available" 69
  validate_bundle "$BUNDLE_ROOT/$current_target" &&
    validate_bundle "$BUNDLE_ROOT/$previous_target" ||
    die "deployment guard rollback bundle is invalid" 78
  current_next="$BUNDLE_ROOT/.current.next"
  previous_next="$BUNDLE_ROOT/.previous.next"
  cleanup_pointer_temps ||
    die "deployment guard pointer staging is insecure" 78
  ln -s "$previous_target" "$current_next"
  ln -s "$current_target" "$previous_next"
  mv -Tf -- "$previous_next" "$PREVIOUS_LINK"
  mv -Tf -- "$current_next" "$CURRENT_LINK"
  version=$("$JQ" -er '.bundle_version' \
    "$BUNDLE_ROOT/$previous_target/guard_bundle_manifest.json")
  printf '{"ok":true,"action":"rollback-guard","bundle":"%s","bundle_version":%s}\n' \
    "${previous_target#releases/sha256-}" "$version"
}

delegate_guard() {
  local target guard
  target=$(bundle_target "$CURRENT_LINK") ||
    die "active deployment guard bundle is unavailable" 78
  validate_bundle "$BUNDLE_ROOT/$target" ||
    die "active deployment guard bundle is invalid" 78
  guard="$BUNDLE_ROOT/$target/nice_assistant_deploy_guard.sh"
  if [[ -n "$DIGEST" ]]; then
    exec /usr/bin/env -i \
      PATH=/usr/sbin:/usr/bin:/sbin:/bin \
      NICE_DEPLOY_GUARD_CONFIG="$CONFIG_FILE" \
      NICE_DEPLOY_LAUNCHER_LOCKED=1 \
      "$guard" "$ACTION" "$DIGEST"
  fi
  exec /usr/bin/env -i \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    NICE_DEPLOY_GUARD_CONFIG="$CONFIG_FILE" \
    NICE_DEPLOY_LAUNCHER_LOCKED=1 \
    "$guard" "$ACTION"
}

cleanup_pointer_temps ||
  die "interrupted deployment guard pointer cleanup failed" 70
cleanup_update_artifacts ||
  die "an interrupted guard update could not be cleaned safely" 70
parse_command "$@"

case "$ACTION" in
  update-guard) install_guard_bundle "$DIGEST" false ;;
  bootstrap-guard) install_guard_bundle "$DIGEST" true ;;
  rollback-guard) rollback_guard_bundle ;;
  *) delegate_guard ;;
esac
