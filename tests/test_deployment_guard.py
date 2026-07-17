import os
from pathlib import Path
import shutil
import subprocess
import json
import hashlib
import tempfile
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "deployment" / "nice_assistant_deploy_guard.sh"
LAUNCHER = ROOT / "scripts" / "deployment" / "nice_assistant_deploy_launcher.sh"
INSTALLER = ROOT / "scripts" / "deployment" / "install_unraid_deploy_guard.sh"
REMOTE = ROOT / "scripts" / "deployment" / "invoke_guarded_deploy.ps1"
KEYGEN = ROOT / "scripts" / "deployment" / "new_laptop_deploy_key.ps1"
CREATE_PAYLOAD_FILTER = ROOT / "scripts" / "deployment" / "create_container_payload.jq"
NORMALIZE_CONFIG_FILTER = ROOT / "scripts" / "deployment" / "normalize_container_config.jq"
GUARD_BUNDLE_MANIFEST = ROOT / "scripts" / "deployment" / "guard_bundle_manifest.json"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def bash_executable() -> str | None:
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash")


def root_command_prefix() -> list[str] | None:
    if os.name != "posix":
        return None
    if os.geteuid() == 0:
        return []
    sudo = shutil.which("sudo")
    if not sudo:
        return None
    available = subprocess.run(
        [sudo, "-n", "true"],
        check=False,
        capture_output=True,
        text=True,
    )
    return [sudo, "-n"] if available.returncode == 0 else None


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DeploymentGuardContractTests(unittest.TestCase):
    def test_shell_entrypoints_are_syntax_valid(self):
        bash = bash_executable()
        if not bash:
            self.skipTest("bash is unavailable")
        for script in (GUARD, LAUNCHER, INSTALLER):
            with self.subTest(script=script.name):
                subprocess.run([bash, "-n", str(script)], cwd=ROOT, check=True)

    def test_forced_command_exposes_only_bounded_nice_assistant_actions(self):
        guard = GUARD.read_text(encoding="utf-8")
        self.assertIn("inspect | backup | health | logs | rollback)", guard)
        self.assertIn("deploy requires one immutable image digest", guard)
        self.assertIn("[[ -z ${SSH_ORIGINAL_COMMAND:-}", guard)
        self.assertIn('*) die "deployment command is not allowed" 64', guard)
        self.assertIn('CONFIG_FILE="$SCRIPT_DIR/guard.conf"', guard)
        self.assertNotIn("docker compose", guard.lower())
        self.assertNotIn("eval ", guard)

    def test_permanent_launcher_owns_updates_and_delegates_a_sanitized_allowlist(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("inspect | backup | health | logs | rollback | rollback-guard)", launcher)
        self.assertIn("deploy\\ * | update-guard\\ *", launcher)
        self.assertIn("bootstrap-guard)", launcher)
        self.assertIn('*) die "deployment command is not allowed" 64', launcher)
        self.assertIn("ACTION=${original%% *}", launcher)
        self.assertIn('"$DIGEST" != *[[:space:]]*', launcher)
        self.assertIn('update-guard) install_guard_bundle "$DIGEST" false', launcher)
        self.assertIn('bootstrap-guard) install_guard_bundle "$DIGEST" true', launcher)
        self.assertIn("exec /usr/bin/env -i", launcher)
        self.assertIn("NICE_DEPLOY_LAUNCHER_LOCKED=1", launcher)
        self.assertIn("PATH=/usr/sbin:/usr/bin:/sbin:/bin", launcher)
        self.assertIn("unset BASH_ENV ENV CDPATH GLOBIGNORE", launcher)
        self.assertIn('exec 9>"$LOCK_FILE"', launcher)
        self.assertIn("flock -n 9", launcher)
        self.assertIn("--arg guard_label", launcher)
        self.assertIn('"com.nice-assistant.guard-update":$guard_label', launcher)
        self.assertNotIn("--arg label", launcher)
        self.assertIn("/proc/self/fd/9", GUARD.read_text(encoding="utf-8"))
        self.assertNotIn("eval ", launcher)
        self.assertNotIn("docker compose", launcher.lower())

    def test_guard_update_requires_the_exact_running_approved_digest(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('[[ "$digest" == "${NICE_APPROVED_IMAGE_PREFIX}@sha256:"* ]]', launcher)
        self.assertIn('[[ "$suffix" =~ ^[0-9a-f]{64}$ ]]', launcher)
        self.assertIn("running_digest=$(current_repo_digest)", launcher)
        self.assertIn('[[ "$digest" == "$running_digest" ]]', launcher)
        self.assertIn("guard updates require the exact running Nice Assistant digest", launcher)
        self.assertIn('if length == 1 then .[0] else error("ambiguous digest") end', launcher)
        self.assertIn("configured_image=", launcher)
        self.assertIn("resolved_id=", launcher)
        self.assertIn('[[ "$resolved_id" == "$image_id" ]]', launcher)
        self.assertIn('org.opencontainers.image.revision"]', launcher)
        self.assertIn('org.opencontainers.image.source"]', launcher)
        self.assertIn('[[ "${source_label,,}" == "$EXPECTED_SOURCE" ]]', launcher)
        self.assertIn("((.[0].Config.Volumes // {}) | length) == 0", launcher)

    def test_guard_update_extracts_only_a_bounded_manifest_bundle_without_running_it(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("--network none", launcher)
        self.assertIn("--read-only", launcher)
        self.assertIn("--cap-drop ALL", launcher)
        self.assertIn("--security-opt no-new-privileges", launcher)
        self.assertIn("--entrypoint /bin/false", launcher)
        for file_name in (
            "guard_bundle_manifest.json",
            "nice_assistant_deploy_guard.sh",
            "create_container_payload.jq",
            "normalize_container_config.jq",
        ):
            self.assertIn(
                f'"$extract:/opt/nice-assistant/scripts/deployment/{file_name}"',
                launcher,
            )
        update_start = launcher.index("install_guard_bundle() {")
        update_end = launcher.index("rollback_guard_bundle() {", update_start)
        update = launcher[update_start:update_end]
        self.assertNotIn('"$DOCKER" start', update)
        self.assertNotIn('"$DOCKER" exec', update)
        self.assertNotIn('"$DOCKER" run', update)
        self.assertIn("MAX_GUARD_BYTES=", launcher)
        self.assertIn("MAX_FILTER_BYTES=", launcher)
        self.assertIn("MAX_MANIFEST_BYTES=", launcher)
        self.assertIn("[[ $(stat -c '%h' \"$path\") == 1 ]]", launcher)
        self.assertIn('[[ $(stat -c \'%a\' "$path") == "$mode" ]]', launcher)
        self.assertIn(
            'safe_copied_file "$raw/nice_assistant_deploy_guard.sh" "$MAX_GUARD_BYTES" 700',
            launcher,
        )
        self.assertIn(
            'safe_copied_file "$raw/create_container_payload.jq" "$MAX_FILTER_BYTES" 600',
            launcher,
        )
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("chmod 0700 scripts/deployment/nice_assistant_deploy_guard.sh", dockerfile)
        self.assertIn("chmod 0600 scripts/deployment/create_container_payload.jq", dockerfile)
        self.assertIn("scripts/deployment/guard_bundle_manifest.json", dockerfile)

    def test_guard_manifest_is_strict_versioned_and_matches_repository_files(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        manifest = json.loads(GUARD_BUNDLE_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest),
            {"schema_version", "launcher_protocol_version", "bundle_version", "files"},
        )
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["launcher_protocol_version"], 1)
        self.assertEqual(manifest["bundle_version"], 3)
        expected_modes = {
            "nice_assistant_deploy_guard.sh": "0700",
            "create_container_payload.jq": "0600",
            "normalize_container_config.jq": "0600",
        }
        self.assertEqual(set(manifest["files"]), set(expected_modes))
        for file_name, expected_mode in expected_modes.items():
            path = ROOT / "scripts" / "deployment" / file_name
            with self.subTest(file=file_name):
                self.assertEqual(manifest["files"][file_name]["mode"], expected_mode)
                self.assertEqual(
                    manifest["files"][file_name]["sha256"],
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
        self.assertIn(
            '(keys == ["bundle_version","files","launcher_protocol_version","schema_version"])',
            launcher,
        )
        self.assertIn("equal-version deployment guard bundles must be identical", launcher)
        self.assertIn("deployment guard bundle downgrade is not allowed", launcher)
        self.assertIn(".bundle_version <= 2147483647", launcher)
        self.assertIn('secure_regular_file "$directory/$file"', launcher)
        self.assertIn("[[ $(stat -c '%h' \"$manifest\") == 1 ]]", launcher)

    def test_guard_update_uses_independent_payload_and_normalization_checks(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        normalizer = NORMALIZE_CONFIG_FILTER.read_text(encoding="utf-8")
        self.assertIn("EXPECTED_PAYLOAD_FILTER=", launcher)
        self.assertIn('candidate_serialized=$("$JQ" -cS . "$candidate_payload")', launcher)
        self.assertIn('expected_serialized=$("$JQ" -cS . "$expected_payload")', launcher)
        self.assertIn('[[ "$candidate_serialized" == "$expected_serialized" ]]', launcher)
        self.assertIn("CANONICAL_FILTER=", launcher)
        self.assertIn('--arg managed_name "$NICE_CONTAINER_NAME"', launcher)
        self.assertIn('"$candidate_before" == "$candidate_after"', launcher)
        self.assertIn('"$candidate_before" == "$before"', launcher)
        self.assertIn('del(.["com.nice-assistant.guard-update"])', normalizer)
        self.assertIn("and . != $managed_name", normalizer)

    def test_guard_update_journal_and_activation_are_exact_and_atomic(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("guard-update.json", launcher)
        self.assertIn("write_update_journal", launcher)
        self.assertIn("cleanup_update_artifacts", launcher)
        self.assertIn("remove_helper", launcher)
        self.assertIn("com.nice-assistant.guard-update", launcher)
        self.assertIn('[[ "$staging" == "$BUNDLE_ROOT/.guard-update-${hex}."* ]]', launcher)
        self.assertIn('[[ "${staging#"$BUNDLE_ROOT/"}" != */*', launcher)
        self.assertNotIn("container prune", launcher.lower())
        self.assertNotIn("system prune", launcher.lower())
        self.assertIn('mv -Tf -- "$current_next" "$CURRENT_LINK"', launcher)
        self.assertIn('mv -Tf -- "$previous_next" "$PREVIOUS_LINK"', launcher)
        self.assertIn('current_next="$BUNDLE_ROOT/.current.next"', launcher)
        self.assertIn('previous_next="$BUNDLE_ROOT/.previous.next"', launcher)
        self.assertIn("cleanup_pointer_temps", launcher)
        self.assertIn('[[ "$has_current_next" == true && "$has_previous_next" == true ]]', launcher)
        self.assertIn('[[ -n "$previous_target" && "$current_target" == "$previous_target" ]]', launcher)
        self.assertIn('mv -Tf -- "$current_next" "$CURRENT_LINK"', launcher)

    def test_deploy_requires_exact_approved_digest_and_preserves_effective_config(self):
        guard = GUARD.read_text(encoding="utf-8")
        payload_filter = CREATE_PAYLOAD_FILTER.read_text(encoding="utf-8")
        self.assertIn('[[ "$digest" == "${NICE_APPROVED_IMAGE_PREFIX}@sha256:"* ]]', guard)
        self.assertIn('[[ "$suffix" =~ ^[0-9a-f]{64}$ ]]', guard)
        self.assertIn("HostConfig: $container.HostConfig", payload_filter)
        self.assertIn("EndpointsConfig", payload_filter)
        self.assertIn("IPAMConfig: (.IPAMConfig // null)", payload_filter)
        self.assertIn("if $preserve_explicit_mac", payload_filter)
        self.assertIn('then ((.MacAddress // "")', payload_filter)
        self.assertIn("del(.MacAddress)", payload_filter)
        self.assertIn("GwPriority: ((.GwPriority // 0)", payload_filter)
        self.assertNotIn("IPv4Address: (.IPAddress", payload_filter)
        self.assertIn('startswith("org.opencontainers.image.")', payload_filter)
        self.assertIn("recreated container definition did not match", guard)
        self.assertIn('current_repo_digest "$NICE_CONTAINER_NAME"', guard)

    def test_jq_filters_preserve_config_but_ignore_runtime_network_assignments(self):
        jq = shutil.which("jq")
        if not jq:
            self.skipTest("jq is unavailable")
        current_id = "a" * 64
        replacement_id = "b" * 64
        host_config = {
            "Binds": ["/srv/data:/data"],
            "PortBindings": {"3000/tcp": [{"HostPort": "3010"}]},
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "NetworkMode": "private",
        }

        def definition(container_id, name, revision, address, mac, gateway_priority):
            return [
                {
                    "Id": container_id,
                    "Name": f"/{name}",
                    "Config": {
                        "Hostname": container_id[:12],
                        "Image": "old-image",
                        "Env": ["EXAMPLE=value"],
                        "Labels": {"keep": "yes", "org.opencontainers.image.revision": revision},
                    },
                    "HostConfig": dict(host_config),
                    "NetworkSettings": {
                        "Networks": {
                            "private": {
                                "Aliases": [name, container_id[:12], "stable-alias"],
                                "Links": None,
                                "DriverOpts": None,
                                "IPAMConfig": None,
                                "IPAddress": address,
                                "MacAddress": mac,
                                "GwPriority": gateway_priority,
                            }
                        }
                    },
                }
            ]

        current = definition(
            current_id,
            "nice-assistant",
            "old",
            "assigned-address-one",
            "02:42:ac:11:00:08",
            10,
        )
        replacement = definition(
            replacement_id,
            "nice-assistant",
            "new",
            "assigned-address-two",
            "02:42:ac:11:00:08",
            10,
        )
        current[0]["HostConfig"]["OomKillDisable"] = None
        replacement[0]["HostConfig"]["OomKillDisable"] = False
        replacement[0]["Config"]["MacAddress"] = "02:42:ac:11:00:08"
        payload = subprocess.run(
            [
                jq,
                "--arg",
                "image",
                "ghcr.io/owner/nice-assistant@sha256:" + ("c" * 64),
                "--argjson",
                "image_labels",
                json.dumps({"org.opencontainers.image.revision": "new", "ignored": "value"}),
                "--argjson",
                "preserve_explicit_mac",
                "true",
                "-f",
                str(CREATE_PAYLOAD_FILTER),
            ],
            input=json.dumps(current),
            text=True,
            capture_output=True,
            check=True,
        )
        created = json.loads(payload.stdout)
        self.assertEqual(created["HostConfig"], current[0]["HostConfig"])
        self.assertEqual(created["Labels"]["keep"], "yes")
        self.assertEqual(created["Labels"]["org.opencontainers.image.revision"], "new")
        self.assertNotIn("ignored", created["Labels"])
        self.assertNotIn("MacAddress", created)
        endpoint = created["NetworkingConfig"]["EndpointsConfig"]["private"]
        self.assertEqual(endpoint["Aliases"], ["nice-assistant", "stable-alias"])
        self.assertNotIn("IPAddress", endpoint)
        self.assertEqual(endpoint["MacAddress"], "02:42:ac:11:00:08")
        self.assertEqual(endpoint["GwPriority"], 10)

        normalized = []
        for value in (current, replacement):
            completed = subprocess.run(
                [
                    jq,
                    "-S",
                    "--arg",
                    "managed_name",
                    "nice-assistant",
                    "--argjson",
                    "preserve_explicit_mac",
                    "true",
                    "-f",
                    str(NORMALIZE_CONFIG_FILTER),
                ],
                input=json.dumps(value),
                text=True,
                capture_output=True,
                check=True,
            )
            normalized.append(json.loads(completed.stdout))
        self.assertEqual(normalized[0], normalized[1])

        generated_replacement = json.loads(json.dumps(replacement))
        generated_replacement[0]["Config"]["MacAddress"] = "02:42:ac:11:00:09"
        generated_replacement[0]["NetworkSettings"]["Networks"]["private"]["MacAddress"] = "02:42:ac:11:00:09"
        generated_payload = subprocess.run(
            [
                jq,
                "--arg",
                "image",
                "ghcr.io/owner/nice-assistant@sha256:" + ("c" * 64),
                "--argjson",
                "image_labels",
                "{}",
                "--argjson",
                "preserve_explicit_mac",
                "false",
                "-f",
                str(CREATE_PAYLOAD_FILTER),
            ],
            input=json.dumps(current),
            text=True,
            capture_output=True,
            check=True,
        )
        generated_created = json.loads(generated_payload.stdout)
        self.assertNotIn("MacAddress", generated_created)
        self.assertNotIn(
            "MacAddress",
            generated_created["NetworkingConfig"]["EndpointsConfig"]["private"],
        )
        generated_normalized = []
        for value in (current, generated_replacement):
            completed = subprocess.run(
                [
                    jq,
                    "-S",
                    "--arg",
                    "managed_name",
                    "nice-assistant",
                    "--argjson",
                    "preserve_explicit_mac",
                    "false",
                    "-f",
                    str(NORMALIZE_CONFIG_FILTER),
                ],
                input=json.dumps(value),
                text=True,
                capture_output=True,
                check=True,
            )
            generated_normalized.append(json.loads(completed.stdout))
        self.assertEqual(generated_normalized[0], generated_normalized[1])

    def test_backup_migration_acceptance_and_container_only_rollback_are_explicit(self):
        guard = GUARD.read_text(encoding="utf-8")
        self.assertIn("create_verified_backup", guard)
        self.assertIn("verify_backup", guard)
        self.assertIn("backup_restore_drill.py", guard)
        self.assertIn('mounted_snapshot="/$backup_name"', guard)
        self.assertNotIn("backup_restore_drill.py /candidate.zip", guard)
        self.assertIn("--network none", guard)
        self.assertIn("--cap-drop ALL", guard)
        self.assertIn("--security-opt no-new-privileges", guard)
        self.assertIn("database_compatible", guard)
        self.assertIn("database restore approval is required before rollback", guard)
        self.assertIn("wait_healthy", guard)
        self.assertIn("check_startup_logs", guard)
        self.assertNotIn("alembic downgrade", guard.lower())
        self.assertNotIn("restore_backup", guard)
        self.assertNotIn("docker system", guard.lower())

    def test_remote_logs_return_only_bounded_content_free_counts(self):
        guard = GUARD.read_text(encoding="utf-8")
        start = guard.index("logs_action() {")
        logs_action = guard[start : guard.index('case "$ACTION" in', start)]
        self.assertIn('"$DOCKER" logs --tail 200', logs_action)
        self.assertIn("dd bs=1024 count=64", logs_action)
        self.assertIn("wc -c", logs_action)
        self.assertIn("wc -l", logs_action)
        self.assertIn("grep -Eic", logs_action)
        self.assertIn("sample_lines:$lines", logs_action)
        self.assertIn("error_lines:$errors", logs_action)
        self.assertIn("warning_lines:$warnings", logs_action)
        self.assertIn('rm -f -- "$target"', logs_action)
        self.assertNotIn("cat ", logs_action)
        self.assertNotIn("tail -f", logs_action)

    def test_successful_deploy_keeps_recreatable_rollback_without_a_second_container(self):
        guard = GUARD.read_text(encoding="utf-8")
        deploy_start = guard.index("deploy_action() {")
        deploy = guard[deploy_start : guard.index('case "$ACTION" in', deploy_start)]
        rollback = guard[guard.index("perform_rollback() {") : guard.index("inspect_action() {")]

        self.assertIn("{state_version:3", guard)
        self.assertIn("previous_definition:$previous_definition", guard)
        self.assertIn('chmod 600 "$temporary"', guard)
        self.assertIn('previous_definition_name="previous-container-definition.${deployment_stamp}.json"', deploy)
        self.assertIn('write_state "$rollback_name"', deploy)
        self.assertIn("cleanup_guard_rollback_containers", deploy)
        self.assertIn('write_state "" "$previous_digest"', deploy)
        self.assertLess(
            deploy.index("cleanup_guard_rollback_containers"),
            deploy.index('\'{ok:true,action:"deploy"'),
        )

        self.assertIn('container_exists "$rollback_container"', rollback)
        self.assertIn('is_guard_rollback_name "$rollback_container"', rollback)
        self.assertIn('previous_definition_file=$(previous_definition_path "$previous_definition")', rollback)
        self.assertIn('create_payload "$previous_definition_file" "$previous_digest"', rollback)
        self.assertIn('create_container_from_payload "$NICE_CONTAINER_NAME" "$rollback_payload"', rollback)
        self.assertIn('[[ $(current_repo_digest "$NICE_CONTAINER_NAME") != "$previous_digest" ]]', rollback)

        self.assertIn('"$NICE_CONTAINER_NAME".rollback.*', guard)
        self.assertIn('[[ "$suffix" =~ ^[0-9]{14}$ ]]', guard)
        self.assertNotIn("container prune", guard.lower())
        self.assertNotIn('"$DOCKER" image rm', guard)

    def test_deploy_recovers_when_create_succeeds_but_the_response_fails(self):
        bash = bash_executable()
        jq = shutil.which("jq")
        root_prefix = root_command_prefix()
        if os.name != "posix" or not bash or not jq or root_prefix is None:
            self.skipTest("a POSIX root-capable bash and jq runtime is unavailable")

        temporary = Path(tempfile.mkdtemp(prefix="nice-guard-create-recovery-test-"))
        try:
            guard_dir = temporary / "guard"
            state_dir = temporary / "state"
            runtime_dir = temporary / "runtime"
            for directory in (guard_dir, state_dir, runtime_dir):
                directory.mkdir(parents=True)

            old_digest = "ghcr.io/example/nice-assistant@sha256:" + ("5" * 64)
            new_digest = "ghcr.io/example/nice-assistant@sha256:" + ("6" * 64)
            old_image_id = "sha256:" + ("a" * 64)
            new_image_id = "sha256:" + ("b" * 64)
            old_container_id = "c" * 64
            old_definition = [
                {
                    "Id": old_container_id,
                    "Name": "/nice-assistant",
                    "Image": old_image_id,
                    "Config": {
                        "Hostname": old_container_id[:12],
                        "Image": old_digest,
                        "Env": ["EXAMPLE=value"],
                        "Cmd": ["python", "-m", "app"],
                        "Labels": {
                            "keep": "yes",
                            "org.opencontainers.image.revision": "1" * 40,
                        },
                    },
                    "HostConfig": {
                        "Binds": ["/srv/nice:/data"],
                        "NetworkMode": "private",
                        "PortBindings": {"3000/tcp": [{"HostPort": "3010"}]},
                        "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                        "OomKillDisable": False,
                    },
                    "NetworkSettings": {
                        "Networks": {
                            "private": {
                                "Aliases": [
                                    "nice-assistant",
                                    old_container_id,
                                    old_container_id[:12],
                                    "stable-alias",
                                ],
                                "Links": None,
                                "DriverOpts": None,
                                "IPAMConfig": None,
                                "IPAddress": "runtime-address-old",
                                "MacAddress": "00:00:00:00:00:08",
                                "GwPriority": 10,
                            }
                        }
                    },
                    "State": {"Running": True, "Status": "running"},
                }
            ]
            runtime_state = {
                "images": {
                    old_digest: {
                        "id": old_image_id,
                        "revision": "1" * 40,
                    },
                    new_digest: {
                        "id": new_image_id,
                        "revision": "2" * 40,
                    },
                },
                "containers": {
                    "nice-assistant": {
                        "definition": old_definition,
                    }
                },
            }
            (runtime_dir / "state.json").write_text(
                json.dumps(runtime_state),
                encoding="utf-8",
                newline="\n",
            )

            fake_docker = runtime_dir / "fake-docker"
            fake_docker.write_text(
                """#!__PYTHON__
import json
import os
from pathlib import Path
import shutil
import sys

root = Path(os.environ["FAKE_RUNTIME_DIR"])
state_path = root / "state.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
with (root / "commands.log").open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")


def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")


def container(name):
    item = state["containers"].get(name)
    if item is None:
        raise SystemExit(1)
    return item["definition"][0]


def set_running(definition, running):
    definition["State"] = {
        "Running": running,
        "Status": "running" if running else "exited",
    }


if args[:2] == ["container", "inspect"]:
    name = args[-1]
    definition = container(name)
    format_value = args[args.index("--format") + 1] if "--format" in args else None
    if format_value == "{{.Config.Image}}":
        print(definition["Config"]["Image"])
    elif format_value == "{{.Image}}":
        print(definition["Image"])
    elif format_value == "{{.State.Running}}":
        print("true" if definition["State"]["Running"] else "false")
    elif format_value == "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}":
        print(definition["State"]["Status"])
    elif format_value == "{{.Name}}":
        print(definition["Name"])
    elif format_value:
        raise SystemExit("unsupported container inspect format: " + format_value)
    else:
        print(json.dumps([definition]))
elif args[:2] == ["container", "ls"]:
    for name in state["containers"]:
        if ".rollback." in name:
            print(name)
elif args[:2] == ["image", "inspect"]:
    if "--format" in args:
        format_index = args.index("--format")
        format_value = args[format_index + 1]
        references = args[2:format_index] + args[format_index + 2:]
        reference = references[0]
    else:
        format_value = None
        reference = args[2]
    image = state["images"].get(reference)
    if image is None:
        for candidate in state["images"].values():
            if candidate["id"] == reference:
                image = candidate
                break
    if image is None:
        raise SystemExit(1)
    if format_value:
        if format_value == "{{.Id}}":
            print(image["id"])
        elif format_value == '{{index .Config.Labels "org.opencontainers.image.revision"}}':
            print(image["revision"])
        elif format_value == "{{json .Config.Labels}}":
            print(json.dumps({
                "org.opencontainers.image.revision": image["revision"],
                "org.opencontainers.image.source": "https://github.com/example/nice-assistant",
            }))
        else:
            raise SystemExit("unsupported image inspect format: " + format_value)
    else:
        repo_digests = [
            digest for digest, candidate in state["images"].items()
            if candidate["id"] == image["id"]
        ]
        print(json.dumps([{
            "Id": image["id"],
            "RepoDigests": repo_digests,
            "Config": {"Labels": {
                "org.opencontainers.image.revision": image["revision"],
            }},
        }]))
elif args and args[0] == "version":
    print("1.44")
elif args and args[0] == "pull":
    print(args[1])
elif args and args[0] == "exec":
    command = " ".join(args)
    if "OperationsService" in command:
        print(json.dumps({
            "name": "nice-assistant-snapshot-20260717_120000-deadbeef.zip",
            "path": "/data/backups/nice-assistant-snapshot-20260717_120000-deadbeef.zip",
        }))
    elif "sqlite3" in command:
        print("base")
elif args and args[0] == "cp":
    Path(args[-1]).write_bytes(b"verified backup")
elif args and args[0] == "run":
    migration_revision = "next" if os.environ.get("FAKE_INCOMPATIBLE_SCHEMA") == "1" else "base"
    (root / "candidate-migration-revision").write_text(migration_revision, encoding="utf-8")
    print(json.dumps({"migration_revision": migration_revision}))
elif args and args[0] == "stop":
    name = args[-1]
    set_running(container(name), False)
    save()
    print(name)
elif args and args[0] == "start":
    name = args[-1]
    set_running(container(name), True)
    save()
    print(name)
elif args and args[0] == "rename":
    old_name, new_name = args[1], args[2]
    if new_name in state["containers"]:
        raise SystemExit("rename target already exists")
    item = state["containers"].pop(old_name, None)
    if item is None:
        raise SystemExit(1)
    item["definition"][0]["Name"] = "/" + new_name
    state["containers"][new_name] = item
    save()
elif args and args[0] == "rm":
    name = args[-1]
    if name not in state["containers"]:
        raise SystemExit(1)
    state["containers"].pop(name)
    save()
    print(name)
elif args and args[0] == "logs":
    pass
else:
    raise SystemExit("unsupported fake Docker command: " + repr(args))
""".replace("__PYTHON__", sys.executable),
                encoding="utf-8",
                newline="\n",
            )

            fake_curl = runtime_dir / "fake-curl"
            fake_curl.write_text(
                """#!__PYTHON__
import json
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

root = Path(os.environ["FAKE_RUNTIME_DIR"])
state_path = root / "state.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
payload_arg = args[args.index("--data-binary") + 1]
payload = json.loads(Path(payload_arg.removeprefix("@")).read_text(encoding="utf-8"))
name = parse_qs(urlparse(args[-1]).query)["name"][0]
if name in state["containers"]:
    raise SystemExit(1)

identifier = "d" * 64
config = {
    key: value for key, value in payload.items()
    if key not in ("HostConfig", "NetworkingConfig")
}
hostname = config.pop("Hostname", identifier[:12])
config = {"Hostname": hostname, **config}
networks = {}
for network_name, endpoint in payload["NetworkingConfig"]["EndpointsConfig"].items():
    networks[network_name] = {
        "Aliases": list(dict.fromkeys(
            [name, identifier, identifier[:12], *(endpoint.get("Aliases") or [])]
        )),
        "Links": endpoint.get("Links"),
        "DriverOpts": endpoint.get("DriverOpts"),
        "IPAMConfig": endpoint.get("IPAMConfig"),
        "IPAddress": "runtime-address-candidate",
        "MacAddress": endpoint.get("MacAddress") or "00:00:00:00:00:09",
        "GwPriority": endpoint.get("GwPriority", 0),
    }
if os.environ.get("FAKE_CONFLICT_CONFIG_MAC") == "1":
    config["MacAddress"] = "00:00:00:00:00:0a"
elif networks:
    config["MacAddress"] = next(iter(networks.values()))["MacAddress"]
image = state["images"][config["Image"]]
definition = [{
    "Id": identifier,
    "Name": "/" + name,
    "Image": image["id"],
    "Config": config,
    "HostConfig": payload["HostConfig"],
    "NetworkSettings": {"Networks": networks},
    "State": {"Running": False, "Status": "created"},
}]
state["containers"][name] = {"definition": definition}
state_path.write_text(json.dumps(state), encoding="utf-8")
(root / "created-candidate.json").write_text(json.dumps(definition), encoding="utf-8")
if os.environ.get("FAKE_CURL_RESPONSE_LOSS") == "1":
    print("simulated response loss after create", file=sys.stderr)
    raise SystemExit(22)
print(json.dumps({"Id": identifier}))
""".replace("__PYTHON__", sys.executable),
                encoding="utf-8",
                newline="\n",
            )

            guard_copy = guard_dir / GUARD.name
            guard_copy.write_text(
                GUARD.read_text(encoding="utf-8").replace("\r\n", "\n"),
                encoding="utf-8",
                newline="\n",
            )
            guard_manifest_copy = guard_dir / GUARD_BUNDLE_MANIFEST.name
            guard_manifest_copy.write_bytes(GUARD_BUNDLE_MANIFEST.read_bytes())
            invalid_manifest = temporary / "invalid-guard-bundle-manifest.json"
            invalid_manifest_payload = json.loads(
                GUARD_BUNDLE_MANIFEST.read_text(encoding="utf-8"),
            )
            invalid_manifest_payload["bundle_version"] = "3"
            invalid_manifest.write_text(
                json.dumps(invalid_manifest_payload),
                encoding="utf-8",
                newline="\n",
            )
            for source in (CREATE_PAYLOAD_FILTER, NORMALIZE_CONFIG_FILTER):
                (guard_dir / source.name).write_text(
                    source.read_text(encoding="utf-8").replace("\r\n", "\n"),
                    encoding="utf-8",
                    newline="\n",
                )
            config = guard_dir / "guard.conf"
            config_lines = (
                "NICE_CONTAINER_NAME='nice-assistant'",
                "NICE_APPROVED_IMAGE_PREFIX='ghcr.io/example/nice-assistant'",
                f"NICE_DEPLOY_STATE_DIR='{state_dir}'",
                f"NICE_DEPLOY_DOCKER_BIN='{fake_docker}'",
                f"NICE_DEPLOY_CURL_BIN='{fake_curl}'",
                f"NICE_DEPLOY_JQ_BIN='{jq}'",
                "NICE_DEPLOY_HEALTH_TIMEOUT_SECONDS='2'",
            )
            default_config = guard_dir / "guard.conf.default"
            true_config = guard_dir / "guard.conf.true"
            config_text = "\n".join((*config_lines, ""))
            config.write_text(config_text, encoding="utf-8", newline="\n")
            default_config.write_text(config_text, encoding="utf-8", newline="\n")
            true_config.write_text(
                "\n".join((*config_lines, "NICE_DEPLOY_PRESERVE_EXPLICIT_MAC='true'", "")),
                encoding="utf-8",
                newline="\n",
            )

            subprocess.run(
                root_prefix + ["chown", "-R", "root:root", str(temporary)],
                check=True,
            )
            subprocess.run(
                root_prefix
                + [
                    "chmod",
                    "0700",
                    str(temporary),
                    str(guard_dir),
                    str(state_dir),
                    str(runtime_dir),
                    str(guard_copy),
                    str(fake_docker),
                    str(fake_curl),
                ],
                check=True,
            )
            subprocess.run(
                root_prefix
                + [
                    "chmod",
                    "0600",
                    str(config),
                    str(default_config),
                    str(true_config),
                    str(guard_manifest_copy),
                    str(guard_dir / CREATE_PAYLOAD_FILTER.name),
                    str(guard_dir / NORMALIZE_CONFIG_FILTER.name),
                    str(runtime_dir / "state.json"),
                ],
                check=True,
            )

            def invoke_guard(
                *arguments: str,
                response_loss: bool = False,
                conflicting_projection: bool = False,
                incompatible_schema: bool = False,
            ):
                environment = [
                    "env",
                    f"FAKE_RUNTIME_DIR={runtime_dir}",
                    f"NICE_DEPLOY_GUARD_CONFIG={config}",
                ]
                if response_loss:
                    environment.append("FAKE_CURL_RESPONSE_LOSS=1")
                if conflicting_projection:
                    environment.append("FAKE_CONFLICT_CONFIG_MAC=1")
                if incompatible_schema:
                    environment.append("FAKE_INCOMPATIBLE_SCHEMA=1")
                return subprocess.run(
                    root_prefix + environment + [bash, str(guard_copy), *arguments],
                    check=False,
                    capture_output=True,
                    text=True,
                )

            for expected_policy, policy_config in (
                (False, default_config),
                (True, true_config),
            ):
                subprocess.run(
                    root_prefix + ["cp", "--", str(policy_config), str(config)],
                    check=True,
                )
                for action in ("inspect", "health"):
                    with self.subTest(
                        action=action,
                        preserve_explicit_mac=expected_policy,
                    ):
                        result = invoke_guard(action)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        payload = json.loads(result.stdout)
                        self.assertIs(type(payload["guard_bundle_version"]), int)
                        self.assertEqual(payload["guard_bundle_version"], 3)
                        self.assertIs(type(payload["preserve_explicit_mac"]), bool)
                        self.assertIs(
                            payload["preserve_explicit_mac"],
                            expected_policy,
                        )

            subprocess.run(
                root_prefix + ["chmod", "0644", str(guard_manifest_copy)],
                check=True,
            )
            insecure_manifest = invoke_guard("inspect")
            self.assertEqual(insecure_manifest.returncode, 78, insecure_manifest.stderr)
            self.assertIn(
                "active deployment guard bundle manifest is invalid",
                insecure_manifest.stderr,
            )
            subprocess.run(
                root_prefix + ["cp", "--", str(invalid_manifest), str(guard_manifest_copy)],
                check=True,
            )
            subprocess.run(
                root_prefix + ["chmod", "0600", str(guard_manifest_copy)],
                check=True,
            )
            invalid_manifest_result = invoke_guard("health")
            self.assertEqual(
                invalid_manifest_result.returncode,
                78,
                invalid_manifest_result.stderr,
            )
            self.assertIn(
                "active deployment guard bundle manifest is invalid",
                invalid_manifest_result.stderr,
            )
            subprocess.run(
                root_prefix
                + [
                    "cp",
                    "--",
                    str(GUARD_BUNDLE_MANIFEST),
                    str(guard_manifest_copy),
                ],
                check=True,
            )
            subprocess.run(
                root_prefix + ["chmod", "0600", str(guard_manifest_copy)],
                check=True,
            )

            subprocess.run(
                root_prefix + ["cp", "--", str(true_config), str(config)],
                check=True,
            )
            rejected_projection = invoke_guard(
                "validate-definition",
                conflicting_projection=True,
            )
            self.assertEqual(rejected_projection.returncode, 70, rejected_projection.stderr)
            self.assertIn("recreated container violated the configured MAC policy", rejected_projection.stderr)
            rejected_runtime = json.loads(
                subprocess.run(
                    root_prefix + ["cat", str(runtime_dir / "state.json")],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            self.assertEqual(set(rejected_runtime["containers"]), {"nice-assistant"})

            subprocess.run(
                root_prefix + ["rm", "-f", "--", str(runtime_dir / "commands.log")],
                check=True,
            )
            rejected_deploy = invoke_guard(
                "deploy",
                new_digest,
                conflicting_projection=True,
                incompatible_schema=True,
            )
            self.assertEqual(rejected_deploy.returncode, 70, rejected_deploy.stderr)
            self.assertIn("candidate definition failed acceptance; prior container restored", rejected_deploy.stderr)
            self.assertEqual(
                subprocess.run(
                    root_prefix + ["cat", str(runtime_dir / "candidate-migration-revision")],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout,
                "next",
            )
            restored_runtime = json.loads(
                subprocess.run(
                    root_prefix + ["cat", str(runtime_dir / "state.json")],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            self.assertEqual(set(restored_runtime["containers"]), {"nice-assistant"})
            restored_definition = restored_runtime["containers"]["nice-assistant"]["definition"][0]
            self.assertEqual(restored_definition["Id"], old_container_id)
            self.assertEqual(restored_definition["Config"]["Image"], old_digest)
            self.assertTrue(restored_definition["State"]["Running"])
            self.assertNotEqual(
                subprocess.run(
                    root_prefix + ["test", "-e", str(state_dir / "deployment-state.json")],
                    check=False,
                ).returncode,
                0,
            )
            previous_definitions = subprocess.run(
                root_prefix
                + [
                    "find",
                    str(state_dir),
                    "-maxdepth",
                    "1",
                    "-name",
                    "previous-container-definition.*.json",
                    "-print",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(previous_definitions.stdout, "")
            preflight_commands = [
                json.loads(line)
                for line in subprocess.run(
                    root_prefix + ["cat", str(runtime_dir / "commands.log")],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
            ]
            self.assertIn(["rm", "nice-assistant"], preflight_commands)
            self.assertEqual(
                sum(command[:1] == ["start"] for command in preflight_commands),
                1,
            )
            self.assertTrue(
                any(
                    command[:2] == ["exec", "nice-assistant"] and any("urllib.request" in part for part in command)
                    for command in preflight_commands
                )
            )

            subprocess.run(
                root_prefix + ["cp", "--", str(default_config), str(config)],
                check=True,
            )

            successful = invoke_guard("deploy", new_digest)
            successful_debug = successful.stderr
            for debug_path in (
                runtime_dir / "commands.log",
                runtime_dir / "state.json",
                runtime_dir / "created-candidate.json",
                state_dir / "container-definition.json",
                state_dir / "candidate-inspect.json",
            ):
                debug_result = subprocess.run(
                    root_prefix + ["cat", str(debug_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                successful_debug += f"\n{debug_path.name}:\n{debug_result.stdout}{debug_result.stderr}"
                if debug_path.name in ("container-definition.json", "candidate-inspect.json"):
                    normalized_result = subprocess.run(
                        root_prefix
                        + [
                            jq,
                            "--arg",
                            "managed_name",
                            "nice-assistant",
                            "--argjson",
                            "preserve_explicit_mac",
                            "false",
                            "-f",
                            str(guard_dir / NORMALIZE_CONFIG_FILTER.name),
                            str(debug_path),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    successful_debug += (
                        f"\nnormalized-{debug_path.name}:\n{normalized_result.stdout}{normalized_result.stderr}"
                    )
            self.assertEqual(successful.returncode, 0, successful_debug)
            guarded_state = json.loads(
                subprocess.run(
                    root_prefix + ["cat", str(state_dir / "deployment-state.json")],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            self.assertEqual(guarded_state["state_version"], 3)
            self.assertIs(guarded_state["preserve_explicit_mac"], False)

            subprocess.run(
                root_prefix + ["cp", "--", str(true_config), str(config)],
                check=True,
            )
            mismatched_policy = invoke_guard("rollback")
            self.assertEqual(mismatched_policy.returncode, 76, mismatched_policy.stderr)
            self.assertIn("deployment MAC policy changed", mismatched_policy.stderr)
            self.assertEqual(
                subprocess.run(
                    root_prefix + ["test", "-f", str(state_dir / "deployment-state.json")],
                    check=False,
                ).returncode,
                0,
            )

            subprocess.run(
                root_prefix + ["cp", "--", str(default_config), str(config)],
                check=True,
            )
            rollback = invoke_guard("rollback")
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            rollback_runtime = json.loads(
                subprocess.run(
                    root_prefix + ["cat", str(runtime_dir / "state.json")],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            prior_to_failure_id = rollback_runtime["containers"]["nice-assistant"]["definition"][0]["Id"]
            self.assertNotEqual(
                subprocess.run(
                    root_prefix + ["test", "-e", str(state_dir / "deployment-state.json")],
                    check=False,
                ).returncode,
                0,
            )
            subprocess.run(
                root_prefix
                + [
                    "rm",
                    "-f",
                    "--",
                    str(runtime_dir / "commands.log"),
                    str(runtime_dir / "created-candidate.json"),
                ],
                check=True,
            )

            deployment = invoke_guard("deploy", new_digest, response_loss=True)
            debug = deployment.stderr
            final_state = json.loads(
                subprocess.run(
                    root_prefix + ["cat", str(runtime_dir / "state.json")],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            candidate_result = subprocess.run(
                root_prefix + ["cat", str(runtime_dir / "created-candidate.json")],
                check=False,
                capture_output=True,
                text=True,
            )
            command_log_result = subprocess.run(
                root_prefix + ["cat", str(runtime_dir / "commands.log")],
                check=False,
                capture_output=True,
                text=True,
            )
            command_log = command_log_result.stdout
            debug += "\ncommands:\n" + command_log + "\nstate:\n" + json.dumps(final_state)

            self.assertEqual(deployment.returncode, 70, debug)
            self.assertIn("prior container restored", deployment.stderr)
            self.assertEqual(command_log_result.returncode, 0, debug)
            self.assertEqual(candidate_result.returncode, 0, debug)
            created_candidate = json.loads(candidate_result.stdout)
            self.assertEqual(created_candidate[0]["Name"], "/nice-assistant")
            self.assertEqual(created_candidate[0]["Config"]["Image"], new_digest)
            self.assertFalse(created_candidate[0]["State"]["Running"])
            self.assertEqual(set(final_state["containers"]), {"nice-assistant"})
            restored = final_state["containers"]["nice-assistant"]["definition"][0]
            self.assertEqual(restored["Id"], prior_to_failure_id)
            self.assertEqual(restored["Config"]["Image"], old_digest)
            self.assertTrue(restored["State"]["Running"])
            commands = [json.loads(line) for line in command_log.splitlines()]
            removal_index = commands.index(["rm", "nice-assistant"])
            restore_index = next(
                index
                for index, command in enumerate(commands)
                if len(command) == 3 and command[0] == "rename" and command[2] == "nice-assistant"
            )
            self.assertLess(removal_index, restore_index)
            self.assertIn(["start", "nice-assistant"], commands)
        finally:
            uid = os.getuid()
            gid = os.getgid()
            subprocess.run(
                root_prefix + ["chown", "-R", f"{uid}:{gid}", str(temporary)],
                check=False,
                capture_output=True,
            )
            shutil.rmtree(temporary, ignore_errors=True)

    def test_installer_validates_restrictions_before_authorizing_key(self):
        installer = INSTALLER.read_text(encoding="utf-8")
        validation = installer.index('"$LAUNCHER_NEXT" bootstrap-guard')
        definition_capture = installer.index('"$LAUNCHER_NEXT" inspect')
        launcher_switch = installer.index('mv -f -- "$LAUNCHER_NEXT"')
        authorization = installer.index('mv -fT -- "$AUTHORIZED_KEYS_NEXT" "$AUTHORIZED_KEYS"')
        final_layout_validation = installer.index(
            "validate_effective_authorized_keys_layout || {",
            launcher_switch,
        )
        self.assertLess(validation, authorization)
        self.assertLess(validation, launcher_switch)
        self.assertLess(validation, definition_capture)
        self.assertLess(definition_capture, launcher_switch)
        self.assertLess(definition_capture, authorization)
        self.assertLess(launcher_switch, authorization)
        self.assertLess(launcher_switch, final_layout_validation)
        self.assertLess(final_layout_validation, authorization)
        self.assertIn("--guard-image IMMUTABLE_DIGEST", installer)
        self.assertIn("validate_source()", installer)
        self.assertIn('[[ "$source" != *,* && "$source" != *[[:space:]]* ]]', installer)
        self.assertIn('[[ "$prefix" =~ ^([1-9]|[12][0-9]|3[0-2])$ ]]', installer)
        self.assertIn("((10#$octet <= 255))", installer)
        self.assertIn('validate_source "$SOURCE"', installer)
        self.assertIn("ssh-keygen -l -f", installer)
        self.assertIn("NICE_DEPLOY_DOCKER_BIN='$DOCKER_BIN'", installer)
        self.assertIn("NICE_DEPLOY_LAUNCHER_CONFIG=", installer)
        self.assertIn("nice_assistant_deploy_launcher.sh", installer)
        self.assertIn("restrict,from=", installer)
        self.assertIn('command="%s"', installer)
        self.assertIn('mktemp "$AUTHORIZED_KEYS_DIR/', installer)
        self.assertIn('PUBLIC_KEY_STAGED="$STATE_DIR/.deployment-public-key.$$"', installer)
        self.assertIn('install -o root -g root -m 0600 "$PUBLIC_KEY" "$PUBLIC_KEY_STAGED"', installer)
        self.assertIn('ssh-keygen -l -f "$PUBLIC_KEY_STAGED"', installer)
        self.assertIn('sub(/\\r$/, "", field)', installer)
        self.assertIn("if (NF == 0 || field != marker) print", installer)
        self.assertIn('mv -fT -- "$AUTHORIZED_KEYS_NEXT" "$AUTHORIZED_KEYS"', installer)
        self.assertNotIn('touch "$AUTHORIZED_KEYS"', installer)
        self.assertNotIn('chown root:root "$AUTHORIZED_KEYS"', installer)
        self.assertNotIn('chmod 0600 "$AUTHORIZED_KEYS"', installer)
        self.assertIn("AUTHORIZED_KEYS_INPUT=/dev/null", installer)
        self.assertIn("AUTHORIZED_KEYS_EXPECTED_SHA256=", installer)
        self.assertIn("authorized_keys changed concurrently", installer)
        self.assertIn("AUTHORIZED_KEYS_UNMANAGED_SHA256=", installer)
        self.assertIn("AUTHORIZED_KEYS_STAGED_SHA256=", installer)
        self.assertIn("AUTHORIZED_KEYS_FINAL_SHA256=", installer)
        self.assertIn("AUTHORIZED_KEYS_FINAL_UNMANAGED_SHA256=", installer)
        self.assertIn(
            'AUTHORIZED_KEYS_RECOVERY="$AUTHORIZED_KEYS_DIR/.authorized_keys.nice-assistant.recovery"',
            installer,
        )
        self.assertIn("AUTHORIZED_KEYS_SWITCHED=true", installer)
        self.assertIn(
            'install -o root -g root -m 0600 \\\n      "$AUTHORIZED_KEYS_RECOVERY" "$restore_candidate"',
            installer,
        )
        self.assertIn('mv -fT -- "$restore_candidate" "$AUTHORIZED_KEYS"', installer)
        switched = installer.index("AUTHORIZED_KEYS_SWITCHED=true", launcher_switch)
        self.assertLess(switched, authorization)
        self.assertIn(
            'recovery_hash=$(sha256sum "$AUTHORIZED_KEYS_RECOVERY"',
            installer,
        )
        self.assertIn(
            '"$recovery_hash" != "$AUTHORIZED_KEYS_EXPECTED_SHA256"',
            installer,
        )
        self.assertIn(
            '"$current_hash" != "$AUTHORIZED_KEYS_STAGED_SHA256"',
            installer,
        )
        self.assertIn("restored authorized_keys metadata did not verify", installer)
        self.assertIn("restored authorized_keys content did not verify", installer)
        self.assertIn("trap cleanup EXIT", installer)
        self.assertIn("trap 'exit 129' HUP", installer)
        self.assertIn("trap 'exit 130' INT", installer)
        self.assertIn("trap 'exit 143' TERM", installer)
        self.assertNotIn("trap cleanup EXIT HUP INT TERM", installer)
        transaction_cleanup = installer.index(
            'rm -f -- "$INSTALL_JOURNAL"',
            authorization,
        )
        signal_mask = installer.index("trap '' HUP INT TERM", transaction_cleanup)
        disarm = installer.index("AUTHORIZED_KEYS_SWITCHED=false", signal_mask)
        trap_removal = installer.index("trap - EXIT HUP INT TERM", disarm)
        self.assertLess(transaction_cleanup, signal_mask)
        self.assertLess(signal_mask, disarm)
        self.assertLess(disarm, trap_removal)
        self.assertIn("a pending authorized_keys enrollment recovery", installer)
        self.assertIn("remove the root-only enrollment recovery", installer)
        pre_switch = installer.rindex("\nsync\n", launcher_switch, authorization)
        self.assertLess(final_layout_validation, pre_switch)
        self.assertLess(pre_switch, authorization)
        self.assertIn("sync", installer)
        self.assertIn("secure_directory_ancestors()", installer)
        self.assertIn('[[ -d "$current" && ! -L "$current" ]]', installer)
        self.assertIn("[[ $(stat -c '%u' \"$current\") == 0 ]]", installer)
        self.assertIn("(( (8#$mode & 0022) == 0 ))", installer)
        self.assertIn('secure_directory_ancestors "$STATE_DIR"', installer)
        self.assertIn('secure_directory_ancestors "$AUTHORIZED_KEYS_DIR"', installer)
        self.assertIn('FILESYSTEM_TEST="$STATE_DIR/.filesystem-contract"', installer)
        self.assertIn('chmod 0700 "$FILESYSTEM_TEST/program"', installer)
        self.assertIn('chmod 0600 "$FILESYSTEM_TEST/data"', installer)
        self.assertIn('ln -s data "$FILESYSTEM_TEST/current.next"', installer)
        self.assertIn('mv -T -- "$FILESYSTEM_TEST/current.next" "$FILESYSTEM_TEST/current"', installer)
        self.assertIn('"$FILESYSTEM_TEST/program"', installer)
        self.assertIn("NICE_DEPLOY_INSTALLER_LOCKED=1", installer)
        self.assertIn('DEFINITION_FILE="$STATE_DATA_DIR/container-definition.json"', installer)
        self.assertIn('secure_root_file "$DEFINITION_FILE" 600', installer)
        self.assertIn("launcher-install.json", installer)
        self.assertIn('INSTALL_JOURNAL_NEXT="$STATE_DIR/.launcher-install.next"', installer)
        self.assertIn('CONFIG_BACKUP_NEXT="$STATE_DIR/.guard.conf.pre-launcher.next"', installer)
        self.assertIn("write_install_phase()", installer)
        self.assertIn("write_install_phase validated", installer)
        self.assertIn("write_install_phase config-switched", installer)
        self.assertIn("write_install_phase launcher-switched", installer)
        self.assertIn("/usr/bin/env -i", installer)
        self.assertIn("SSH_ORIGINAL_COMMAND=", installer)

    def test_installer_limits_unraid_authorized_keys_symlink_to_the_exact_private_layout(self):
        installer = INSTALLER.read_text(encoding="utf-8")
        canonical_branch = installer.index("validate_generic_authorized_keys_layout()")
        unraid_branch = installer.index("UNRAID_SSH_DIR=/root/.ssh")
        authorization = installer.index('mv -fT -- "$AUTHORIZED_KEYS_NEXT" "$AUTHORIZED_KEYS"')
        self.assertLess(canonical_branch, unraid_branch)
        self.assertLess(unraid_branch, authorization)
        self.assertIn("AUTHORIZED_KEYS_REQUESTED=$AUTHORIZED_KEYS", installer)
        self.assertIn('"$AUTHORIZED_KEYS_REQUESTED" != /boot', installer)
        self.assertIn('"$AUTHORIZED_KEYS_REQUESTED" != /boot/*', installer)
        self.assertIn(
            '$(readlink -m -- "$AUTHORIZED_KEYS_REQUESTED") == "$AUTHORIZED_KEYS_REQUESTED"',
            installer,
        )
        self.assertIn("UNRAID_SSH_TARGET=/boot/config/ssh/root", installer)
        self.assertIn(
            'UNRAID_AUTHORIZED_KEYS="$UNRAID_SSH_TARGET/authorized_keys"',
            installer,
        )
        self.assertIn(
            '"$AUTHORIZED_KEYS_REQUESTED" == /root/.ssh/authorized_keys',
            installer,
        )
        self.assertIn("$(stat -c '%u:%g' \"$UNRAID_SSH_DIR\") == 0:0", installer)
        self.assertIn(
            '$(readlink -- "$UNRAID_SSH_DIR") == "$UNRAID_SSH_TARGET"',
            installer,
        )
        self.assertIn(
            '$(readlink -m -- "$AUTHORIZED_KEYS_REQUESTED") == "$UNRAID_AUTHORIZED_KEYS"',
            installer,
        )
        self.assertIn("secure_directory_ancestors /root", installer)
        self.assertIn(
            'findmnt -rn -T "$UNRAID_SSH_TARGET" -o TARGET,FSTYPE,OPTIONS',
            installer,
        )
        self.assertIn("mount_record=$(", installer)
        self.assertIn(") || return 1", installer)
        self.assertIn(
            '[[ -n "$mount_record" && "$mount_record" != *$\'\\n\'* ]]',
            installer,
        )
        self.assertNotIn("mapfile -t mount_lines", installer)
        self.assertIn('"$mount_target" == /boot', installer)
        self.assertIn('"$mount_fstype" == vfat', installer)
        self.assertIn('",$mount_options," == *,rw,*', installer)
        self.assertIn('",$mount_options," == *,fmask=0177,*', installer)
        self.assertIn('",$mount_options," == *,dmask=0077,*', installer)
        self.assertIn(
            "$(stat -f -c '%T' \"$UNRAID_SSH_TARGET\") == msdos",
            installer,
        )
        self.assertIn('secure_directory_ancestors "$UNRAID_SSH_TARGET"', installer)
        self.assertIn("AUTHORIZED_KEYS=$UNRAID_AUTHORIZED_KEYS", installer)
        self.assertIn("AUTHORIZED_KEYS_DIR=$UNRAID_SSH_TARGET", installer)
        self.assertIn("AUTHORIZED_KEYS_LAYOUT=unraid", installer)
        self.assertIn("secure_authorized_keys_file()", installer)
        self.assertIn("$(stat -c '%u:%g' \"$path\") == 0:0", installer)
        self.assertIn("$(stat -c '%a' \"$path\") == 600", installer)
        self.assertIn("$(stat -c '%h' \"$path\") == 1", installer)
        self.assertIn(
            'mktemp "$AUTHORIZED_KEYS_DIR/.nice-assistant-auth.XXXXXX"',
            installer,
        )
        self.assertIn('mv -fT -- "$AUTHORIZED_KEYS_PROBE"', installer)
        self.assertIn("printf 'replaced\\n' >\"$AUTHORIZED_KEYS_PROBE_NEXT\"", installer)
        self.assertIn(
            "$(stat -c '%a' \"$AUTHORIZED_KEYS_PROBE_NEXT\") == 600",
            installer,
        )
        self.assertIn("AUTHORIZED_KEYS_PROBE_DEVICE=", installer)
        self.assertIn("AUTHORIZED_KEYS_DIR_DEVICE=", installer)
        final_validation = installer.index(
            "validate_effective_authorized_keys_layout || {",
            installer.index("write_install_phase launcher-switched"),
        )
        self.assertLess(final_validation, authorization)
        self.assertIn("authorized_keys changed concurrently", installer)
        self.assertIn("authorized_keys appeared concurrently", installer)
        self.assertIn("authorized_keys replacement did not verify", installer)
        self.assertNotIn("readlink -f", installer)

    def test_laptop_tools_use_dedicated_key_and_strict_noninteractive_ssh(self):
        remote = REMOTE.read_text(encoding="utf-8")
        keygen = KEYGEN.read_text(encoding="utf-8")
        self.assertIn("BatchMode=yes", remote)
        self.assertIn("IdentitiesOnly=yes", remote)
        self.assertIn("StrictHostKeyChecking=yes", remote)
        self.assertIn(
            "ValidateSet('inspect', 'backup', 'deploy', 'health', 'logs', 'rollback', 'update-guard', 'rollback-guard')",
            remote,
        )
        self.assertIn("$Action -in @('deploy', 'update-guard')", remote)
        self.assertIn("Digest is accepted only for deploy and update-guard", remote)
        self.assertIn("ssh-keygen.exe", keygen)
        self.assertIn("-t ed25519", keygen)
        self.assertIn("[Environment]::GetFolderPath('UserProfile')", keygen)
        self.assertIn("'.ssh\\nice_assistant_deploy_ed25519'", keygen)
        self.assertIn("must be stored outside the repository", keygen)
        self.assertNotIn(".local/deployment", keygen)
        self.assertIn("must be absolute or start with $HOME", remote)
        self.assertIn("must be stored outside the repository", remote)

    def test_container_build_context_excludes_private_runtime_artifacts(self):
        ignored = set(DOCKERIGNORE.read_text(encoding="utf-8").splitlines())
        for pattern in (
            ".git",
            ".local",
            ".env",
            ".env.*",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "*.pyc",
            "**/__pycache__",
            "**/*.pyc",
            "**/*.pyo",
            ".pytest_cache",
            ".coverage",
            "htmlcov",
            ".mypy_cache",
            ".ruff_cache",
            "dist",
            "build",
            "*.egg-info",
            "*.log",
            "*.bundle",
            "tests",
            "frontend/tests",
            "frontend/e2e",
            "test-results",
            "playwright-report",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignored)
        self.assertIn("!.env.example", ignored)
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertNotIn("COPY .local", dockerfile)
        self.assertNotIn("COPY .env ", dockerfile)
        self.assertIn("RUN test ! -e .local", dockerfile)
        self.assertIn("&& test ! -e .env", dockerfile)
        self.assertIn("&& test ! -e build", dockerfile)
        self.assertIn("&& test ! -e .coverage", dockerfile)
        self.assertIn("&& test ! -e tests", dockerfile)
        self.assertIn("&& test ! -e frontend/tests", dockerfile)
        self.assertIn("&& test ! -e frontend/e2e", dockerfile)
        self.assertIn("-name '__pycache__'", dockerfile)
        self.assertIn("-name '*.pyc'", dockerfile)
        self.assertIn("-name '*.egg-info'", dockerfile)
        self.assertIn("&& rm -rf build dist ./*.egg-info", dockerfile)
        self.assertIn("-name 'nice_assistant_deploy_ed25519*'", dockerfile)
        self.assertIn("-name 'remote.json'", dockerfile)

    def test_launcher_executes_only_a_valid_root_owned_bundle_and_scrubs_environment(self):
        bash = bash_executable()
        jq = shutil.which("jq")
        root_prefix = root_command_prefix()
        if os.name != "posix" or not bash or not jq or root_prefix is None:
            self.skipTest("a POSIX root-capable bash and jq runtime is unavailable")

        temporary = Path(tempfile.mkdtemp(prefix="nice-guard-test-"))
        try:
            install_dir = temporary / "bin"
            state_dir = temporary / "state"
            bundle_root = install_dir / "guard-bundles"
            release_root = bundle_root / "releases"
            digest_hex = "1" * 64
            release = release_root / f"sha256-{digest_hex}"
            release.mkdir(parents=True)
            recovered_digest_hex = "2" * 64
            recovered_release = release_root / f"sha256-{recovered_digest_hex}"
            state_dir.mkdir()
            launcher_copy = install_dir / "nice-assistant-deploy-guard"
            launcher_copy.write_bytes(LAUNCHER.read_bytes())

            delegated_guard = """#!/usr/bin/env bash
set -eu
printf 'args=%s\\n' "$*"
printf 'path=%s\\n' "$PATH"
printf 'locked=%s\\n' "${NICE_DEPLOY_LAUNCHER_LOCKED-unset}"
printf 'config=%s\\n' "${NICE_DEPLOY_GUARD_CONFIG-unset}"
printf 'leak=%s\\n' "${TEST_SECRET-unset}"
"""
            filters = {
                "nice_assistant_deploy_guard.sh": delegated_guard,
                "create_container_payload.jq": ".\n",
                "normalize_container_config.jq": ".\n",
            }
            modes = {
                "nice_assistant_deploy_guard.sh": "0700",
                "create_container_payload.jq": "0600",
                "normalize_container_config.jq": "0600",
            }
            for name, content in filters.items():
                (release / name).write_text(content, encoding="utf-8", newline="\n")
            manifest = {
                "schema_version": 1,
                "launcher_protocol_version": 1,
                "bundle_version": 1,
                "files": {
                    name: {"sha256": sha256_text(content), "mode": modes[name]} for name, content in filters.items()
                },
            }
            (release / "guard_bundle_manifest.json").write_text(
                json.dumps(manifest, separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            shutil.copytree(release, recovered_release)
            recovered_manifest = dict(manifest)
            recovered_manifest["bundle_version"] = 2
            (recovered_release / "guard_bundle_manifest.json").write_text(
                json.dumps(recovered_manifest, separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            (bundle_root / "current").symlink_to(f"releases/sha256-{digest_hex}")
            config = install_dir / "guard.conf"
            config_lines = (
                "NICE_CONTAINER_NAME='nice-assistant'",
                "NICE_APPROVED_IMAGE_PREFIX='ghcr.io/example/nice-assistant'",
                f"NICE_DEPLOY_STATE_DIR='{state_dir}'",
                "NICE_DEPLOY_DOCKER_BIN='/bin/true'",
                "NICE_DEPLOY_CURL_BIN='/bin/true'",
                f"NICE_DEPLOY_JQ_BIN='{jq}'",
            )
            valid_config = install_dir / "guard.conf.valid"
            invalid_configs = {
                "empty": install_dir / "guard.conf.empty",
                "malformed": install_dir / "guard.conf.malformed",
            }
            config_text = "\n".join((*config_lines, ""))
            config.write_text(config_text, encoding="utf-8", newline="\n")
            valid_config.write_text(config_text, encoding="utf-8", newline="\n")
            for value, path in (("", invalid_configs["empty"]), ("sometimes", invalid_configs["malformed"])):
                path.write_text(
                    "\n".join((*config_lines, f"NICE_DEPLOY_PRESERVE_EXPLICIT_MAC='{value}'", "")),
                    encoding="utf-8",
                    newline="\n",
                )

            subprocess.run(
                root_prefix + ["chown", "-R", "root:root", str(install_dir), str(state_dir)],
                check=True,
            )
            subprocess.run(root_prefix + ["chmod", "0700", str(install_dir)], check=True)
            subprocess.run(
                root_prefix
                + ["chmod", "0700", str(bundle_root), str(release_root), str(release), str(recovered_release)],
                check=True,
            )
            subprocess.run(
                root_prefix
                + [
                    "chmod",
                    "0700",
                    str(launcher_copy),
                    str(release / "nice_assistant_deploy_guard.sh"),
                    str(recovered_release / "nice_assistant_deploy_guard.sh"),
                ],
                check=True,
            )
            subprocess.run(
                root_prefix
                + [
                    "chmod",
                    "0600",
                    str(config),
                    str(valid_config),
                    *(str(path) for path in invalid_configs.values()),
                    str(release / "guard_bundle_manifest.json"),
                    str(release / "create_container_payload.jq"),
                    str(release / "normalize_container_config.jq"),
                    str(recovered_release / "guard_bundle_manifest.json"),
                    str(recovered_release / "create_container_payload.jq"),
                    str(recovered_release / "normalize_container_config.jq"),
                ],
                check=True,
            )

            def invoke(*arguments: str, original_command: str | None = None):
                environment = [
                    "env",
                    "TEST_SECRET=must-not-cross-boundary",
                    f"NICE_DEPLOY_LAUNCHER_CONFIG={config}",
                ]
                if original_command is not None:
                    environment.append(f"SSH_ORIGINAL_COMMAND={original_command}")
                return subprocess.run(
                    root_prefix + environment + [bash, str(launcher_copy), *arguments],
                    check=False,
                    capture_output=True,
                    text=True,
                )

            local_result = invoke("inspect")
            self.assertEqual(local_result.returncode, 0, local_result.stderr)
            self.assertIn("args=inspect", local_result.stdout)
            self.assertIn("path=/usr/sbin:/usr/bin:/sbin:/bin", local_result.stdout)
            self.assertIn("locked=1", local_result.stdout)
            self.assertIn(f"config={config}", local_result.stdout)
            self.assertIn("leak=unset", local_result.stdout)

            for label, invalid_config in invalid_configs.items():
                with self.subTest(policy=label, entrypoint="launcher"):
                    subprocess.run(
                        root_prefix + ["cp", "--", str(invalid_config), str(config)],
                        check=True,
                    )
                    rejected_policy = invoke("inspect")
                    self.assertEqual(rejected_policy.returncode, 78, rejected_policy.stderr)
                    self.assertIn("invalid explicit MAC preservation policy", rejected_policy.stderr)
                    self.assertNotIn("args=", rejected_policy.stdout)
                with self.subTest(policy=label, entrypoint="guard"):
                    rejected_policy = subprocess.run(
                        root_prefix
                        + [
                            "env",
                            f"NICE_DEPLOY_GUARD_CONFIG={config}",
                            bash,
                            str(GUARD),
                            "inspect",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(rejected_policy.returncode, 78, rejected_policy.stderr)
                    self.assertIn("invalid explicit MAC preservation policy", rejected_policy.stderr)
            subprocess.run(
                root_prefix + ["cp", "--", str(valid_config), str(config)],
                check=True,
            )

            forced_result = invoke(original_command="health")
            self.assertEqual(forced_result.returncode, 0, forced_result.stderr)
            self.assertIn("args=health", forced_result.stdout)
            self.assertIn("leak=unset", forced_result.stdout)

            for action in ("inspect", "backup", "health", "logs"):
                with self.subTest(bundle_version=1, allowed_action=action):
                    allowed = invoke(action)
                    self.assertEqual(allowed.returncode, 0, allowed.stderr)
                    self.assertIn(f"args={action}", allowed.stdout)
            valid_digest = "ghcr.io/example/nice-assistant@sha256:" + ("9" * 64)
            for arguments in (("deploy", valid_digest), ("rollback",)):
                with self.subTest(bundle_version=1, blocked_action=arguments[0]):
                    blocked = invoke(*arguments)
                    self.assertEqual(blocked.returncode, 76, blocked.stderr)
                    self.assertIn("bundle version 2 or newer", blocked.stderr)
                    self.assertNotIn("args=", blocked.stdout)

            for rejected in (
                "inspect extra",
                "bootstrap-guard ghcr.io/example/nice-assistant@sha256:" + ("2" * 64),
                "deploy ghcr.io/other/nice-assistant@sha256:" + ("2" * 64),
                "update-guard ghcr.io/example/nice-assistant@sha256:" + ("2" * 64) + " trailing",
            ):
                with self.subTest(rejected=rejected):
                    result = invoke(original_command=rejected)
                    self.assertEqual(result.returncode, 64)
                    if rejected.startswith("bootstrap"):
                        self.assertIn("deployment command is not allowed", result.stderr)
                    self.assertNotIn("args=", result.stdout)

            subprocess.run(
                root_prefix
                + [
                    "ln",
                    "-s",
                    f"releases/sha256-{digest_hex}",
                    str(bundle_root / "previous"),
                ],
                check=True,
            )
            subprocess.run(
                root_prefix
                + [
                    "ln",
                    "-s",
                    f"releases/sha256-{recovered_digest_hex}",
                    str(bundle_root / ".current.next"),
                ],
                check=True,
            )
            recovered = invoke("inspect")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            recovered_target = subprocess.run(
                root_prefix + ["readlink", str(bundle_root / "current")],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(recovered_target, f"releases/sha256-{recovered_digest_hex}")
            for arguments in (("deploy", valid_digest), ("rollback",)):
                with self.subTest(bundle_version=2, allowed_action=arguments[0]):
                    allowed = invoke(*arguments)
                    self.assertEqual(allowed.returncode, 0, allowed.stderr)
                    self.assertIn(f"args={' '.join(arguments)}", allowed.stdout)

            subprocess.run(
                root_prefix
                + [
                    "ln",
                    "-s",
                    f"releases/sha256-{digest_hex}",
                    str(bundle_root / ".current.next"),
                ],
                check=True,
            )
            subprocess.run(
                root_prefix
                + [
                    "ln",
                    "-s",
                    f"releases/sha256-{recovered_digest_hex}",
                    str(bundle_root / ".previous.next"),
                ],
                check=True,
            )
            abandoned = invoke("health")
            self.assertEqual(abandoned.returncode, 0, abandoned.stderr)
            still_current = subprocess.run(
                root_prefix + ["readlink", str(bundle_root / "current")],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(still_current, f"releases/sha256-{recovered_digest_hex}")

            subprocess.run(
                root_prefix + ["chmod", "0644", str(recovered_release / "normalize_container_config.jq")],
                check=True,
            )
            insecure = invoke("inspect")
            self.assertEqual(insecure.returncode, 78)
            self.assertIn("active deployment guard bundle is invalid", insecure.stderr)
            self.assertNotIn("args=", insecure.stdout)
        finally:
            uid = os.getuid()
            gid = os.getgid()
            subprocess.run(
                root_prefix + ["chown", "-R", f"{uid}:{gid}", str(temporary)],
                check=False,
                capture_output=True,
            )
            shutil.rmtree(temporary, ignore_errors=True)

    def test_launcher_bootstraps_and_updates_a_bundle_through_a_stopped_fake_runtime(self):
        bash = bash_executable()
        jq = shutil.which("jq")
        root_prefix = root_command_prefix()
        if os.name != "posix" or not bash or not jq or root_prefix is None:
            self.skipTest("a POSIX root-capable bash and jq runtime is unavailable")

        temporary = Path(tempfile.mkdtemp(prefix="nice-guard-update-test-"))
        try:
            install_dir = temporary / "bin"
            state_dir = temporary / "state"
            runtime_dir = temporary / "runtime"
            candidate_dir = runtime_dir / "candidate"
            for directory in (install_dir, state_dir, candidate_dir):
                directory.mkdir(parents=True)

            digest_one = "ghcr.io/example/nice-assistant@sha256:" + ("3" * 64)
            digest_two = "ghcr.io/example/nice-assistant@sha256:" + ("4" * 64)
            (runtime_dir / "running-digest").write_text(digest_one + "\n", encoding="utf-8")
            live_id = "a" * 64
            live_definition = [
                {
                    "Id": live_id,
                    "Name": "/nice-assistant",
                    "Config": {
                        "Hostname": live_id[:12],
                        "Image": "sha256:live-image",
                        "Env": ["EXAMPLE=value"],
                        "Cmd": ["python", "-m", "app"],
                        "Labels": {
                            "keep": "yes",
                            "org.opencontainers.image.revision": "old",
                        },
                    },
                    "HostConfig": {
                        "Binds": ["/srv/nice:/data"],
                        "NetworkMode": "private",
                        "PortBindings": {"3000/tcp": [{"HostPort": "3010"}]},
                        "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                        "OomKillDisable": False,
                    },
                    "NetworkSettings": {
                        "Networks": {
                            "private": {
                                "Aliases": [
                                    "nice-assistant",
                                    live_id,
                                    live_id[:12],
                                    "stable-alias",
                                ],
                                "Links": None,
                                "DriverOpts": None,
                                "IPAMConfig": None,
                                "IPAddress": "runtime-address-live",
                                "MacAddress": "00:00:00:00:00:08",
                                "GwPriority": 10,
                            }
                        }
                    },
                }
            ]
            live_path = runtime_dir / "live.json"
            valid_live_path = runtime_dir / "valid-live.json"
            live_text = json.dumps(live_definition)
            live_path.write_text(live_text, encoding="utf-8", newline="\n")
            valid_live_path.write_text(live_text, encoding="utf-8", newline="\n")
            invalid_live_paths = {}
            for label in ("zero-networks", "empty-endpoint-mac", "legacy-endpoint-mismatch"):
                definition = json.loads(live_text)
                if label == "zero-networks":
                    definition[0]["NetworkSettings"]["Networks"] = {}
                elif label == "empty-endpoint-mac":
                    definition[0]["NetworkSettings"]["Networks"]["private"]["MacAddress"] = ""
                else:
                    definition[0]["Config"]["MacAddress"] = "00:00:00:00:00:0a"
                path = runtime_dir / f"invalid-live-{label}.json"
                path.write_text(json.dumps(definition), encoding="utf-8", newline="\n")
                invalid_live_paths[label] = path
            (runtime_dir / "helpers.json").write_text("{}\n", encoding="utf-8", newline="\n")

            candidate_guard = "#!/usr/bin/env bash\nset -eu\nexit 0\n"
            candidate_files = {
                "nice_assistant_deploy_guard.sh": candidate_guard,
                "create_container_payload.jq": CREATE_PAYLOAD_FILTER.read_text(encoding="utf-8").replace("\r\n", "\n"),
                "normalize_container_config.jq": NORMALIZE_CONFIG_FILTER.read_text(encoding="utf-8").replace(
                    "\r\n", "\n"
                ),
            }
            candidate_modes = {
                "nice_assistant_deploy_guard.sh": "0700",
                "create_container_payload.jq": "0600",
                "normalize_container_config.jq": "0600",
            }
            for name, content in candidate_files.items():
                (candidate_dir / name).write_text(content, encoding="utf-8", newline="\n")
            candidate_manifest = {
                "schema_version": 1,
                "launcher_protocol_version": 1,
                "bundle_version": 1,
                "files": {
                    name: {"sha256": sha256_text(content), "mode": candidate_modes[name]}
                    for name, content in candidate_files.items()
                },
            }
            (candidate_dir / "guard_bundle_manifest.json").write_text(
                json.dumps(candidate_manifest, separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            candidate_v2_manifest = runtime_dir / "candidate-v2-manifest.json"
            candidate_manifest["bundle_version"] = 2
            candidate_v2_manifest.write_text(
                json.dumps(candidate_manifest, separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            fake_docker = runtime_dir / "fake-docker"
            fake_docker.write_text(
                f"""#!{sys.executable}
import json
import os
from pathlib import Path
import shutil
import sys

root = Path(os.environ["FAKE_RUNTIME_DIR"])
args = sys.argv[1:]
with (root / "commands.log").open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")

helpers_path = root / "helpers.json"
helpers = json.loads(helpers_path.read_text(encoding="utf-8"))

def save_helpers():
    helpers_path.write_text(json.dumps(helpers), encoding="utf-8")

def image_metadata(reference):
    running = (root / "running-digest").read_text(encoding="utf-8").strip()
    return [{{
        "Id": "sha256:fake-candidate",
        "RepoDigests": [running],
        "Config": {{
            "Labels": {{
                "org.opencontainers.image.revision": "b" * 40,
                "org.opencontainers.image.source": "https://github.com/Example/nice-assistant"
            }},
            "Volumes": None
        }}
    }}]

if args[:2] == ["container", "inspect"]:
    name = args[-1]
    format_value = args[args.index("--format") + 1] if "--format" in args else None
    if name == "nice-assistant":
        if format_value == "{{{{.Config.Image}}}}":
            print((root / "running-digest").read_text(encoding="utf-8").strip())
        elif format_value == "{{{{.Image}}}}":
            print("sha256:live-image")
        elif format_value:
            raise SystemExit("unsupported live inspect format")
        else:
            print((root / "live.json").read_text(encoding="utf-8"))
    elif name in helpers:
        helper = helpers[name]
        if format_value == '{{{{index .Config.Labels "com.nice-assistant.guard-update"}}}}':
            print(helper["label"])
        elif format_value == "{{{{.State.Running}}}}":
            print("false")
        elif format_value:
            raise SystemExit("unsupported helper inspect format")
        elif helper["kind"] == "probe":
            print((root / "probe.json").read_text(encoding="utf-8"))
        else:
            print(json.dumps([{{"Config": {{"Labels": {{
                "com.nice-assistant.guard-update": helper["label"]
            }}}}, "State": {{"Running": False}}}}]))
    else:
        raise SystemExit(1)
elif args[:2] == ["image", "inspect"]:
    if "--format" in args:
        format_value = args[args.index("--format") + 1]
        if format_value != "{{{{.Id}}}}":
            raise SystemExit("unsupported image inspect format")
        print("sha256:live-image")
    else:
        print(json.dumps(image_metadata(args[2])))
elif args and args[0] == "pull":
    print(args[1])
elif args and args[0] == "create":
    name = args[args.index("--name") + 1]
    label = args[args.index("--label") + 1].split("=", 1)[1]
    helpers[name] = {{"label": label, "kind": "extract"}}
    save_helpers()
    print("extract-id")
elif args and args[0] == "cp":
    source = args[1].split(":", 1)[1]
    shutil.copy2(root / "candidate" / Path(source).name, Path(args[2]))
elif args and args[0] == "rm":
    helpers.pop(args[1], None)
    save_helpers()
    print(args[1])
elif args and args[0] == "version":
    print("1.44")
else:
    raise SystemExit("unsupported fake Docker command: " + repr(args))
""",
                encoding="utf-8",
                newline="\n",
            )

            fake_curl = runtime_dir / "fake-curl"
            fake_curl.write_text(
                f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

root = Path(os.environ["FAKE_RUNTIME_DIR"])
args = sys.argv[1:]
(root / "curl.log").write_text(json.dumps(args), encoding="utf-8")
payload_arg = args[args.index("--data-binary") + 1]
payload = json.loads(Path(payload_arg.removeprefix("@")).read_text(encoding="utf-8"))
(root / "curl-payload.json").write_text(json.dumps(payload), encoding="utf-8")
if "Config" in payload:
    raise SystemExit("Docker create payload contains an invalid nested Config object")
name = parse_qs(urlparse(args[-1]).query)["name"][0]
probe_id = "c" * 64
config = {{key: value for key, value in payload.items()
          if key not in ("HostConfig", "NetworkingConfig")}}
config.setdefault("Hostname", probe_id[:12])
networks = {{}}
for network_name, endpoint in payload["NetworkingConfig"]["EndpointsConfig"].items():
    aliases = [name, probe_id, probe_id[:12], *(endpoint.get("Aliases") or [])]
    networks[network_name] = {{
        "Aliases": aliases,
        "Links": endpoint.get("Links"),
        "DriverOpts": endpoint.get("DriverOpts"),
        "IPAMConfig": endpoint.get("IPAMConfig"),
        "GwPriority": endpoint.get("GwPriority", 0),
        "IPAddress": "runtime-address-probe",
        "MacAddress": endpoint.get("MacAddress") or "00:00:00:00:00:09"
    }}
config["MacAddress"] = (
    "00:00:00:00:00:0a"
    if os.environ.get("FAKE_CONFLICT_CONFIG_MAC") == "1"
    else next(iter(networks.values()))["MacAddress"]
)
probe = [{{
    "Id": probe_id,
    "Name": "/" + name,
    "Config": config,
    "HostConfig": payload["HostConfig"],
    "NetworkSettings": {{"Networks": networks}},
    "State": {{"Running": False}}
}}]
(root / "probe.json").write_text(json.dumps(probe), encoding="utf-8")
projected_live = json.loads(json.dumps(probe))
projected_live[0]["Name"] = "/nice-assistant"
projected_live[0]["Config"]["Labels"].pop("com.nice-assistant.guard-update", None)
for endpoint in projected_live[0]["NetworkSettings"]["Networks"].values():
    endpoint["Aliases"] = [
        "nice-assistant",
        probe_id,
        probe_id[:12],
        "stable-alias",
    ]
(root / "projected-live.json").write_text(json.dumps(projected_live), encoding="utf-8")
helpers_path = root / "helpers.json"
helpers = json.loads(helpers_path.read_text(encoding="utf-8"))
helpers[name] = {{
    "label": config["Labels"]["com.nice-assistant.guard-update"],
    "kind": "probe"
}}
helpers_path.write_text(json.dumps(helpers), encoding="utf-8")
print(json.dumps({{"Id": probe_id}}))
""",
                encoding="utf-8",
                newline="\n",
            )

            launcher_copy = install_dir / "nice-assistant-deploy-guard"
            launcher_copy.write_bytes(LAUNCHER.read_bytes())
            config = install_dir / "guard.conf"
            config_lines = (
                "NICE_CONTAINER_NAME='nice-assistant'",
                "NICE_APPROVED_IMAGE_PREFIX='ghcr.io/example/nice-assistant'",
                f"NICE_DEPLOY_STATE_DIR='{state_dir}'",
                f"NICE_DEPLOY_DOCKER_BIN='{fake_docker}'",
                f"NICE_DEPLOY_CURL_BIN='{fake_curl}'",
                f"NICE_DEPLOY_JQ_BIN='{jq}'",
            )
            false_config = install_dir / "guard.conf.false"
            true_config = install_dir / "guard.conf.true"
            config_text = "\n".join((*config_lines, ""))
            config.write_text(config_text, encoding="utf-8", newline="\n")
            false_config.write_text(config_text, encoding="utf-8", newline="\n")
            true_config.write_text(
                "\n".join((*config_lines, "NICE_DEPLOY_PRESERVE_EXPLICIT_MAC='true'", "")),
                encoding="utf-8",
                newline="\n",
            )

            subprocess.run(
                root_prefix + ["chown", "-R", "root:root", str(install_dir), str(state_dir), str(runtime_dir)],
                check=True,
            )
            subprocess.run(
                root_prefix
                + [
                    "chmod",
                    "0700",
                    str(install_dir),
                    str(state_dir),
                    str(runtime_dir),
                    str(candidate_dir),
                    str(launcher_copy),
                    str(fake_docker),
                    str(fake_curl),
                    str(candidate_dir / "nice_assistant_deploy_guard.sh"),
                ],
                check=True,
            )
            subprocess.run(
                root_prefix
                + [
                    "chmod",
                    "0600",
                    str(config),
                    str(false_config),
                    str(true_config),
                    str(runtime_dir / "running-digest"),
                    str(live_path),
                    str(valid_live_path),
                    *(str(path) for path in invalid_live_paths.values()),
                    str(runtime_dir / "helpers.json"),
                    str(candidate_v2_manifest),
                    str(candidate_dir / "guard_bundle_manifest.json"),
                    str(candidate_dir / "create_container_payload.jq"),
                    str(candidate_dir / "normalize_container_config.jq"),
                ],
                check=True,
            )

            def invoke(
                action: str,
                digest: str,
                *,
                conflicting_projection: bool = False,
            ):
                environment = [
                    "env",
                    f"FAKE_RUNTIME_DIR={runtime_dir}",
                    f"NICE_DEPLOY_LAUNCHER_CONFIG={config}",
                ]
                if conflicting_projection:
                    environment.append("FAKE_CONFLICT_CONFIG_MAC=1")
                return subprocess.run(
                    root_prefix + environment + [bash, str(launcher_copy), action, digest],
                    check=False,
                    capture_output=True,
                    text=True,
                )

            def root_read(path: Path) -> str:
                return subprocess.run(
                    root_prefix + ["cat", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout

            def root_exists(path: Path) -> bool:
                return (
                    subprocess.run(
                        root_prefix + ["test", "-e", str(path)],
                        check=False,
                        capture_output=True,
                    ).returncode
                    == 0
                )

            rejected_v1_bootstrap = invoke("bootstrap-guard", digest_one)
            self.assertEqual(rejected_v1_bootstrap.returncode, 76, rejected_v1_bootstrap.stderr)
            self.assertIn("initial deployment guard bundle must be version 2 or newer", rejected_v1_bootstrap.stderr)
            self.assertFalse(root_exists(install_dir / "guard-bundles" / "current"))
            subprocess.run(
                root_prefix
                + [
                    "cp",
                    "--",
                    str(candidate_v2_manifest),
                    str(candidate_dir / "guard_bundle_manifest.json"),
                ],
                check=True,
            )

            subprocess.run(
                root_prefix + ["cp", "--", str(true_config), str(config)],
                check=True,
            )
            for label, invalid_live_path in invalid_live_paths.items():
                with self.subTest(explicit_mac_input=label):
                    subprocess.run(
                        root_prefix + ["cp", "--", str(invalid_live_path), str(live_path)],
                        check=True,
                    )
                    rejected_input = invoke("bootstrap-guard", digest_one)
                    self.assertEqual(rejected_input.returncode, 70, rejected_input.stderr)
                    self.assertIn("guard bundle did not preserve", rejected_input.stderr)
                    self.assertFalse(root_exists(install_dir / "guard-bundles" / "current"))
                    self.assertEqual(json.loads(root_read(runtime_dir / "helpers.json")), {})
            subprocess.run(
                root_prefix + ["cp", "--", str(valid_live_path), str(live_path)],
                check=True,
            )
            rejected_projection = invoke(
                "bootstrap-guard",
                digest_one,
                conflicting_projection=True,
            )
            self.assertEqual(rejected_projection.returncode, 70, rejected_projection.stderr)
            self.assertIn("guard bundle did not preserve", rejected_projection.stderr)
            conflicting_probe = json.loads(root_read(runtime_dir / "probe.json"))
            self.assertEqual(
                conflicting_probe[0]["NetworkSettings"]["Networks"]["private"]["MacAddress"],
                "00:00:00:00:00:08",
            )
            self.assertEqual(
                conflicting_probe[0]["Config"]["MacAddress"],
                "00:00:00:00:00:0a",
            )
            self.assertFalse(root_exists(install_dir / "guard-bundles" / "current"))
            self.assertEqual(json.loads(root_read(runtime_dir / "helpers.json")), {})
            subprocess.run(
                root_prefix + ["cp", "--", str(false_config), str(config)],
                check=True,
            )

            bootstrap = invoke("bootstrap-guard", digest_one)
            bootstrap_debug = bootstrap.stderr + "\ncommands:\n" + root_read(runtime_dir / "commands.log")
            if root_exists(runtime_dir / "curl.log"):
                bootstrap_debug += "\ncurl:\n" + root_read(runtime_dir / "curl.log")
            if root_exists(runtime_dir / "curl-payload.json"):
                bootstrap_debug += "\ncurl payload:\n" + root_read(runtime_dir / "curl-payload.json")
            if root_exists(runtime_dir / "probe.json"):
                bootstrap_debug += "\nprobe:\n" + root_read(runtime_dir / "probe.json")
            self.assertEqual(bootstrap.returncode, 0, bootstrap_debug)
            bootstrap_result = json.loads(bootstrap.stdout)
            self.assertEqual(bootstrap_result["action"], "bootstrap-guard")
            self.assertEqual(bootstrap_result["digest"], digest_one)
            probe_payload = json.loads(root_read(runtime_dir / "curl-payload.json"))
            probe_endpoint = probe_payload["NetworkingConfig"]["EndpointsConfig"]["private"]
            self.assertNotIn("MacAddress", probe_endpoint)
            self.assertEqual(probe_endpoint["GwPriority"], 10)
            current_target = subprocess.run(
                root_prefix + ["readlink", str(install_dir / "guard-bundles" / "current")],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(current_target, "releases/sha256-" + ("3" * 64))
            self.assertFalse(root_exists(state_dir / "guard-update.json"))

            projected_definition = json.loads(root_read(runtime_dir / "projected-live.json"))
            self.assertEqual(
                projected_definition[0]["Config"]["MacAddress"],
                "00:00:00:00:00:09",
            )
            subprocess.run(
                root_prefix
                + [
                    "cp",
                    "--",
                    str(runtime_dir / "projected-live.json"),
                    str(runtime_dir / "live.json"),
                ],
                check=True,
            )
            update = invoke("update-guard", digest_one)
            self.assertEqual(update.returncode, 0, update.stderr)
            self.assertEqual(json.loads(update.stdout)["action"], "update-guard")

            rejected_digest = invoke("update-guard", digest_two)
            self.assertEqual(rejected_digest.returncode, 64)
            self.assertIn("exact running Nice Assistant digest", rejected_digest.stderr)

            subprocess.run(
                root_prefix + ["chmod", "0755", str(candidate_dir / "nice_assistant_deploy_guard.sh")],
                check=True,
            )
            wrong_mode = invoke("bootstrap-guard", digest_two)
            self.assertEqual(wrong_mode.returncode, 70)
            self.assertIn("guard bundle contains an unsafe file", wrong_mode.stderr)
            self.assertFalse(root_exists(state_dir / "guard-update.json"))

            command_log = root_read(runtime_dir / "commands.log")
            self.assertNotIn('["start"', command_log)
            self.assertNotIn('["exec"', command_log)
            self.assertNotIn('["run"', command_log)
            helpers = json.loads(root_read(runtime_dir / "helpers.json"))
            self.assertEqual(helpers, {})
        finally:
            uid = os.getuid()
            gid = os.getgid()
            subprocess.run(
                root_prefix + ["chown", "-R", f"{uid}:{gid}", str(temporary)],
                check=False,
                capture_output=True,
            )
            shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
