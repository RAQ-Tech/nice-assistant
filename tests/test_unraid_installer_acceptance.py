from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "deployment" / "install_unraid_deploy_guard.sh"
IMAGE_PREFIX = "ghcr.io/example/nice-assistant"
GUARD_IMAGE = f"{IMAGE_PREFIX}@sha256:{'a' * 64}"
CONTAINER_NAME = "nice-assistant"
SOURCE_CIDR = "192.0.2.0/24"
MARKER = "nice-assistant-deploy-guard"
PATH_LINE = "export PATH=/usr/sbin:/usr/bin:/sbin:/bin"
TEST_PATH_LINE = 'export PATH="${NICE_INSTALLER_TEST_BIN:?}:/usr/sbin:/usr/bin:/sbin:/bin"'


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
        timeout=5,
    )
    return [sudo, "-n"] if available.returncode == 0 else None


ROOT_COMMAND_PREFIX = root_command_prefix()
RUNTIME_COMMANDS = {
    name: shutil.which(name)
    for name in (
        "bash",
        "env",
        "flock",
        "install",
        "ssh-keygen",
        "sha256sum",
        "mv",
        "rm",
        "stat",
        "sync",
    )
}
ROOT_RUNTIME_AVAILABLE = (
    os.name == "posix"
    and ROOT_COMMAND_PREFIX is not None
    and all(value is not None for value in RUNTIME_COMMANDS.values())
)


@unittest.skipUnless(ROOT_RUNTIME_AVAILABLE, "a POSIX root-capable installer runtime is unavailable")
class UnraidInstallerExecutableAcceptanceTests(unittest.TestCase):
    """Execute the installer transaction without touching the host SSH layout."""

    root_prefix: list[str]
    bash: str
    env_command: str
    real_mv: str
    real_rm: str
    real_stat: str
    real_sync: str
    ssh_keygen: str

    @classmethod
    def setUpClass(cls) -> None:
        assert ROOT_COMMAND_PREFIX is not None
        cls.root_prefix = ROOT_COMMAND_PREFIX
        cls.bash = str(RUNTIME_COMMANDS["bash"])
        cls.env_command = str(RUNTIME_COMMANDS["env"])
        cls.real_mv = str(RUNTIME_COMMANDS["mv"])
        cls.real_rm = str(RUNTIME_COMMANDS["rm"])
        cls.real_stat = str(RUNTIME_COMMANDS["stat"])
        cls.real_sync = str(RUNTIME_COMMANDS["sync"])
        cls.ssh_keygen = str(RUNTIME_COMMANDS["ssh-keygen"])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="nice-installer-acceptance-")
        self.addCleanup(self._temporary.cleanup)
        self.work = Path(self._temporary.name)
        self.fake_bin = self.work / "bin"
        self.source_dir = self.work / "source"
        self.fake_bin.mkdir()
        self.source_dir.mkdir()
        self.final_auth_failure_sentinel = self.work / "final-auth-validation-fault-fired"
        self.final_rename_failure_sentinel = self.work / "final-auth-rename-fault-fired"
        self.precommit_term_sentinel = self.work / "precommit-term-sent"
        self.ambiguous_auth_sentinel = self.work / "ambiguous-auth-injected"
        self.recovery_sync_failure_sentinel = self.work / "recovery-sync-fault-fired"
        self._write_fake_commands()
        self.public_key = self.work / "deploy-key"
        subprocess.run(
            [
                self.ssh_keygen,
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "installer-acceptance",
                "-f",
                str(self.public_key),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.public_key = self.public_key.with_suffix(".pub")
        self.secure_base = self._new_secure_base()
        self.addCleanup(self._remove_secure_base)

    def _root_run(
        self,
        arguments: list[str],
        *,
        environment: dict[str, str] | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = [*self.root_prefix]
        if environment:
            command.extend([self.env_command, *(f"{key}={value}" for key, value in environment.items())])
        command.extend(arguments)
        return subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def _new_secure_base(self) -> Path:
        for parent in (Path("/run"), Path("/var/lib")):
            contract = self._root_run(["stat", "-c", "%u:%a", str(parent)])
            if contract.returncode != 0:
                continue
            owner_text, mode_text = contract.stdout.strip().split(":", maxsplit=1)
            if owner_text != "0" or int(mode_text, 8) & 0o022:
                continue
            created = self._root_run(["mktemp", "-d", f"{parent}/nice-assistant-installer-acceptance.XXXXXX"])
            if created.returncode == 0:
                path = Path(created.stdout.strip())
                self._root_run(["chown", "root:root", str(path)], check=True)
                self._root_run(["chmod", "0700", str(path)], check=True)
                return path
        self.skipTest("no secure root-owned temporary parent is available")
        raise AssertionError("unreachable")

    def _remove_secure_base(self) -> None:
        path = self.secure_base
        if path.parent not in (Path("/run"), Path("/var/lib")):
            raise AssertionError(f"refusing to clean unexpected test path: {path}")
        if not path.name.startswith("nice-assistant-installer-acceptance."):
            raise AssertionError(f"refusing to clean unexpected test path: {path}")
        self._root_run(["rm", "-rf", "--", str(path)])

    def _root_install_directory(self, path: Path, mode: str = "0700") -> None:
        self._root_run(
            ["install", "-d", "-o", "root", "-g", "root", "-m", mode, str(path)],
            check=True,
        )

    def _root_install_text(self, path: Path, content: str, mode: str) -> None:
        staged = self.work / f"staged-{len(list(self.work.glob('staged-*')))}"
        staged.write_text(content, encoding="utf-8")
        self._root_run(
            ["install", "-o", "root", "-g", "root", "-m", mode, str(staged), str(path)],
            check=True,
        )

    def _root_read(self, path: Path) -> str:
        completed = self._root_run(["cat", str(path)], check=True)
        return completed.stdout

    def _root_mode(self, path: Path) -> int:
        completed = self._root_run(["stat", "-c", "%a", str(path)], check=True)
        return int(completed.stdout.strip(), 8)

    def _root_stat_identity(self, path: Path) -> str:
        completed = self._root_run(["stat", "-c", "%i:%a:%y", str(path)], check=True)
        return completed.stdout.strip()

    def _root_sha256(self, path: Path) -> str:
        completed = self._root_run(["sha256sum", str(path)], check=True)
        return completed.stdout.split()[0]

    def _root_exists(self, path: Path) -> bool:
        return self._root_run(["test", "-e", str(path)]).returncode == 0

    def _root_symlink(self, target: Path, link: Path) -> None:
        self._root_run(["ln", "-s", str(target), str(link)], check=True)

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _write_fake_commands(self) -> None:
        success = "#!/bin/sh\nexit 0\n"
        for name in ("docker", "curl", "jq"):
            self._write_executable(self.fake_bin / name, success)

        findmnt = """#!/bin/bash
set -euo pipefail
[[ " $* " == *" -T ${NICE_INSTALLER_TEST_UNRAID_TARGET:?} "* ]] || exit 91
printf '%s vfat rw,fmask=0177,dmask=0077\\n' "${NICE_INSTALLER_TEST_UNRAID_BOOT:?}"
"""
        self._write_executable(self.fake_bin / "findmnt", findmnt)

        stat_wrapper = f"""#!/bin/bash
set -euo pipefail
if [[ ${{NICE_INSTALLER_TEST_UNRAID_TARGET:-}} &&
  ${{1:-}} == -f &&
  ${{2:-}} == -c &&
  ${{3:-}} == %T &&
  ${{4:-}} == "$NICE_INSTALLER_TEST_UNRAID_TARGET" ]]; then
  printf 'msdos\\n'
  exit 0
fi
if [[ ${{NICE_INSTALLER_TEST_FAIL_FINAL_AUTH_PATH:-}} &&
  ${{1:-}} == -c &&
  ${{2:-}} == %h &&
  ${{3:-}} == "$NICE_INSTALLER_TEST_FAIL_FINAL_AUTH_PATH" &&
  ! -e "${{NICE_INSTALLER_TEST_FAIL_FINAL_AUTH_SENTINEL:?}}" ]] &&
  grep -q -- {shlex.quote(MARKER)} "$NICE_INSTALLER_TEST_FAIL_FINAL_AUTH_PATH"; then
  touch "$NICE_INSTALLER_TEST_FAIL_FINAL_AUTH_SENTINEL"
  exit 92
fi
exec {shlex.quote(self.real_stat)} "$@"
"""
        self._write_executable(self.fake_bin / "stat", stat_wrapper)

        mv_wrapper = f"""#!/bin/bash
set -euo pipefail
{shlex.quote(self.real_mv)} "$@"
destination=${{!#}}
if [[ ${{NICE_INSTALLER_TEST_FAIL_AFTER_FINAL_RENAME:-0}} == 1 &&
  "$destination" == "${{NICE_INSTALLER_TEST_FAIL_FINAL_RENAME_PATH:?}}" &&
  ! -e "${{NICE_INSTALLER_TEST_FAIL_FINAL_RENAME_SENTINEL:?}}" ]]; then
  touch "$NICE_INSTALLER_TEST_FAIL_FINAL_RENAME_SENTINEL"
  exit 93
fi
if [[ ${{NICE_INSTALLER_TEST_RACE_AFTER_LAUNCHER_SWITCH:-0}} == 1 ]]; then
  journal="${{NICE_INSTALLER_TEST_STATE_DIR:?}}/launcher-install.json"
  sentinel="${{NICE_INSTALLER_TEST_STATE_DIR:?}}/.race-fired"
  if [[ "$destination" == "$journal" &&
    ! -e "$sentinel" &&
    -f "$journal" ]] &&
    grep -q '"phase":"launcher-switched"' "$journal"; then
    target=${{NICE_INSTALLER_TEST_UNRAID_TARGET:?}}
    saved="${{target}}.validated"
    attacker=${{NICE_INSTALLER_TEST_RACE_ATTACKER:?}}
    {shlex.quote(self.real_mv)} -- "$target" "$saved"
    ln -s -- "$attacker" "$target"
    touch "$sentinel"
  fi
fi
"""
        self._write_executable(self.fake_bin / "mv", mv_wrapper)

        rm_wrapper = f"""#!/bin/bash
set -euo pipefail
{shlex.quote(self.real_rm)} "$@"
if [[ ${{NICE_INSTALLER_TEST_TERM_AT_PRECOMMIT:-0}} == 1 &&
  ! -e "${{NICE_INSTALLER_TEST_PRECOMMIT_TERM_SENTINEL:?}}" ]]; then
  for removed in "$@"; do
    if [[ "$removed" == "${{NICE_INSTALLER_TEST_PRECOMMIT_JOURNAL:?}}" ]]; then
      touch "$NICE_INSTALLER_TEST_PRECOMMIT_TERM_SENTINEL"
      kill -TERM "$PPID"
      break
    fi
  done
fi
"""
        self._write_executable(self.fake_bin / "rm", rm_wrapper)

        sync_wrapper = f"""#!/bin/bash
set -euo pipefail
if [[ ${{NICE_INSTALLER_TEST_INJECT_AMBIGUOUS_AUTH:-0}} == 1 &&
  -f "${{NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_PATH:?}}" &&
  ! -e "${{NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_SENTINEL:?}}" ]] &&
  grep -q -- {shlex.quote(MARKER)} "$NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_PATH"; then
  cat "${{NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_SOURCE:?}}" \
    >"$NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_PATH"
  chown root:root "$NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_PATH"
  chmod 0600 "$NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_PATH"
  touch "$NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_SENTINEL"
  {shlex.quote(self.real_sync)}
  exit 94
fi
if [[ ${{NICE_INSTALLER_TEST_FAIL_RECOVERY_SYNC:-0}} == 1 &&
  -e "${{NICE_INSTALLER_TEST_RECOVERY_SYNC_ARMED_SENTINEL:?}}" &&
  ! -e "${{NICE_INSTALLER_TEST_RECOVERY_SYNC_SENTINEL:?}}" ]]; then
  touch "$NICE_INSTALLER_TEST_RECOVERY_SYNC_SENTINEL"
  exit 96
fi
exec {shlex.quote(self.real_sync)} "$@"
"""
        self._write_executable(self.fake_bin / "sync", sync_wrapper)

    def _instrument_installer(self, *, unraid_root: Path | None = None) -> Path:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertEqual(source.count(PATH_LINE), 1)
        source = source.replace(PATH_LINE, TEST_PATH_LINE, 1)
        if unraid_root is not None:
            self.assertIn("/root/.ssh/authorized_keys", source)
            self.assertIn("/root/.ssh", source)
            self.assertIn("/boot", source)
            source = source.replace("/boot/config/ssh/root", "__TEST_UNRAID_SSH_TARGET__")
            source = source.replace("/root/.ssh/authorized_keys", "__TEST_UNRAID_AUTHORIZED_KEYS__")
            source = source.replace("/root/.ssh", "__TEST_UNRAID_SSH_DIR__")
            source = source.replace("/root", "__TEST_UNRAID_ROOT__")
            source = source.replace("/boot", "__TEST_UNRAID_BOOT__")
            source = source.replace(
                "__TEST_UNRAID_SSH_TARGET__",
                f"{unraid_root}/boot/config/ssh/root",
            )
            source = source.replace(
                "__TEST_UNRAID_AUTHORIZED_KEYS__",
                f"{unraid_root}/root/.ssh/authorized_keys",
            )
            source = source.replace("__TEST_UNRAID_SSH_DIR__", f"{unraid_root}/root/.ssh")
            source = source.replace("__TEST_UNRAID_ROOT__", f"{unraid_root}/root")
            source = source.replace("__TEST_UNRAID_BOOT__", f"{unraid_root}/boot")
        installer = self.source_dir / "install_unraid_deploy_guard.sh"
        self._write_executable(installer, source)
        return installer

    def _write_launcher_stub(self, *, fail_action: str = "", term_action: str = "") -> None:
        launcher = f"""#!/bin/bash
set -Eeuo pipefail
source "${{NICE_DEPLOY_LAUNCHER_CONFIG:?}}"
action=${{1:-}}
printf '%s\\n' "$action" >>"$NICE_DEPLOY_STATE_DIR/launcher-actions.log"
if [[ "$action" == {shlex.quote(fail_action)} ]]; then
  exit 42
fi
if [[ "$action" == {shlex.quote(term_action)} ]]; then
  touch "$NICE_DEPLOY_STATE_DIR/term-sent"
  kill -TERM "$PPID"
fi
case "$action" in
  bootstrap-guard)
    [[ ${{2:-}} == {shlex.quote(GUARD_IMAGE)} ]]
    ;;
  inspect)
    printf '{{"schema_version":1}}\\n' >"$NICE_DEPLOY_STATE_DIR/container-definition.json"
    chown root:root "$NICE_DEPLOY_STATE_DIR/container-definition.json"
    chmod 0600 "$NICE_DEPLOY_STATE_DIR/container-definition.json"
    ;;
  *)
    exit 64
    ;;
esac
"""
        self._write_executable(self.source_dir / "nice_assistant_deploy_launcher.sh", launcher)

    def _installer_environment(
        self,
        *,
        unraid_boot: Path | None = None,
        unraid_target: Path | None = None,
        state_dir: Path | None = None,
        attacker: Path | None = None,
        fail_final_authorization: Path | None = None,
        fail_after_final_rename: Path | None = None,
        term_at_precommit_journal: Path | None = None,
        ambiguous_authorization: tuple[Path, Path] | None = None,
        fail_recovery_sync: bool = False,
    ) -> dict[str, str]:
        environment = {"NICE_INSTALLER_TEST_BIN": str(self.fake_bin)}
        if unraid_boot is not None:
            environment["NICE_INSTALLER_TEST_UNRAID_BOOT"] = str(unraid_boot)
        if unraid_target is not None:
            environment["NICE_INSTALLER_TEST_UNRAID_TARGET"] = str(unraid_target)
        if state_dir is not None:
            environment["NICE_INSTALLER_TEST_STATE_DIR"] = str(state_dir)
        if attacker is not None:
            environment["NICE_INSTALLER_TEST_RACE_AFTER_LAUNCHER_SWITCH"] = "1"
            environment["NICE_INSTALLER_TEST_RACE_ATTACKER"] = str(attacker)
        if fail_final_authorization is not None:
            environment["NICE_INSTALLER_TEST_FAIL_FINAL_AUTH_PATH"] = str(fail_final_authorization)
            environment["NICE_INSTALLER_TEST_FAIL_FINAL_AUTH_SENTINEL"] = str(self.final_auth_failure_sentinel)
        if fail_after_final_rename is not None:
            environment["NICE_INSTALLER_TEST_FAIL_AFTER_FINAL_RENAME"] = "1"
            environment["NICE_INSTALLER_TEST_FAIL_FINAL_RENAME_PATH"] = str(fail_after_final_rename)
            environment["NICE_INSTALLER_TEST_FAIL_FINAL_RENAME_SENTINEL"] = str(self.final_rename_failure_sentinel)
        if term_at_precommit_journal is not None:
            environment["NICE_INSTALLER_TEST_TERM_AT_PRECOMMIT"] = "1"
            environment["NICE_INSTALLER_TEST_PRECOMMIT_JOURNAL"] = str(term_at_precommit_journal)
            environment["NICE_INSTALLER_TEST_PRECOMMIT_TERM_SENTINEL"] = str(self.precommit_term_sentinel)
        if ambiguous_authorization is not None:
            authorization_path, unexpected_source = ambiguous_authorization
            environment["NICE_INSTALLER_TEST_INJECT_AMBIGUOUS_AUTH"] = "1"
            environment["NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_PATH"] = str(authorization_path)
            environment["NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_SOURCE"] = str(unexpected_source)
            environment["NICE_INSTALLER_TEST_AMBIGUOUS_AUTH_SENTINEL"] = str(self.ambiguous_auth_sentinel)
        if fail_recovery_sync:
            environment["NICE_INSTALLER_TEST_FAIL_RECOVERY_SYNC"] = "1"
            environment["NICE_INSTALLER_TEST_RECOVERY_SYNC_ARMED_SENTINEL"] = str(self.final_rename_failure_sentinel)
            environment["NICE_INSTALLER_TEST_RECOVERY_SYNC_SENTINEL"] = str(self.recovery_sync_failure_sentinel)
        return environment

    def _run_installer(
        self,
        *,
        installer: Path,
        state_dir: Path,
        authorized_keys: Path,
        environment: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return self._root_run(
            [
                self.bash,
                str(installer),
                "--container",
                CONTAINER_NAME,
                "--image-prefix",
                IMAGE_PREFIX,
                "--guard-image",
                GUARD_IMAGE,
                "--public-key",
                str(self.public_key),
                "--source",
                SOURCE_CIDR,
                "--state-dir",
                str(state_dir),
                "--authorized-keys",
                str(authorized_keys),
            ],
            environment=environment,
        )

    def _legacy_config(self, state_dir: Path) -> str:
        return (
            f"NICE_CONTAINER_NAME='{CONTAINER_NAME}'\n"
            f"NICE_APPROVED_IMAGE_PREFIX='{IMAGE_PREFIX}'\n"
            f"NICE_DEPLOY_STATE_DIR='{state_dir}/state'\n"
            "LEGACY_SENTINEL='unchanged'\n"
        )

    def _prepare_generic_paths(self) -> tuple[Path, Path]:
        state_dir = self.secure_base / "deployment"
        ssh_dir = self.secure_base / "ssh"
        self._root_install_directory(ssh_dir)
        return state_dir, ssh_dir / "authorized_keys"

    def _prepare_unraid_paths(self, *, wrong_target: bool = False) -> tuple[Path, Path, Path, Path]:
        state_dir = self.secure_base / "deployment"
        root_dir = self.secure_base / "root"
        boot_dir = self.secure_base / "boot"
        target = boot_dir / "config" / "ssh" / "root"
        attacker = self.secure_base / "attacker"
        self._root_install_directory(root_dir)
        self._root_install_directory(target)
        self._root_install_directory(attacker)
        symlink_target = attacker if wrong_target else target
        self._root_symlink(symlink_target, root_dir / ".ssh")
        return state_dir, root_dir / ".ssh" / "authorized_keys", target, attacker

    def test_generic_install_succeeds_and_collapses_managed_key_duplicates(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        initial = (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIUnrelated unrelated\n"
            f"old managed one {MARKER}\r\n"
            f"old managed two {MARKER}\n"
        )
        self._root_install_text(authorized_keys, initial, "0600")
        installer = self._instrument_installer()
        self._write_launcher_stub()

        first = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        enrollment_recovery = authorized_keys.parent / ".authorized_keys.nice-assistant.recovery"
        self.assertTrue(self._root_exists(enrollment_recovery))
        self.assertEqual(self._root_mode(enrollment_recovery), 0o600)
        self._root_run(["rm", "-f", "--", str(enrollment_recovery)], check=True)
        second = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(),
        )
        self.assertEqual(second.returncode, 0, second.stderr)

        installed_keys = self._root_read(authorized_keys)
        self.assertIn("unrelated\n", installed_keys)
        self.assertEqual(
            sum(1 for line in installed_keys.splitlines() if line.split() and line.split()[-1] == MARKER),
            1,
        )
        self.assertIn(f'restrict,from="{SOURCE_CIDR}",command="', installed_keys)
        self.assertEqual(self._root_mode(authorized_keys), 0o600)
        self.assertEqual(self._root_mode(authorized_keys.parent), 0o700)
        self.assertEqual(self._root_mode(state_dir / "bin" / "guard.conf"), 0o600)
        self.assertEqual(self._root_mode(state_dir / "bin" / "nice-assistant-deploy-guard"), 0o700)
        self.assertEqual(self._root_mode(state_dir / "state" / "container-definition.json"), 0o600)
        self.assertEqual(
            self._root_read(state_dir / "state" / "launcher-actions.log").splitlines(),
            ["bootstrap-guard", "inspect", "bootstrap-guard", "inspect"],
        )
        self.assertFalse(self._root_exists(state_dir / "launcher-install.json"))
        self.assertFalse(self._root_exists(state_dir / "guard.conf.pre-launcher"))
        self.assertTrue(self._root_exists(enrollment_recovery))

    def test_fresh_generic_install_creates_one_managed_key_and_empty_recovery(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        enrollment_recovery = authorized_keys.parent / ".authorized_keys.nice-assistant.recovery"
        installer = self._instrument_installer()
        self._write_launcher_stub()

        completed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        installed_keys = self._root_read(authorized_keys)
        self.assertEqual(
            sum(1 for line in installed_keys.splitlines() if line.split() and line.split()[-1] == MARKER),
            1,
        )
        self.assertEqual(len(installed_keys.splitlines()), 1)
        self.assertIn(f'restrict,from="{SOURCE_CIDR}",command="', installed_keys)
        self.assertEqual(self._root_mode(authorized_keys), 0o600)
        self.assertTrue(self._root_exists(enrollment_recovery))
        self.assertEqual(self._root_read(enrollment_recovery), "")
        recovery_contract = self._root_run(
            ["stat", "-c", "%u:%g:%a:%h:%d", str(enrollment_recovery)],
            check=True,
        ).stdout.strip()
        recovery_device = recovery_contract.rsplit(":", maxsplit=1)[-1]
        directory_device = self._root_run(
            ["stat", "-c", "%d", str(authorized_keys.parent)],
            check=True,
        ).stdout.strip()
        self.assertEqual(recovery_contract, f"0:0:600:1:{directory_device}")
        self.assertEqual(recovery_device, directory_device)
        self.assertIn("remove the root-only enrollment recovery", completed.stdout)

    def test_fresh_generic_post_rename_validation_failure_removes_authorization(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        enrollment_recovery = authorized_keys.parent / ".authorized_keys.nice-assistant.recovery"
        installer = self._instrument_installer()
        self._write_launcher_stub()

        failed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(fail_final_authorization=authorized_keys),
        )

        self.assertEqual(failed.returncode, 78, failed.stderr)
        self.assertTrue(self.final_auth_failure_sentinel.exists())
        self.assertFalse(self._root_exists(authorized_keys))
        self.assertFalse(self._root_exists(enrollment_recovery))
        self.assertIn("authorized_keys replacement did not verify", failed.stderr)
        self.assertIn(
            "the newly created authorized_keys file was removed after an enrollment failure",
            failed.stderr,
        )
        self.assertNotIn("permanent deployment launcher installed", failed.stdout)
        self.assertNotIn("automatic authorized_keys recovery was not completed", failed.stderr)

    def test_launcher_validation_failure_leaves_live_files_and_authorization_unchanged(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        install_dir = state_dir / "bin"
        data_dir = state_dir / "state"
        self._root_install_directory(install_dir)
        self._root_install_directory(data_dir)
        initial_keys = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExisting existing\n"
        initial_config = self._legacy_config(state_dir)
        initial_launcher = "#!/bin/sh\n# legacy launcher\nexit 0\n"
        self._root_install_text(authorized_keys, initial_keys, "0600")
        self._root_install_text(install_dir / "guard.conf", initial_config, "0600")
        self._root_install_text(
            install_dir / "nice-assistant-deploy-guard",
            initial_launcher,
            "0700",
        )
        original_identities = {
            path: self._root_stat_identity(path)
            for path in (
                authorized_keys,
                install_dir / "guard.conf",
                install_dir / "nice-assistant-deploy-guard",
            )
        }
        installer = self._instrument_installer()
        self._write_launcher_stub(fail_action="inspect")

        failed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(),
        )

        self.assertEqual(failed.returncode, 42, failed.stderr)
        self.assertEqual(self._root_read(authorized_keys), initial_keys)
        self.assertEqual(self._root_read(install_dir / "guard.conf"), initial_config)
        self.assertEqual(
            self._root_read(install_dir / "nice-assistant-deploy-guard"),
            initial_launcher,
        )
        self.assertEqual(
            {path: self._root_stat_identity(path) for path in original_identities},
            original_identities,
        )
        self.assertFalse(self._root_exists(state_dir / "launcher-install.json"))
        self.assertFalse(self._root_exists(state_dir / "guard.conf.pre-launcher"))

    def test_post_rename_validation_failure_restores_original_authorization(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        initial_keys = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExisting existing\n"
        self._root_install_text(authorized_keys, initial_keys, "0600")
        original_sha256 = self._root_sha256(authorized_keys)
        original_mode = self._root_mode(authorized_keys)
        enrollment_recovery = authorized_keys.parent / ".authorized_keys.nice-assistant.recovery"
        installer = self._instrument_installer()
        self._write_launcher_stub()

        failed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(fail_final_authorization=authorized_keys),
        )

        self.assertEqual(failed.returncode, 78, failed.stderr)
        self.assertIn("authorized_keys replacement did not verify", failed.stderr)
        self.assertIn("previous authorized_keys file was restored", failed.stderr)
        self.assertTrue(self.final_auth_failure_sentinel.exists())
        self.assertEqual(self._root_sha256(authorized_keys), original_sha256)
        self.assertEqual(self._root_mode(authorized_keys), original_mode)
        self.assertNotIn(MARKER, self._root_read(authorized_keys))
        self.assertFalse(self._root_exists(enrollment_recovery))

    def test_post_rename_command_failure_restores_original_authorization(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        initial_keys = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExisting existing\n"
        self._root_install_text(authorized_keys, initial_keys, "0600")
        original_sha256 = self._root_sha256(authorized_keys)
        original_mode = self._root_mode(authorized_keys)
        enrollment_recovery = authorized_keys.parent / ".authorized_keys.nice-assistant.recovery"
        installer = self._instrument_installer()
        self._write_launcher_stub()

        failed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(fail_after_final_rename=authorized_keys),
        )

        self.assertEqual(failed.returncode, 93, failed.stderr)
        self.assertIn("previous authorized_keys file was restored", failed.stderr)
        self.assertTrue(self.final_rename_failure_sentinel.exists())
        self.assertEqual(self._root_sha256(authorized_keys), original_sha256)
        self.assertEqual(self._root_mode(authorized_keys), original_mode)
        self.assertNotIn(MARKER, self._root_read(authorized_keys))
        self.assertFalse(self._root_exists(enrollment_recovery))

    def test_term_before_live_switch_exits_143_without_authorizing(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        initial_keys = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExisting existing\n"
        self._root_install_text(authorized_keys, initial_keys, "0600")
        original_identity = self._root_stat_identity(authorized_keys)
        original_sha256 = self._root_sha256(authorized_keys)
        enrollment_recovery = authorized_keys.parent / ".authorized_keys.nice-assistant.recovery"
        installer = self._instrument_installer()
        self._write_launcher_stub(term_action="inspect")

        terminated = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(),
        )

        self.assertEqual(terminated.returncode, 143, terminated.stderr)
        self.assertTrue(self._root_exists(state_dir / "state" / "term-sent"))
        self.assertEqual(self._root_sha256(authorized_keys), original_sha256)
        self.assertEqual(self._root_stat_identity(authorized_keys), original_identity)
        self.assertNotIn(MARKER, self._root_read(authorized_keys))
        self.assertFalse(self._root_exists(enrollment_recovery))
        self.assertFalse(self._root_exists(state_dir / "bin" / "guard.conf"))
        self.assertFalse(self._root_exists(state_dir / "bin" / "nice-assistant-deploy-guard"))
        self.assertFalse(self._root_exists(state_dir / "launcher-install.json"))
        self.assertEqual(
            self._root_read(state_dir / "state" / "launcher-actions.log").splitlines(),
            ["bootstrap-guard", "inspect"],
        )

    def test_term_after_final_verification_rolls_back_before_commit(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        initial_keys = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExisting existing\n"
        self._root_install_text(authorized_keys, initial_keys, "0600")
        original_sha256 = self._root_sha256(authorized_keys)
        original_mode = self._root_mode(authorized_keys)
        enrollment_recovery = authorized_keys.parent / ".authorized_keys.nice-assistant.recovery"
        installer = self._instrument_installer()
        self._write_launcher_stub()

        terminated = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(term_at_precommit_journal=state_dir / "launcher-install.json"),
        )

        self.assertEqual(terminated.returncode, 143, terminated.stderr)
        self.assertTrue(self.precommit_term_sentinel.exists())
        self.assertEqual(self._root_sha256(authorized_keys), original_sha256)
        self.assertEqual(self._root_mode(authorized_keys), original_mode)
        self.assertNotIn(MARKER, self._root_read(authorized_keys))
        self.assertFalse(self._root_exists(enrollment_recovery))
        self.assertFalse(self._root_exists(state_dir / "launcher-install.json"))
        self.assertNotIn("permanent deployment launcher installed", terminated.stdout)

    def test_unexpected_post_rename_content_preserves_live_file_and_recovery(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        initial_keys = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExisting existing\n"
        unexpected_keys = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIUnknown unexpected\n"
        unexpected_source = self.work / "unexpected-authorized-keys"
        unexpected_source.write_text(unexpected_keys, encoding="utf-8")
        self._root_install_text(authorized_keys, initial_keys, "0600")
        original_sha256 = self._root_sha256(authorized_keys)
        unexpected_sha256 = self._root_sha256(unexpected_source)
        enrollment_recovery = authorized_keys.parent / ".authorized_keys.nice-assistant.recovery"
        installer = self._instrument_installer()
        self._write_launcher_stub()

        failed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(ambiguous_authorization=(authorized_keys, unexpected_source)),
        )

        self.assertEqual(failed.returncode, 94, failed.stderr)
        self.assertTrue(self.ambiguous_auth_sentinel.exists())
        self.assertEqual(self._root_sha256(authorized_keys), unexpected_sha256)
        self.assertEqual(self._root_mode(authorized_keys), 0o600)
        self.assertTrue(self._root_exists(enrollment_recovery))
        self.assertEqual(self._root_sha256(enrollment_recovery), original_sha256)
        self.assertEqual(self._root_mode(enrollment_recovery), 0o600)
        self.assertIn("authorized_keys changed after enrollment", failed.stderr)
        self.assertIn("automatic authorized_keys recovery was not completed", failed.stderr)
        self.assertNotIn("was restored after an enrollment failure", failed.stderr)
        self.assertNotIn("remained active after an enrollment failure", failed.stderr)
        self.assertTrue(self._root_exists(state_dir / "launcher-install.json"))

    def test_recovery_sync_failure_is_not_reported_as_success(self) -> None:
        state_dir, authorized_keys = self._prepare_generic_paths()
        initial_keys = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExisting existing\n"
        self._root_install_text(authorized_keys, initial_keys, "0600")
        original_sha256 = self._root_sha256(authorized_keys)
        enrollment_recovery = authorized_keys.parent / ".authorized_keys.nice-assistant.recovery"
        installer = self._instrument_installer()
        self._write_launcher_stub()

        failed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=authorized_keys,
            environment=self._installer_environment(
                fail_after_final_rename=authorized_keys,
                fail_recovery_sync=True,
            ),
        )

        self.assertEqual(failed.returncode, 93, failed.stderr)
        self.assertTrue(self.final_rename_failure_sentinel.exists())
        self.assertTrue(self.recovery_sync_failure_sentinel.exists())
        self.assertEqual(self._root_sha256(authorized_keys), original_sha256)
        self.assertEqual(self._root_mode(authorized_keys), 0o600)
        self.assertTrue(self._root_exists(enrollment_recovery))
        self.assertEqual(self._root_sha256(enrollment_recovery), original_sha256)
        self.assertEqual(self._root_mode(enrollment_recovery), 0o600)
        self.assertNotIn(MARKER, self._root_read(authorized_keys))
        self.assertIn("restored authorized_keys did not flush", failed.stderr)
        self.assertIn("automatic authorized_keys recovery was not completed", failed.stderr)
        self.assertNotIn("was restored after an enrollment failure", failed.stderr)
        self.assertNotIn("remained active after an enrollment failure", failed.stderr)

    def test_stock_unraid_layout_succeeds_without_touching_host_root_or_boot(self) -> None:
        state_dir, requested_keys, target, _attacker = self._prepare_unraid_paths()
        initial = (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIUnrelated unrelated\n"
            f"old managed one {MARKER}\r\n"
            f"old managed two {MARKER}\n"
        )
        self._root_install_text(target / "authorized_keys", initial, "0600")
        installer = self._instrument_installer(unraid_root=self.secure_base)
        self._write_launcher_stub()
        environment = self._installer_environment(
            unraid_boot=self.secure_base / "boot",
            unraid_target=target,
        )

        completed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=requested_keys,
            environment=environment,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        installed_keys = self._root_read(target / "authorized_keys")
        self.assertIn("unrelated\n", installed_keys)
        self.assertEqual(
            sum(1 for line in installed_keys.splitlines() if line.split() and line.split()[-1] == MARKER),
            1,
        )
        self.assertEqual(self._root_read(requested_keys), installed_keys)
        self.assertEqual(self._root_mode(target / "authorized_keys"), 0o600)

    def test_stock_unraid_layout_rejects_a_different_symlink_target(self) -> None:
        state_dir, requested_keys, target, attacker = self._prepare_unraid_paths(wrong_target=True)
        trusted_keys = "trusted target remains unchanged\n"
        attacker_keys = "attacker target remains unchanged\n"
        self._root_install_text(target / "authorized_keys", trusted_keys, "0600")
        self._root_install_text(attacker / "authorized_keys", attacker_keys, "0600")
        installer = self._instrument_installer(unraid_root=self.secure_base)
        self._write_launcher_stub()
        environment = self._installer_environment(
            unraid_boot=self.secure_base / "boot",
            unraid_target=target,
        )

        completed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=requested_keys,
            environment=environment,
        )

        self.assertEqual(completed.returncode, 78, completed.stderr)
        self.assertIn("not the supported Unraid persistence path", completed.stderr)
        self.assertEqual(self._root_read(target / "authorized_keys"), trusted_keys)
        self.assertEqual(self._root_read(attacker / "authorized_keys"), attacker_keys)
        self.assertFalse(self._root_exists(state_dir / "bin" / "guard.conf"))

    def test_stock_unraid_layout_revalidates_after_launcher_switch(self) -> None:
        state_dir, requested_keys, target, attacker = self._prepare_unraid_paths()
        trusted_keys = "trusted target remains unchanged\n"
        attacker_keys = "attacker target remains unchanged\n"
        self._root_install_text(target / "authorized_keys", trusted_keys, "0600")
        self._root_install_text(attacker / "authorized_keys", attacker_keys, "0600")
        installer = self._instrument_installer(unraid_root=self.secure_base)
        self._write_launcher_stub()
        environment = self._installer_environment(
            unraid_boot=self.secure_base / "boot",
            unraid_target=target,
            state_dir=state_dir,
            attacker=attacker,
        )

        completed = self._run_installer(
            installer=installer,
            state_dir=state_dir,
            authorized_keys=requested_keys,
            environment=environment,
        )

        self.assertEqual(completed.returncode, 78, completed.stderr)
        self.assertIn("layout changed before authorization", completed.stderr)
        self.assertEqual(self._root_read(attacker / "authorized_keys"), attacker_keys)
        self.assertEqual(
            self._root_read(Path(f"{target}.validated") / "authorized_keys"),
            trusted_keys,
        )
        self.assertTrue(self._root_exists(state_dir / ".race-fired"))


if __name__ == "__main__":
    unittest.main()
