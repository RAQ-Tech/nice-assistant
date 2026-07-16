#!/usr/bin/env bash
# Install the forced-command guard after an operator has opened a root session.

set -Eeuo pipefail
umask 077

usage() {
  echo "usage: $0 --container NAME --image-prefix ghcr.io/OWNER/nice-assistant --public-key FILE --source CIDR --state-dir DIR --authorized-keys FILE [--unraid-template FILE]" >&2
  exit 64
}

CONTAINER_NAME=
IMAGE_PREFIX=
PUBLIC_KEY=
SOURCE=
STATE_DIR=
AUTHORIZED_KEYS=
UNRAID_TEMPLATE=
while (($#)); do
  case "$1" in
    --container) CONTAINER_NAME=${2:-}; shift 2 ;;
    --image-prefix) IMAGE_PREFIX=${2:-}; shift 2 ;;
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
[[ -f "$PUBLIC_KEY" && -n "$SOURCE" && -n "$STATE_DIR" && -n "$AUTHORIZED_KEYS" ]] || usage
[[ "$SOURCE" =~ ^[A-Fa-f0-9:.,/]+$ ]] || usage
[[ "$STATE_DIR" =~ ^/[A-Za-z0-9_./-]+$ && "$STATE_DIR" != / ]] || usage
[[ "$AUTHORIZED_KEYS" =~ ^/[A-Za-z0-9_./-]+$ ]] || usage
[[ -z "$UNRAID_TEMPLATE" || "$UNRAID_TEMPLATE" =~ ^/[A-Za-z0-9_./-]+$ ]] || usage
for command in docker curl jq flock sed stat install chown cp grep ssh-keygen; do
  command -v "$command" >/dev/null || { echo "$command is required" >&2; exit 69; }
done
docker container inspect "$CONTAINER_NAME" >/dev/null
ssh-keygen -l -f "$PUBLIC_KEY" >/dev/null 2>&1 || { echo "the public key is invalid" >&2; exit 65; }
DOCKER_BIN=$(command -v docker)
CURL_BIN=$(command -v curl)
JQ_BIN=$(command -v jq)

INSTALL_DIR="$STATE_DIR/bin"
mkdir -p "$INSTALL_DIR"
chmod 700 "$STATE_DIR" "$INSTALL_DIR"
install -o root -g root -m 0700 "$(dirname "$0")/nice_assistant_deploy_guard.sh" "$INSTALL_DIR/nice-assistant-deploy-guard"
install -o root -g root -m 0600 "$(dirname "$0")/create_container_payload.jq" "$INSTALL_DIR/create_container_payload.jq"
install -o root -g root -m 0600 "$(dirname "$0")/normalize_container_config.jq" "$INSTALL_DIR/normalize_container_config.jq"
cat >"$INSTALL_DIR/guard.conf" <<EOF
NICE_CONTAINER_NAME='$CONTAINER_NAME'
NICE_APPROVED_IMAGE_PREFIX='$IMAGE_PREFIX'
NICE_DEPLOY_STATE_DIR='$STATE_DIR/state'
NICE_UNRAID_TEMPLATE='$UNRAID_TEMPLATE'
NICE_DEPLOY_DOCKER_BIN='$DOCKER_BIN'
NICE_DEPLOY_CURL_BIN='$CURL_BIN'
NICE_DEPLOY_JQ_BIN='$JQ_BIN'
EOF
chown root:root "$INSTALL_DIR/guard.conf"
chmod 0600 "$INSTALL_DIR/guard.conf"

if [[ -n "$UNRAID_TEMPLATE" ]]; then
  [[ -f "$UNRAID_TEMPLATE" ]] || { echo "Unraid template was not found" >&2; exit 66; }
  cp -p "$UNRAID_TEMPLATE" "$STATE_DIR/unraid-template.original.xml"
  chmod 0600 "$STATE_DIR/unraid-template.original.xml"
fi

NICE_DEPLOY_GUARD_CONFIG="$INSTALL_DIR/guard.conf" "$INSTALL_DIR/nice-assistant-deploy-guard" validate-definition >/dev/null

mkdir -p "$(dirname "$AUTHORIZED_KEYS")"
touch "$AUTHORIZED_KEYS"
chown root:root "$AUTHORIZED_KEYS"
chmod 0600 "$AUTHORIZED_KEYS"
KEY=$(tr -d '\r\n' <"$PUBLIC_KEY")
[[ "$KEY" =~ ^ssh-ed25519\  ]] || { echo "an ed25519 public key is required" >&2; exit 65; }
MARKER="nice-assistant-deploy-guard"
temporary="$STATE_DIR/authorized_keys.tmp"
grep -v "$MARKER" "$AUTHORIZED_KEYS" >"$temporary" || true
printf 'restrict,from="%s",command="%s" %s %s\n' "$SOURCE" "$INSTALL_DIR/nice-assistant-deploy-guard" "$KEY" "$MARKER" >>"$temporary"
cat "$temporary" >"$AUTHORIZED_KEYS"
rm -f "$temporary"
chmod 0600 "$AUTHORIZED_KEYS"

echo "Nice Assistant deployment guard installed and definition-checked."
