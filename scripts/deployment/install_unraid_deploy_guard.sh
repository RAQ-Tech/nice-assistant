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
  local source=$1 address prefix='' octet
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
  bash awk mktemp dirname mv chmod touch tr ln rm findmnt sync; do
  command -v "$command" >/dev/null || {
    echo "$command is required" >&2
    exit 69
  }
done
[[ $(readlink -m -- "$STATE_DIR") == "$STATE_DIR" ]] || usage
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
  local path=$1 current='' component mode
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

secure_authorized_keys_file() {
  local path=$1
  [[ -f "$path" && ! -L "$path" &&
    $(stat -c '%u:%g' "$path") == 0:0 &&
    $(stat -c '%a' "$path") == 600 &&
    $(stat -c '%h' "$path") == 1 ]]
}

validate_authorized_keys_file_if_present() {
  local path=$1
  if [[ -e "$path" || -L "$path" ]]; then
    secure_authorized_keys_file "$path"
  fi
}

validate_generic_authorized_keys_layout() {
  [[ "$AUTHORIZED_KEYS_REQUESTED" != /boot &&
    "$AUTHORIZED_KEYS_REQUESTED" != /boot/* &&
    $(readlink -m -- "$AUTHORIZED_KEYS_REQUESTED") == "$AUTHORIZED_KEYS_REQUESTED" ]] &&
    secure_directory_ancestors "$AUTHORIZED_KEYS_DIR" &&
    validate_authorized_keys_file_if_present "$AUTHORIZED_KEYS"
}

validate_unraid_authorized_keys_layout() {
  local mount_record='' mount_target='' mount_fstype='' mount_options='' mount_extra=''
  [[ "$AUTHORIZED_KEYS_REQUESTED" == /root/.ssh/authorized_keys &&
    -d /root &&
    ! -L /root ]] || return 1
  secure_directory_ancestors /root || return 1
  [[ -L "$UNRAID_SSH_DIR" &&
    $(stat -c '%u:%g' "$UNRAID_SSH_DIR") == 0:0 &&
    $(readlink -- "$UNRAID_SSH_DIR") == "$UNRAID_SSH_TARGET" &&
    $(readlink -m -- "$UNRAID_SSH_DIR") == "$UNRAID_SSH_TARGET" &&
    $(readlink -m -- "$AUTHORIZED_KEYS_REQUESTED") == "$UNRAID_AUTHORIZED_KEYS" ]] ||
    return 1
  secure_directory_ancestors "$UNRAID_SSH_TARGET" || return 1
  [[ $(stat -c '%u:%g' "$UNRAID_SSH_TARGET") == 0:0 &&
    $(stat -c '%a' "$UNRAID_SSH_TARGET") == 700 ]] || return 1
  mount_record=$(
    findmnt -rn -T "$UNRAID_SSH_TARGET" -o TARGET,FSTYPE,OPTIONS
  ) || return 1
  [[ -n "$mount_record" && "$mount_record" != *$'\n'* ]] || return 1
  read -r mount_target mount_fstype mount_options mount_extra <<<"$mount_record"
  [[ "$mount_target" == /boot &&
    "$mount_fstype" == vfat &&
    -z "$mount_extra" &&
    ",$mount_options," == *,rw,* &&
    ",$mount_options," == *,fmask=0177,* &&
    ",$mount_options," == *,dmask=0077,* &&
    $(stat -f -c '%T' "$UNRAID_SSH_TARGET") == msdos ]] || return 1
  validate_authorized_keys_file_if_present "$AUTHORIZED_KEYS"
}

validate_effective_authorized_keys_layout() {
  if [[ "$AUTHORIZED_KEYS_LAYOUT" == unraid ]]; then
    validate_unraid_authorized_keys_layout
  else
    validate_generic_authorized_keys_layout
  fi
}

secure_directory_ancestors "$STATE_DIR" ||
  { echo "deployment state ancestors are insecure" >&2; exit 78; }

AUTHORIZED_KEYS_REQUESTED=$AUTHORIZED_KEYS
AUTHORIZED_KEYS_DIR=$(dirname -- "$AUTHORIZED_KEYS_REQUESTED")
AUTHORIZED_KEYS_LAYOUT=generic
if [[ "$AUTHORIZED_KEYS_REQUESTED" == /root/.ssh/authorized_keys &&
  -L /root/.ssh ]]; then
  # Unraid persists root SSH configuration on the flash device and exposes it
  # through one fixed symlink. Accept only that exact root-owned platform
  # layout, with the restrictive masks that make directories 0700 and files
  # 0600. Every other symlinked authorized_keys ancestry remains forbidden.
  UNRAID_SSH_DIR=/root/.ssh
  UNRAID_SSH_TARGET=/boot/config/ssh/root
  UNRAID_AUTHORIZED_KEYS="$UNRAID_SSH_TARGET/authorized_keys"
  AUTHORIZED_KEYS=$UNRAID_AUTHORIZED_KEYS
  AUTHORIZED_KEYS_DIR=$UNRAID_SSH_TARGET
  AUTHORIZED_KEYS_LAYOUT=unraid
  validate_unraid_authorized_keys_layout || {
    echo "authorized_keys symlink layout is not the supported Unraid persistence path" >&2
    exit 78
  }
  (
    AUTHORIZED_KEYS_PROBE=$(mktemp "$AUTHORIZED_KEYS_DIR/.nice-assistant-auth.XXXXXX")
    AUTHORIZED_KEYS_PROBE_NEXT="${AUTHORIZED_KEYS_PROBE}.next"
    trap 'rm -f -- "$AUTHORIZED_KEYS_PROBE" "$AUTHORIZED_KEYS_PROBE_NEXT"' EXIT
    printf 'probe\n' >"$AUTHORIZED_KEYS_PROBE"
    printf 'replaced\n' >"$AUTHORIZED_KEYS_PROBE_NEXT"
    chown root:root "$AUTHORIZED_KEYS_PROBE"
    chown root:root "$AUTHORIZED_KEYS_PROBE_NEXT"
    chmod 0600 "$AUTHORIZED_KEYS_PROBE"
    chmod 0600 "$AUTHORIZED_KEYS_PROBE_NEXT"
    mv -fT -- "$AUTHORIZED_KEYS_PROBE" "$AUTHORIZED_KEYS_PROBE_NEXT"
    AUTHORIZED_KEYS_PROBE_DEVICE=$(stat -c '%d' "$AUTHORIZED_KEYS_PROBE_NEXT")
    AUTHORIZED_KEYS_DIR_DEVICE=$(stat -c '%d' "$AUTHORIZED_KEYS_DIR")
    [[ -f "$AUTHORIZED_KEYS_PROBE_NEXT" &&
      ! -L "$AUTHORIZED_KEYS_PROBE_NEXT" &&
      $(stat -c '%u:%g' "$AUTHORIZED_KEYS_PROBE_NEXT") == 0:0 &&
      $(stat -c '%a' "$AUTHORIZED_KEYS_PROBE_NEXT") == 600 &&
      $(stat -c '%h' "$AUTHORIZED_KEYS_PROBE_NEXT") == 1 &&
      "$AUTHORIZED_KEYS_PROBE_DEVICE" == "$AUTHORIZED_KEYS_DIR_DEVICE" &&
      $(<"$AUTHORIZED_KEYS_PROBE_NEXT") == probe ]]
  ) || {
    echo "the Unraid SSH persistence filesystem contract failed" >&2
    exit 78
  }
else
  validate_generic_authorized_keys_layout ||
    { echo "authorized_keys path is insecure" >&2; exit 78; }
fi

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
AUTHORIZED_KEYS_RECOVERY="$AUTHORIZED_KEYS_DIR/.authorized_keys.nice-assistant.recovery"
AUTHORIZED_KEYS_RECOVERY_PREPARED=false
AUTHORIZED_KEYS_EXPECTED_EXISTS=false
AUTHORIZED_KEYS_EXPECTED_SHA256=
AUTHORIZED_KEYS_STAGED_SHA256=
AUTHORIZED_KEYS_SWITCHED=false

restore_authorized_keys_after_failure() {
  local recovery_hash='' current_hash='' restored_hash='' restore_candidate=''
  local recovery_device='' directory_device=''
  if [[ "$AUTHORIZED_KEYS_EXPECTED_EXISTS" == true ]]; then
    recovery_device=$(stat -c '%d' "$AUTHORIZED_KEYS_RECOVERY") || return 1
    directory_device=$(stat -c '%d' "$AUTHORIZED_KEYS_DIR") || return 1
    if ! secure_authorized_keys_file "$AUTHORIZED_KEYS_RECOVERY" ||
      [[ "$recovery_device" != "$directory_device" ]]; then
      echo "authorized_keys recovery is insecure; use the still-open administrative session" >&2
      return 1
    fi
    recovery_hash=$(sha256sum "$AUTHORIZED_KEYS_RECOVERY" | awk '{print $1}') ||
      return 1
    if [[ -z "$AUTHORIZED_KEYS_EXPECTED_SHA256" ||
      "$recovery_hash" != "$AUTHORIZED_KEYS_EXPECTED_SHA256" ]]; then
      echo "authorized_keys recovery hash changed; use the still-open administrative session" >&2
      return 1
    fi
    if [[ -e "$AUTHORIZED_KEYS" || -L "$AUTHORIZED_KEYS" ]]; then
      validate_effective_authorized_keys_layout &&
        secure_authorized_keys_file "$AUTHORIZED_KEYS" || {
        echo "authorized_keys state is ambiguous; use the still-open administrative session" >&2
        return 1
      }
      current_hash=$(sha256sum "$AUTHORIZED_KEYS" | awk '{print $1}') ||
        return 1
      if [[ "$current_hash" == "$AUTHORIZED_KEYS_EXPECTED_SHA256" ]]; then
        rm -f -- "$AUTHORIZED_KEYS_RECOVERY" || return 1
        sync || return 1
        AUTHORIZED_KEYS_SWITCHED=false
        AUTHORIZED_KEYS_RECOVERY_PREPARED=false
        printf '%s\n' \
          "the previous authorized_keys file remained active after an enrollment failure" >&2 ||
          true
        return 0
      fi
      if [[ -z "$AUTHORIZED_KEYS_STAGED_SHA256" ||
        "$current_hash" != "$AUTHORIZED_KEYS_STAGED_SHA256" ]]; then
        echo "authorized_keys changed after enrollment; recovery was preserved for the administrative session" >&2
        return 1
      fi
    else
      validate_effective_authorized_keys_layout || {
        echo "authorized_keys layout changed; use the still-open administrative session" >&2
        return 1
      }
    fi
    restore_candidate=$(mktemp "$AUTHORIZED_KEYS_DIR/.authorized_keys.nice-assistant.restore.XXXXXX") ||
      return 1
    install -o root -g root -m 0600 \
      "$AUTHORIZED_KEYS_RECOVERY" "$restore_candidate" || {
      rm -f -- "$restore_candidate"
      return 1
    }
    recovery_device=$(stat -c '%d' "$restore_candidate") || {
      rm -f -- "$restore_candidate"
      return 1
    }
    recovery_hash=$(sha256sum "$restore_candidate" | awk '{print $1}') || {
      rm -f -- "$restore_candidate"
      return 1
    }
    secure_authorized_keys_file "$restore_candidate" &&
      [[ "$recovery_device" == "$directory_device" &&
        "$recovery_hash" == "$AUTHORIZED_KEYS_EXPECTED_SHA256" ]] || {
      rm -f -- "$restore_candidate"
      echo "the authorized_keys restore candidate is insecure" >&2
      return 1
    }
    mv -fT -- "$restore_candidate" "$AUTHORIZED_KEYS" || {
      rm -f -- "$restore_candidate"
      echo "authorized_keys recovery rename failed; use the still-open administrative session" >&2
      return 1
    }
    restore_candidate=
    sync || {
      echo "restored authorized_keys did not flush; keep the administrative session open" >&2
      return 1
    }
    validate_effective_authorized_keys_layout &&
      secure_authorized_keys_file "$AUTHORIZED_KEYS" || {
      echo "restored authorized_keys metadata did not verify; keep the administrative session open" >&2
      return 1
    }
    restored_hash=$(sha256sum "$AUTHORIZED_KEYS" | awk '{print $1}') ||
      return 1
    [[ "$restored_hash" == "$AUTHORIZED_KEYS_EXPECTED_SHA256" ]] || {
      echo "restored authorized_keys content did not verify; keep the administrative session open" >&2
      return 1
    }
    rm -f -- "$AUTHORIZED_KEYS_RECOVERY" || return 1
    sync || return 1
    AUTHORIZED_KEYS_SWITCHED=false
    AUTHORIZED_KEYS_RECOVERY_PREPARED=false
    printf '%s\n' \
      "the previous authorized_keys file was restored after an enrollment failure" >&2 ||
      true
    return 0
  fi

  if [[ -e "$AUTHORIZED_KEYS" || -L "$AUTHORIZED_KEYS" ]]; then
    validate_effective_authorized_keys_layout &&
      secure_authorized_keys_file "$AUTHORIZED_KEYS" || {
      echo "new authorized_keys state is ambiguous; use the still-open administrative session" >&2
      return 1
    }
    current_hash=$(sha256sum "$AUTHORIZED_KEYS" | awk '{print $1}') ||
      return 1
    [[ -n "$AUTHORIZED_KEYS_STAGED_SHA256" &&
      "$current_hash" == "$AUTHORIZED_KEYS_STAGED_SHA256" ]] || {
      echo "new authorized_keys content is ambiguous; use the still-open administrative session" >&2
      return 1
    }
    rm -f -- "$AUTHORIZED_KEYS" || return 1
    sync || return 1
  fi
  validate_effective_authorized_keys_layout || {
    echo "authorized_keys absence recovery did not verify; keep the administrative session open" >&2
    return 1
  }
  rm -f -- "$AUTHORIZED_KEYS_RECOVERY" || return 1
  sync || return 1
  AUTHORIZED_KEYS_SWITCHED=false
  AUTHORIZED_KEYS_RECOVERY_PREPARED=false
  printf '%s\n' \
    "the newly created authorized_keys file was removed after an enrollment failure" >&2 ||
    true
}

cleanup() {
  local recovery_status=0
  trap - EXIT HUP INT TERM
  if [[ "$AUTHORIZED_KEYS_SWITCHED" == true ]]; then
    set +e
    restore_authorized_keys_after_failure
    recovery_status=$?
    set -e
    if ((recovery_status != 0)); then
      echo "automatic authorized_keys recovery was not completed; keep the administrative session open" >&2
    fi
  elif [[ "$AUTHORIZED_KEYS_RECOVERY_PREPARED" == true ]]; then
    rm -f -- "$AUTHORIZED_KEYS_RECOVERY" ||
      echo "the unused authorized_keys recovery could not be removed" >&2
  fi
  rm -f -- "$CONFIG_NEXT" "$LAUNCHER_NEXT" "$PUBLIC_KEY_STAGED" ||
    echo "launcher staging cleanup was incomplete" >&2
  if [[ -n "$AUTHORIZED_KEYS_NEXT" ]]; then
    rm -f -- "$AUTHORIZED_KEYS_NEXT" ||
      echo "authorized_keys staging cleanup was incomplete" >&2
  fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

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
validate_effective_authorized_keys_layout ||
  { echo "authorized_keys layout changed before staging" >&2; exit 78; }

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
AUTHORIZED_KEYS_INPUT=/dev/null
if [[ -e "$AUTHORIZED_KEYS" || -L "$AUTHORIZED_KEYS" ]]; then
  secure_authorized_keys_file "$AUTHORIZED_KEYS" || {
    echo "authorized_keys is insecure" >&2
    exit 78
  }
  AUTHORIZED_KEYS_EXPECTED_EXISTS=true
  AUTHORIZED_KEYS_INPUT=$AUTHORIZED_KEYS
  AUTHORIZED_KEYS_EXPECTED_SHA256=$(sha256sum "$AUTHORIZED_KEYS" | awk '{print $1}')
fi
if [[ -e "$AUTHORIZED_KEYS_RECOVERY" || -L "$AUTHORIZED_KEYS_RECOVERY" ]]; then
  echo "a pending authorized_keys enrollment recovery must be resolved first" >&2
  exit 75
fi
if [[ "$AUTHORIZED_KEYS_EXPECTED_EXISTS" == true ]]; then
  install -o root -g root -m 0600 "$AUTHORIZED_KEYS" "$AUTHORIZED_KEYS_RECOVERY"
else
  : >"$AUTHORIZED_KEYS_RECOVERY"
  chown root:root "$AUTHORIZED_KEYS_RECOVERY"
  chmod 0600 "$AUTHORIZED_KEYS_RECOVERY"
fi
AUTHORIZED_KEYS_RECOVERY_PREPARED=true
AUTHORIZED_KEYS_RECOVERY_DEVICE=$(stat -c '%d' "$AUTHORIZED_KEYS_RECOVERY")
AUTHORIZED_KEYS_DIR_DEVICE=$(stat -c '%d' "$AUTHORIZED_KEYS_DIR")
[[ $(stat -c '%u:%g' "$AUTHORIZED_KEYS_RECOVERY") == 0:0 &&
  $(stat -c '%a' "$AUTHORIZED_KEYS_RECOVERY") == 600 &&
  $(stat -c '%h' "$AUTHORIZED_KEYS_RECOVERY") == 1 &&
  "$AUTHORIZED_KEYS_RECOVERY_DEVICE" == "$AUTHORIZED_KEYS_DIR_DEVICE" ]] || {
  echo "the authorized_keys enrollment recovery is insecure" >&2
  exit 78
}
if [[ "$AUTHORIZED_KEYS_EXPECTED_EXISTS" == true ]]; then
  AUTHORIZED_KEYS_RECOVERY_SHA256=$(
    sha256sum "$AUTHORIZED_KEYS_RECOVERY" | awk '{print $1}'
  )
  [[ "$AUTHORIZED_KEYS_RECOVERY_SHA256" == "$AUTHORIZED_KEYS_EXPECTED_SHA256" ]] || {
    echo "the authorized_keys enrollment recovery did not verify" >&2
    exit 78
  }
else
  [[ ! -s "$AUTHORIZED_KEYS_RECOVERY" ]] || {
    echo "the authorized_keys absence recovery did not verify" >&2
    exit 78
  }
fi
AUTHORIZED_KEYS_UNMANAGED_SHA256=$(
  awk -v marker="$MARKER" \
    '{ field = $NF; sub(/\r$/, "", field); if (NF == 0 || field != marker) print }' \
    "$AUTHORIZED_KEYS_INPUT" |
    sha256sum | awk '{print $1}'
)
AUTHORIZED_KEYS_NEXT=$(mktemp "$AUTHORIZED_KEYS_DIR/.authorized_keys.nice-assistant.XXXXXX")
awk -v marker="$MARKER" \
  '{ field = $NF; sub(/\r$/, "", field); if (NF == 0 || field != marker) print }' \
  "$AUTHORIZED_KEYS_INPUT" >"$AUTHORIZED_KEYS_NEXT"
FORCED_COMMAND='/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C SSH_ORIGINAL_COMMAND="$SSH_ORIGINAL_COMMAND" '"$INSTALL_DIR/nice-assistant-deploy-guard"
ESCAPED_FORCED_COMMAND=${FORCED_COMMAND//\"/\\\"}
printf 'restrict,from="%s",command="%s" %s %s\n' \
  "$SOURCE" "$ESCAPED_FORCED_COMMAND" "$KEY" "$MARKER" \
  >>"$AUTHORIZED_KEYS_NEXT"
chown root:root "$AUTHORIZED_KEYS_NEXT"
chmod 0600 "$AUTHORIZED_KEYS_NEXT"
AUTHORIZED_KEYS_NEXT_DEVICE=$(stat -c '%d' "$AUTHORIZED_KEYS_NEXT")
AUTHORIZED_KEYS_DIR_DEVICE=$(stat -c '%d' "$AUTHORIZED_KEYS_DIR")
[[ $(stat -c '%u:%g' "$AUTHORIZED_KEYS_NEXT") == 0:0 &&
  $(stat -c '%a' "$AUTHORIZED_KEYS_NEXT") == 600 &&
  $(stat -c '%h' "$AUTHORIZED_KEYS_NEXT") == 1 &&
  "$AUTHORIZED_KEYS_NEXT_DEVICE" == "$AUTHORIZED_KEYS_DIR_DEVICE" &&
  $(awk -v marker="$MARKER" \
    '{ field = $NF; sub(/\r$/, "", field); if (field == marker) count++ }
      END { print count + 0 }' \
    "$AUTHORIZED_KEYS_NEXT") == 1 ]] || {
  echo "the staged authorized_keys file is insecure" >&2
  exit 78
}
AUTHORIZED_KEYS_STAGED_SHA256=$(
  sha256sum "$AUTHORIZED_KEYS_NEXT" | awk '{print $1}'
)
[[ $(
  awk -v marker="$MARKER" \
    '{ field = $NF; sub(/\r$/, "", field); if (NF == 0 || field != marker) print }' \
    "$AUTHORIZED_KEYS_NEXT" |
    sha256sum | awk '{print $1}'
) == "$AUTHORIZED_KEYS_UNMANAGED_SHA256" ]] || {
  echo "the staged authorized_keys file changed unrelated entries" >&2
  exit 78
}

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

validate_effective_authorized_keys_layout || {
  echo "authorized_keys layout changed before authorization" >&2
  exit 78
}
if [[ "$AUTHORIZED_KEYS_EXPECTED_EXISTS" == true ]]; then
  AUTHORIZED_KEYS_CURRENT_SHA256=$(
    sha256sum "$AUTHORIZED_KEYS" | awk '{print $1}'
  )
  [[ -e "$AUTHORIZED_KEYS" &&
    "$AUTHORIZED_KEYS_CURRENT_SHA256" == "$AUTHORIZED_KEYS_EXPECTED_SHA256" ]] || {
    echo "authorized_keys changed concurrently; authorization was not replaced" >&2
    exit 75
  }
else
  [[ ! -e "$AUTHORIZED_KEYS" && ! -L "$AUTHORIZED_KEYS" ]] || {
    echo "authorized_keys appeared concurrently; authorization was not replaced" >&2
    exit 75
  }
fi
sync
AUTHORIZED_KEYS_SWITCHED=true
mv -fT -- "$AUTHORIZED_KEYS_NEXT" "$AUTHORIZED_KEYS"
AUTHORIZED_KEYS_NEXT=
sync
AUTHORIZED_KEYS_FINAL_SHA256=$(
  sha256sum "$AUTHORIZED_KEYS" | awk '{print $1}'
)
AUTHORIZED_KEYS_FINAL_UNMANAGED_SHA256=$(
  awk -v marker="$MARKER" \
    '{ field = $NF; sub(/\r$/, "", field); if (NF == 0 || field != marker) print }' \
    "$AUTHORIZED_KEYS" |
    sha256sum | awk '{print $1}'
)
validate_effective_authorized_keys_layout &&
  [[ "$AUTHORIZED_KEYS_FINAL_SHA256" == "$AUTHORIZED_KEYS_STAGED_SHA256" &&
    $(awk -v marker="$MARKER" \
      '{ field = $NF; sub(/\r$/, "", field); if (field == marker) count++ }
        END { print count + 0 }' \
      "$AUTHORIZED_KEYS") == 1 &&
    "$AUTHORIZED_KEYS_FINAL_UNMANAGED_SHA256" == "$AUTHORIZED_KEYS_UNMANAGED_SHA256" ]] || {
  echo "authorized_keys replacement did not verify" >&2
  exit 78
}
rm -f -- "$INSTALL_JOURNAL" "$INSTALL_JOURNAL_NEXT" \
  "$CONFIG_BACKUP" "$CONFIG_BACKUP_NEXT" "$PUBLIC_KEY_STAGED"
# From this point the transaction is accepted. Ignore a signal only across the
# non-fallible in-memory commit and trap removal so a half-committed enrollment
# cannot escape rollback.
trap '' HUP INT TERM
AUTHORIZED_KEYS_SWITCHED=false
AUTHORIZED_KEYS_RECOVERY_PREPARED=false
trap - EXIT HUP INT TERM
printf '%s\n' \
  "Nice Assistant permanent deployment launcher installed and definition-checked." ||
  true
printf '%s\n' \
  "Keep the administrative session open; remove the root-only enrollment recovery only after the replacement key passes remote acceptance." ||
  true
