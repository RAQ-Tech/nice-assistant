import unittest

from scripts.audit_public_repo import audit_text


class PublicRepositoryAuditTests(unittest.TestCase):
    def test_private_deployment_values_are_reported_without_echoing_them(self):
        value = "private-host-value"
        findings = audit_text("docs/example.md", f"Host: {value}", [value])
        self.assertEqual([finding.kind for finding in findings], ["known-private-value-1"])
        self.assertNotIn(value, findings[0].kind)
        self.assertEqual(
            [finding.kind for finding in audit_text("tests/test_public_repo_audit.py", value, [value])],
            ["known-private-value-1"],
        )

    def test_high_risk_public_content_is_rejected(self):
        text = "\n".join(
            (
                "server=http://192.168.50.20:9000",
                "path=C:\\Users\\operator\\project",
                "email=operator@private-company.invalid",
                "backup=nice-assistant-snapshot-20260102_030405-deadbeef.zip",
                "token=ghp_abcdefghijklmnopqrstuvwxyz123456",
                "url=http://admin:password@server.lan",
            )
        )
        kinds = {finding.kind for finding in audit_text("docs/example.md", text)}
        self.assertEqual(
            kinds,
            {
                "private-address",
                "personal-home-path",
                "non-example-email",
                "concrete-backup-name",
                "credential-like-token",
                "credential-bearing-url",
            },
        )

    def test_required_network_constants_and_explicit_fixtures_are_allowed(self):
        self.assertEqual(audit_text("app/security.py", 'network = "192.168.0.0/16"'), [])
        self.assertEqual(
            audit_text("tests/test_production_hardening.py", 'url = "http://100.64.0.10:8880"'),
            [],
        )
        self.assertEqual(
            audit_text("tests/example.py", 'token = "sk-test-DO-NOT-USE-000000000000"'),
            [],
        )


if __name__ == "__main__":
    unittest.main()
