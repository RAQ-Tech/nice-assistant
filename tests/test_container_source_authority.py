import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContainerSourceAuthorityTests(unittest.TestCase):
    def test_container_uses_image_code_unless_explicit_development_sync_is_enabled(self):
        entrypoint = (ROOT / "entrypoint.sh").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()
        container_smoke = (ROOT / "scripts" / "container_smoke_check.ps1").read_text()

        self.assertIn("NICE_ASSISTANT_DEVELOPMENT_PROJECT_SYNC:-0", entrypoint)
        self.assertIn("cd /opt/nice-assistant", entrypoint)
        self.assertIn('if [[ "${DEVELOPMENT_PROJECT_SYNC}" == "1" ]]', entrypoint)
        self.assertIn("ENV NICE_ASSISTANT_DEVELOPMENT_PROJECT_SYNC=0", dockerfile)
        self.assertNotIn("SYNC_PROJECT_ON_START=1", dockerfile)
        self.assertNotIn("PROJECT_ROOT=/data/project", dockerfile)
        self.assertNotIn('rm -rf "${PROJECT_ROOT}"', entrypoint)
        self.assertIn("-e 'NICE_ASSISTANT_DEVELOPMENT_PROJECT_SYNC=0'", container_smoke)
        self.assertIn("-e 'PROJECT_ROOT=/data/project'", container_smoke)
        self.assertIn("-e 'SYNC_PROJECT_ON_START=1'", container_smoke)
        self.assertIn("applicationSourceRoot.Trim() -ne '/opt/nice-assistant'", container_smoke)
        self.assertIn("Join-Path $dataPath 'project'", container_smoke)


if __name__ == "__main__":
    unittest.main()
