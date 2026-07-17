#!/bin/bash
# Bootstrap or migrate the permanent deployment launcher in a supervised root session.

set -Eeuo pipefail
set -f
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
unset BASH_ENV ENV CDPATH GLOBIGNORE

usage() {
  echo "usage: $0 --container NAME --image-prefix ghcr.io/OWNER/nice-assistant --guard-image IMMUTABLE_DIGEST --public-key FILE --source IPV4_OR_CIDR --state-dir DIR --authorized-keys FILE [--unraid-template FILE]" >&2
  exit 64
}

CONTAINER_NAME=
IMAGE_PREFIX=
GUARD_IMAGE=
PUBLIC_KEY=
SOURCE=
STATE_DIR=
AUTHORIZED_KEYS=
UNRAID_TEMPLATE=
while (($#)); do
  case "$1" in
    --container) CONTAINER_NAME=${2:-}; shift 2 ;;
    --image-prefix) IMAGE_PREFIX=${2:-}; shift 2 ;;
    --guard-image) GUARD_IMAGE=${2:-}; shift 2 ;;
    --public-key) PUBLIC_KEY=${2:-}; shift 2 ;;
    --source) SOURCE=${2:-}; shift 2 ;;
    --state-dir) STATE_DIR=${2:-}; shift 2 ;;
    --authorized-keys) AUTHORIZED_KEYS=${2:-}; shift 2 ;;
    --unraid-template) UNRAID_TEMPLATE=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "root is required" >&2; exit 77; }
[[ "$CONTAINER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || usage
[[ "$IMAGE_PREFIX" =~ ^ghcr\.io/[a-z0-9_.-]+/nice-assistant$ ]] || usage
[[ "$GUARD_IMAGE" =~ ^${IMAGE_PREFIX//./\\.}@sha256:[0-9a-f]{64}$ ]] || usage
[[ -f "$PUBLIC_KEY" && ! -L "$PUBLIC_KEY" && -n "$SOURCE" &&
  -n "$STATE_DIR" && -n "$AUTHORIZED_KEYS" ]] || usage

validate_source() {
  local source=$1 address prefix= octet
  local -a octets
  [[ "$source" != *,* && "$source" != *[[:space:]]* ]] || return 1
  if [[ "$source" == */* ]]; then
    address=${source%/*}
    prefix=${source##*/}
    [[ "$prefix" =~ ^([1-9]|[12][0-9]|3[0-2])$ ]] || return 1
  else
    address=$source
  fi
  IFS=. read -r -a octets <<<"$address"
  [[ ${#octets[@]} -eq 4 ]] || return 1
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^(0|[1-9][0-9]{0,2})$ ]] || return 1
    ((10#$octet <= 255)) || return 1
  done
}

validate_source "$SOURCE" || usage
[[ "$STATE_DIR" =~ ^/[A-Za-z0-9_./-]+$ &&
  "$STATE_DIR" != / &&
  "$STATE_DIR" != */ &&
  "$STATE_DIR" != *"//"* &&
  "$STATE_DIR" != *"/./"* &&
  "$STATE_DIR" != *"/." &&
  "$STATE_DIR" != *"/../"* &&
  "$STATE_DIR" != *"/.." ]] || usage
[[ "$AUTHORIZED_KEYS" =~ ^/[A-Za-z0-9_./-]+$ &&
  "$AUTHORIZED_KEYS" != */ &&
  "$AUTHORIZED_KEYS" != *"//"* &&
  "$AUTHORIZED_KEYS" != *"/./"* &&
  "$AUTHORIZED_KEYS" != *"/." &&
  "$AUTHORIZED_KEYS" != *"/../"* &&
  "$AUTHORIZED_KEYS" != *"/.." ]] || usage
[[ -z "$UNRAID_TEMPLATE" ||
  ( "$UNRAID_TEMPLATE" =~ ^/[A-Za-z0-9_./-]+$ &&
    "$UNRAID_TEMPLATE" != */ &&
    "$UNRAID_TEMPLATE" != *"//"* &&
    "$UNRAID_TEMPLATE" != *"/./"* &&
    "$UNRAID_TEMPLATE" != *"/." &&
    "$UNRAID_TEMPLATE" != *"/../"* &&
    "$UNRAID_TEMPLATE" != *"/.." ) ]] || usage

for command in \
  docker curl jq flock stat install chown cp ssh-keygen sha256sum readlink \
  bash awk mktemp dirname mv chmod touch tr ln rm; do
  command -v "$command" >/dev/null || {
    echo "$command is required" >&2
    exit 69
  }
done
[[ $(readlink -m -- "$STATE_DIR") == "$STATE_DIR" ]] || usage
[[ $(readlink -m -- "$AUTHORIZED_KEYS") == "$AUTHORIZED_KEYS" ]] || usage
if [[ -n "$UNRAID_TEMPLATE" ]]; then
  [[ $(readlink -m -- "$UNRAID_TEMPLATE") == "$UNRAID_TEMPLATE" ]] || usage
fi
docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1 || {
  echo "the Nice Assistant container is unavailable" >&2
  exit 69
}

DOCKER_BIN=$(command -v docker)
CURL_BIN=$(command -v curl)
JQ_BIN=$(command -v jq)
INSTALL_DIR="$STATE_DIR/bin"
STATE_DATA_DIR="$STATE_DIR/state"
DEFINITION_FILE="$STATE_DATA_DIR/container-definition.json"
SOURCE_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
LAUNCHER_SOURCE="$SOURCE_DIR/nice_assistant_deploy_launcher.sh"
[[ -f "$LAUNCHER_SOURCE" && ! -L "$LAUNCHER_SOURCE" ]] || {
  echo "the permanent deployment launcher is unavailable" >&2
  exit 66
}

secure_directory_ancestors() {
  local path=$1 current= component mode
  local -a components
  IFS=/ read -r -a components <<<"${path#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    current="$current/$component"
    [[ -e "$current" || -L "$current" ]] || break
    [[ -d "$current" && ! -L "$current" ]] || return 1
    [[ $(stat -c '%u' "$current") == 0 ]] || return 1
    mode=$(stat -c '%a' "$current")
    (( (8#$mode & 0022) == 0 )) || return 1
  done
}

AUTHORIZED_KEYS_DIR=$(dirname -- "$AUTHORIZED_KEYS")
secure_directory_ancestors "$STATE_DIR" ||
  { echo "deployment state ancestors are insecure" >&2; exit 78; }
secure_directory_ancestors "$AUTHORIZED_KEYS_DIR" ||
  { echo "authorized_keys ancestors are insecure" >&2; exit 78; }

for directory in "$STATE_DIR" "$INSTALL_DIR" "$STATE_DATA_DIR"; do
  if [[ -e "$directory" && ( ! -d "$directory" || -L "$directory" ) ]]; then
    echo "a deployment directory is insecure" >&2
    exit 78
  fi
done
mkdir -p -- "$INSTALL_DIR" "$STATE_DATA_DIR"
chown root:root "$STATE_DIR" "$INSTALL_DIR" "$STATE_DATA_DIR"
chmod 700 "$STATE_DIR" "$INSTALL_DIR" "$STATE_DATA_DIR"

FILESYSTEM_TEST="$STATE_DIR/.filesystem-contract"
if [[ -e "$FILESYSTEM_TEST" || -L "$FILESYSTEM_TEST" ]]; then
  [[ -d "$FILESYSTEM_TEST" && ! -L "$FILESYSTEM_TEST" &&
    $(stat -c '%u' "$FILESYSTEM_TEST") == 0 ]] || {
    echo "the deployment filesystem test path is insecure" >&2
    exit 78
  }
  rm -rf -- "$FILESYSTEM_TEST"
fi
mkdir -m 700 -- "$FILESYSTEM_TEST"
printf '#!/bin/sh\nexit 0\n' >"$FILESYSTEM_TEST/program"
chmod 0700 "$FILESYSTEM_TEST/program"
printf 'ok\n' >"$FILESYSTEM_TEST/data"
chmod 0600 "$FILESYSTEM_TEST/data"
ln -s data "$FILESYSTEM_TEST/current.next"
mv -T -- "$FILESYSTEM_TEST/current.next" "$FILESYSTEM_TEST/current"
[[ $(readlink "$FILESYSTEM_TEST/current") == data &&
  $(stat -c '%a' "$FILESYSTEM_TEST") == 700 &&
  $(stat -c '%a' "$FILESYSTEM_TEST/program") == 700 &&
  $(stat -c '%a' "$FILESYSTEM_TEST/data") == 600 ]] &&
  "$FILESYSTEM_TEST/program" || {
    rm -rf -- "$FILESYSTEM_TEST"
    echo "deployment state must support secure modes, execution, symlinks, and atomic rename" >&2
    exit 78
  }
rm -rf -- "$FILESYSTEM_TEST"

CONFIG_NEXT="$INSTALL_DIR/.guard.conf.next.$$"
LAUNCHER_NEXT="$INSTALL_DIR/.nice-assistant-deploy-launcher.next.$$"
LIVE_CONFIG="$INSTALL_DIR/guard.conf"
LIVE_LAUNCHER="$INSTALL_DIR/nice-assistant-deploy-guard"
INSTALL_JOURNAL="$STATE_DIR/launcher-install.json"
INSTALL_JOURNAL_NEXT="$STATE_DIR/.launcher-install.next"
CONFIG_BACKUP="$STATE_DIR/guard.conf.pre-launcher"
CONFIG_BACKUP_NEXT="$STATE_DIR/.guard.conf.pre-launcher.next"
PUBLIC_KEY_STAGED="$STATE_DIR/.deployment-public-key.$$"
AUTHORIZED_KEYS_NEXT=
cleanup() {
  rm -f -- "$CONFIG_NEXT" "$LAUNCHER_NEXT" "$PUBLIC_KEY_STAGED"
  [[ -z "$AUTHORIZED_KEYS_NEXT" ]] || rm -f -- "$AUTHORIZED_KEYS_NEXT"
}
trap cleanup EXIT HUP INT TERM

secure_root_file() {
  local path=$1 mode=$2
  [[ -f "$path" && ! -L "$path" ]] || return 1
  [[ $(stat -c '%u' "$path") == 0 && $(stat -c '%a' "$path") == "$mode" ]]
}

for stale in "$INSTALL_JOURNAL_NEXT" "$CONFIG_BACKUP_NEXT"; do
  if [[ -e "$stale" || -L "$stale" ]]; then
    secure_root_file "$stale" 600 ||
      { echo "an interrupted launcher staging file is insecure" >&2; exit 78; }
    rm -f -- "$stale"
  fi
done

write_install_phase() {
  local phase=$1
  printf '{"schema_version":1,"phase":"%s"}\n' "$phase" >"$INSTALL_JOURNAL_NEXT"
  chown root:root "$INSTALL_JOURNAL_NEXT"
  chmod 0600 "$INSTALL_JOURNAL_NEXT"
  mv -f -- "$INSTALL_JOURNAL_NEXT" "$INSTALL_JOURNAL"
}

if [[ -e "$INSTALL_JOURNAL" ]]; then
  secure_root_file "$INSTALL_JOURNAL" 600 ||
    { echo "an interrupted launcher installation is insecure" >&2; exit 78; }
  jq -e '.schema_version == 1 and
    (.phase == "validated" or .phase == "config-switched" or .phase == "launcher-switched")' \
    "$INSTALL_JOURNAL" >/dev/null ||
    { echo "an interrupted launcher installation is invalid" >&2; exit 78; }
  if [[ -e "$CONFIG_BACKUP" ]]; then
    secure_root_file "$CONFIG_BACKUP" 600 ||
      { echo "the launcher configuration recovery file is insecure" >&2; exit 78; }
    install -o root -g root -m 0600 "$CONFIG_BACKUP" "$CONFIG_NEXT"
    mv -f -- "$CONFIG_NEXT" "$LIVE_CONFIG"
  fi
  rm -f -- "$INSTALL_JOURNAL" "$CONFIG_BACKUP"
fi

if [[ -e "$AUTHORIZED_KEYS_DIR" &&
  ( ! -d "$AUTHORIZED_KEYS_DIR" || -L "$AUTHORIZED_KEYS_DIR" ) ]]; then
  echo "the authorized_keys directory is insecure" >&2
  exit 78
fi
mkdir -p -- "$AUTHORIZED_KEYS_DIR"
chown root:root "$AUTHORIZED_KEYS_DIR"
chmod 0700 "$AUTHORIZED_KEYS_DIR"
if [[ -e "$AUTHORIZED_KEYS" && ( ! -f "$AUTHORIZED_KEYS" || -L "$AUTHORIZED_KEYS" ) ]]; then
  echo "authorized_keys is insecure" >&2
  exit 78
fi
touch "$AUTHORIZED_KEYS"
chown root:root "$AUTHORIZED_KEYS"
chmod 0600 "$AUTHORIZED_KEYS"

install -o root -g root -m 0600 "$PUBLIC_KEY" "$PUBLIC_KEY_STAGED"
ssh-keygen -l -f "$PUBLIC_KEY_STAGED" >/dev/null 2>&1 || {
  echo "the public key is invalid" >&2
  exit 65
}
KEY=$(tr -d '\r\n' <"$PUBLIC_KEY_STAGED")
[[ "$KEY" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+([[:space:]].*)?$ ]] || {
  echo "an ed25519 public key is required" >&2
  exit 65
}
MARKER="nice-assistant-deploy-guard"
AUTHORIZED_KEYS_NEXT=$(mktemp "$AUTHORIZED_KEYS_DIR/.authorized_keys.nice-assistant.XXXXXX")
awk -v marker="$MARKER" 'NF == 0 || $NF != marker' "$AUTHORIZED_KEYS" >"$AUTHORIZED_KEYS_NEXT"
FORCED_COMMAND='/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C SSH_ORIGINAL_COMMAND="$SSH_ORIGINAL_COMMAND" '"$INSTALL_DIR/nice-assistant-deploy-guard"
ESCAPED_FORCED_COMMAND=${FORCED_COMMAND//\"/\\\"}
printf 'restrict,from="%s",command="%s" %s %s\n' \
  "$SOURCE" "$ESCAPED_FORCED_COMMAND" "$KEY" "$MARKER" \
  >>"$AUTHORIZED_KEYS_NEXT"
chown root:root "$AUTHORIZED_KEYS_NEXT"
chmod 0600 "$AUTHORIZED_KEYS_NEXT"

if [[ -e "$LIVE_CONFIG" ]]; then
  secure_root_file "$LIVE_CONFIG" 600 ||
    { echo "the existing deployment configuration is insecure" >&2; exit 78; }
  (
    unset NICE_CONTAINER_NAME NICE_APPROVED_IMAGE_PREFIX NICE_DEPLOY_STATE_DIR
    # shellcheck disable=SC1090
    source "$LIVE_CONFIG"
    [[ ${NICE_CONTAINER_NAME:-} == "$CONTAINER_NAME" &&
      ${NICE_APPROVED_IMAGE_PREFIX:-} == "$IMAGE_PREFIX" &&
      ${NICE_DEPLOY_STATE_DIR:-} == "$STATE_DATA_DIR" ]]
  ) || {
    echo "legacy migration must preserve the enrolled container, repository, and state directory" >&2
    exit 78
  }
fi
if [[ -e "$LIVE_LAUNCHER" ]]; then
  secure_root_file "$LIVE_LAUNCHER" 700 ||
    { echo "the existing deployment guard executable is insecure" >&2; exit 78; }
fi

cat >"$CONFIG_NEXT" <<EOF
NICE_CONTAINER_NAME='$CONTAINER_NAME'
NICE_APPROVED_IMAGE_PREFIX='$IMAGE_PREFIX'
NICE_DEPLOY_STATE_DIR='$STATE_DATA_DIR'
NICE_UNRAID_TEMPLATE='$UNRAID_TEMPLATE'
NICE_DEPLOY_DOCKER_BIN='$DOCKER_BIN'
NICE_DEPLOY_CURL_BIN='$CURL_BIN'
NICE_DEPLOY_JQ_BIN='$JQ_BIN'
EOF
chown root:root "$CONFIG_NEXT"
chmod 0600 "$CONFIG_NEXT"

install -o root -g root -m 0700 "$LAUNCHER_SOURCE" "$LAUNCHER_NEXT"

if [[ -n "$UNRAID_TEMPLATE" ]]; then
  [[ -f "$UNRAID_TEMPLATE" && ! -L "$UNRAID_TEMPLATE" ]] || {
    echo "Unraid template was not found or is insecure" >&2
    exit 66
  }
  if [[ ! -e "$STATE_DIR/unraid-template.original.xml" ]]; then
    cp -p "$UNRAID_TEMPLATE" "$STATE_DIR/unraid-template.original.xml"
    chown root:root "$STATE_DIR/unraid-template.original.xml"
    chmod 0600 "$STATE_DIR/unraid-template.original.xml"
  fi
fi

LOCK_FILE="$STATE_DATA_DIR/deploy.lock"
if [[ -e "$LOCK_FILE" && ( ! -f "$LOCK_FILE" || -L "$LOCK_FILE" ) ]]; then
  echo "the deployment lock is insecure" >&2
  exit 78
fi
touch "$LOCK_FILE"
chown root:root "$LOCK_FILE"
chmod 0600 "$LOCK_FILE"
exec 9>"$LOCK_FILE"
flock -n 9 || {
  echo "another Nice Assistant deployment action is active" >&2
  exit 75
}

/usr/bin/env -i \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  LC_ALL=C \
  NICE_DEPLOY_LAUNCHER_CONFIG="$CONFIG_NEXT" \
  NICE_DEPLOY_INSTALLER_LOCKED=1 \
  "$LAUNCHER_NEXT" bootstrap-guard "$GUARD_IMAGE" >/dev/null

if [[ -e "$DEFINITION_FILE" || -L "$DEFINITION_FILE" ]]; then
  secure_root_file "$DEFINITION_FILE" 600 || {
    echo "the existing captured container definition is insecure" >&2
    exit 78
  }
fi
/usr/bin/env -i \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  LC_ALL=C \
  NICE_DEPLOY_LAUNCHER_CONFIG="$CONFIG_NEXT" \
  NICE_DEPLOY_INSTALLER_LOCKED=1 \
  "$LAUNCHER_NEXT" inspect >/dev/null
secure_root_file "$DEFINITION_FILE" 600 || {
  echo "the captured container definition was not persisted securely" >&2
  exit 78
}

if [[ -e "$LIVE_CONFIG" ]]; then
  install -o root -g root -m 0600 "$LIVE_CONFIG" "$CONFIG_BACKUP_NEXT"
  mv -f -- "$CONFIG_BACKUP_NEXT" "$CONFIG_BACKUP"
fi
write_install_phase validated

mv -f -- "$CONFIG_NEXT" "$LIVE_CONFIG"
write_install_phase config-switched

# Keep the forced-command path stable. This is the only switch from a legacy
# direct guard to the permanent launcher, and the rename is atomic.
mv -f -- "$LAUNCHER_NEXT" "$LIVE_LAUNCHER"
chown root:root "$LIVE_LAUNCHER"
chmod 0700 "$LIVE_LAUNCHER"
write_install_phase launcher-switched

mv -f -- "$AUTHORIZED_KEYS_NEXT" "$AUTHORIZED_KEYS"
AUTHORIZED_KEYS_NEXT=

rm -f -- "$INSTALL_JOURNAL" "$INSTALL_JOURNAL_NEXT" \
  "$CONFIG_BACKUP" "$CONFIG_BACKUP_NEXT" "$PUBLIC_KEY_STAGED"
trap - EXIT HUP INT TERM
echo "Nice Assistant permanent deployment launcher installed and definition-checked."
