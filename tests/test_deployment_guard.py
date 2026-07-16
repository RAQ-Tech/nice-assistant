import os
from pathlib import Path
import shutil
import subprocess
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "deployment" / "nice_assistant_deploy_guard.sh"
INSTALLER = ROOT / "scripts" / "deployment" / "install_unraid_deploy_guard.sh"
REMOTE = ROOT / "scripts" / "deployment" / "invoke_guarded_deploy.ps1"
KEYGEN = ROOT / "scripts" / "deployment" / "new_laptop_deploy_key.ps1"
CREATE_PAYLOAD_FILTER = ROOT / "scripts" / "deployment" / "create_container_payload.jq"
NORMALIZE_CONFIG_FILTER = ROOT / "scripts" / "deployment" / "normalize_container_config.jq"


def bash_executable() -> str | None:
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash")


class DeploymentGuardContractTests(unittest.TestCase):
    def test_shell_entrypoints_are_syntax_valid(self):
        bash = bash_executable()
        if not bash:
            self.skipTest("bash is unavailable")
        for script in (GUARD, INSTALLER):
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

    def test_deploy_requires_exact_approved_digest_and_preserves_effective_config(self):
        guard = GUARD.read_text(encoding="utf-8")
        payload_filter = CREATE_PAYLOAD_FILTER.read_text(encoding="utf-8")
        self.assertIn('[[ "$digest" == "${NICE_APPROVED_IMAGE_PREFIX}@sha256:"* ]]', guard)
        self.assertIn('[[ "$suffix" =~ ^[0-9a-f]{64}$ ]]', guard)
        self.assertIn("HostConfig: $container.HostConfig", payload_filter)
        self.assertIn("EndpointsConfig", payload_filter)
        self.assertIn("IPAMConfig: (.IPAMConfig // null)", payload_filter)
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

        def definition(container_id, name, revision, address, mac):
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
                    "HostConfig": host_config,
                    "NetworkSettings": {
                        "Networks": {
                            "private": {
                                "Aliases": [name, container_id[:12], "stable-alias"],
                                "Links": None,
                                "DriverOpts": None,
                                "IPAMConfig": None,
                                "IPAddress": address,
                                "MacAddress": mac,
                            }
                        }
                    },
                }
            ]

        current = definition(current_id, "nice-assistant", "old", "assigned-address-one", "assigned-mac-one")
        replacement = definition(
            replacement_id,
            "nice-assistant",
            "new",
            "assigned-address-two",
            "assigned-mac-two",
        )
        payload = subprocess.run(
            [
                jq,
                "--arg",
                "image",
                "ghcr.io/owner/nice-assistant@sha256:" + ("c" * 64),
                "--argjson",
                "image_labels",
                json.dumps({"org.opencontainers.image.revision": "new", "ignored": "value"}),
                "-f",
                str(CREATE_PAYLOAD_FILTER),
            ],
            input=json.dumps(current),
            text=True,
            capture_output=True,
            check=True,
        )
        created = json.loads(payload.stdout)
        self.assertEqual(created["HostConfig"], host_config)
        self.assertEqual(created["Labels"]["keep"], "yes")
        self.assertEqual(created["Labels"]["org.opencontainers.image.revision"], "new")
        self.assertNotIn("ignored", created["Labels"])
        endpoint = created["NetworkingConfig"]["EndpointsConfig"]["private"]
        self.assertEqual(endpoint["Aliases"], ["nice-assistant", "stable-alias"])
        self.assertNotIn("IPAddress", endpoint)
        self.assertNotIn("MacAddress", endpoint)

        normalized = []
        for value in (current, replacement):
            completed = subprocess.run(
                [jq, "-S", "-f", str(NORMALIZE_CONFIG_FILTER)],
                input=json.dumps(value),
                text=True,
                capture_output=True,
                check=True,
            )
            normalized.append(json.loads(completed.stdout))
        self.assertEqual(normalized[0], normalized[1])

    def test_backup_migration_acceptance_and_container_only_rollback_are_explicit(self):
        guard = GUARD.read_text(encoding="utf-8")
        self.assertIn("create_verified_backup", guard)
        self.assertIn("verify_backup", guard)
        self.assertIn("backup_restore_drill.py", guard)
        self.assertIn("database_compatible", guard)
        self.assertIn("database restore approval is required before rollback", guard)
        self.assertIn("wait_healthy", guard)
        self.assertIn("check_startup_logs", guard)
        self.assertNotIn("alembic downgrade", guard.lower())
        self.assertNotIn("restore_backup", guard)
        self.assertNotIn("docker system", guard.lower())

    def test_installer_validates_restrictions_before_authorizing_key(self):
        installer = INSTALLER.read_text(encoding="utf-8")
        validation = installer.index("validate-definition")
        authorization = installer.index('nice-assistant-deploy-guard" "$KEY')
        self.assertLess(validation, authorization)
        self.assertIn('[[ "$SOURCE" =~ ^[A-Fa-f0-9:.,/]+$ ]]', installer)
        self.assertIn("ssh-keygen -l -f", installer)
        self.assertIn("NICE_DEPLOY_DOCKER_BIN='$DOCKER_BIN'", installer)
        self.assertIn("restrict,from=", installer)
        self.assertIn('command="%s"', installer)

    def test_laptop_tools_use_dedicated_key_and_strict_noninteractive_ssh(self):
        remote = REMOTE.read_text(encoding="utf-8")
        keygen = KEYGEN.read_text(encoding="utf-8")
        self.assertIn("BatchMode=yes", remote)
        self.assertIn("IdentitiesOnly=yes", remote)
        self.assertIn("StrictHostKeyChecking=yes", remote)
        self.assertIn("ValidateSet('inspect', 'backup', 'deploy', 'health', 'logs', 'rollback')", remote)
        self.assertIn("ssh-keygen.exe", keygen)
        self.assertIn("-t ed25519", keygen)
        self.assertIn(".local/deployment", keygen)


if __name__ == "__main__":
    unittest.main()
