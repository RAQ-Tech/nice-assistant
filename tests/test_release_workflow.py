from pathlib import Path
import unittest


class ReleaseWorkflowTests(unittest.TestCase):
    def test_publish_metadata_is_deterministic_and_keeps_required_provenance(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-ghcr.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("docker/metadata-action", workflow)
        self.assertIn('image="ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/nice-assistant"', workflow)
        self.assertIn('${image}:sha-${GITHUB_SHA:0:7}', workflow)
        self.assertIn('${image}:main', workflow)
        self.assertIn('${image}:latest', workflow)
        self.assertIn('${image}:${GITHUB_REF_NAME}', workflow)
        self.assertIn("org.opencontainers.image.revision=${GITHUB_SHA}", workflow)
        self.assertIn("org.opencontainers.image.source=${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}", workflow)


if __name__ == "__main__":
    unittest.main()
