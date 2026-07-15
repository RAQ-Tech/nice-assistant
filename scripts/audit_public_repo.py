#!/usr/bin/env python3
"""Fail when tracked files contain likely secrets or private deployment data."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import subprocess

from PIL import ExifTags, Image, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]

PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)
SAFE_NETWORK_LITERALS = {
    ("app/security.py", "10.0.0.0"),
    ("app/security.py", "172.16.0.0"),
    ("app/security.py", "192.168.0.0"),
    ("app/security.py", "100.64.0.0"),
    ("tests/test_production_hardening.py", "100.64.0.10"),
}
SAFE_CREDENTIAL_URLS = {
    ("tests/test_identity_provider.py", "http://user:password@verifier.lan"),
}
DUMMY_TOKEN_MARKERS = ("test", "smoke", "contract", "no-real", "not-a-real", "fake", "unit")
SAFE_EMAIL_DOMAINS = {
    "asdf.com",
    "example.com",
    "example.net",
    "example.org",
    "users.noreply.github.com",
    "verifier.lan",
}

IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")
TOKEN_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_-]{20,})\b",
    re.IGNORECASE,
)
USER_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\Users\\[^\\\s]+|/home/[^/\s]+)", re.IGNORECASE)
CREDENTIAL_URL_PATTERN = re.compile(r"https?://[^\s/:]+:[^\s/@]+@[^\s]+", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
BACKUP_PATTERN = re.compile(r"nice-assistant-snapshot-\d{8}_\d{6}-[a-f0-9]{8}\.zip", re.IGNORECASE)
SENSITIVE_IMAGE_METADATA = {
    "artist",
    "author",
    "comment",
    "copyright",
    "description",
    "documentname",
    "hostcomputer",
    "imagedescription",
    "parameters",
    "prompt",
    "usercomment",
    "xml:com.adobe.xmp",
}
AUDIT_PATTERN_FIXTURES = {
    "scripts/audit_public_repo.py",
    "tests/test_public_repo_audit.py",
}


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    kind: str


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def load_local_private_values(path: Path) -> list[str]:
    if not path.exists():
        return []
    values = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if value and not value.startswith("#"):
            values.append(value)
    return values


def audit_text(path: str, text: str, private_values: list[str] | None = None) -> list[Finding]:
    findings: set[Finding] = set()
    private_values = private_values or []
    folded = text.casefold()

    for index, value in enumerate(private_values, start=1):
        offset = folded.find(value.casefold())
        if offset >= 0:
            findings.add(Finding(path, _line_number(text, offset), f"known-private-value-{index}"))

    if path in AUDIT_PATTERN_FIXTURES:
        return sorted(findings)

    for match in PRIVATE_KEY_PATTERN.finditer(text):
        findings.add(Finding(path, _line_number(text, match.start()), "private-key"))

    for match in TOKEN_PATTERN.finditer(text):
        token = match.group(0).casefold()
        fixture = path.startswith(("tests/", "scripts/")) and any(marker in token for marker in DUMMY_TOKEN_MARKERS)
        if not fixture:
            findings.add(Finding(path, _line_number(text, match.start()), "credential-like-token"))

    for match in USER_PATH_PATTERN.finditer(text):
        findings.add(Finding(path, _line_number(text, match.start()), "personal-home-path"))

    for match in CREDENTIAL_URL_PATTERN.finditer(text):
        candidate = match.group(0).rstrip("\"',)")
        if (path, candidate) not in SAFE_CREDENTIAL_URLS:
            findings.add(Finding(path, _line_number(text, match.start()), "credential-bearing-url"))

    for match in EMAIL_PATTERN.finditer(text):
        domain = match.group(1).casefold()
        if domain not in SAFE_EMAIL_DOMAINS:
            findings.add(Finding(path, _line_number(text, match.start()), "non-example-email"))

    for match in BACKUP_PATTERN.finditer(text):
        fixture = path.startswith("tests/") and match.group(0).casefold().endswith("-deadbeef.zip")
        if not fixture:
            findings.add(Finding(path, _line_number(text, match.start()), "concrete-backup-name"))

    for match in IPV4_PATTERN.finditer(text):
        value = match.group(0)
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.is_loopback or not any(address in network for network in PRIVATE_NETWORKS):
            continue
        if (path, value) not in SAFE_NETWORK_LITERALS:
            findings.add(Finding(path, _line_number(text, match.start()), "private-address"))

    return sorted(findings)


def audit_image(path: Path, relative_path: str) -> list[Finding]:
    try:
        with Image.open(path) as image:
            metadata_names = {str(key).casefold() for key in image.info}
            metadata_names.update(
                str(ExifTags.TAGS.get(key, key)).casefold()
                for key, value in image.getexif().items()
                if value not in (None, "", b"")
            )
    except (OSError, UnidentifiedImageError):
        return []
    if metadata_names & SENSITIVE_IMAGE_METADATA:
        return [Finding(relative_path, 1, "sensitive-image-metadata")]
    return []


def public_candidate_files(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]


def audit_repository(root: Path, private_values: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for relative_path in public_candidate_files(root):
        path = root / relative_path
        content = path.read_bytes()
        if b"\0" in content[:8192]:
            findings.extend(audit_image(path, relative_path))
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(audit_text(relative_path, text, private_values))
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--local-values", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    local_values_path = args.local_values or root / ".local" / "public-repo-private-values.txt"
    private_values = load_local_private_values(local_values_path)
    findings = audit_repository(root, private_values)
    if findings:
        print("Public repository privacy audit failed:")
        for finding in findings:
            print(f"  {finding.path}:{finding.line}: {finding.kind}")
        print("Matched content is intentionally not printed. Move private facts to .local/ or use public placeholders.")
        return 1

    local_note = f" with {len(private_values)} local private values" if private_values else ""
    print(f"Public repository privacy audit passed{local_note}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
