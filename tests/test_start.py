"""Check launch options without starting or modifying a deployment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://github.com/raistlinJ/CTFd.git#feature/retract-solves"


class StartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "deployment with spaces"
        self.root.mkdir()
        for name in ("start.sh", "docker-compose-https.yml", "docker-compose-source.yml"):
            shutil.copy2(ROOT / name, self.root / name)
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$PWD" "${CTFD_SOURCE_URL:-}" "$@" > "$START_LOG"\n'
        )
        docker.chmod(0o755)
        self.log = self.root / "docker-args"
        self.env = {key: value for key, value in os.environ.items() if key != "CTFD_SOURCE_URL"}
        self.env.update(PATH=f"{self.bin}:{os.environ['PATH']}", START_LOG=str(self.log))

    def start(self, *args, environment=None):
        return subprocess.run(
            ["sh", str(self.root / "start.sh"), *args],
            cwd=self.temp.name, env=dict(self.env, **(environment or {})),
            capture_output=True, text=True,
        )

    def test_default_keeps_official_image_and_passes_compose_arguments(self):
        result = self.start("--force-recreate", "ctfd")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            str(self.root), "", "compose", "-f", "docker-compose-https.yml",
            "up", "-d", "--force-recreate", "ctfd",
        ])
        secret = self.root / "secrets/.env"
        original = secret.read_bytes()
        self.assertRegex(original.decode(), r"^SECRET_KEY=[0-9a-f]{64}\n$")
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual(secret.read_bytes(), original)

    def test_source_builds_requested_ref_with_override(self):
        result = self.start("--source-url", SOURCE, "--no-deps", "ctfd")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            str(self.root), SOURCE, "compose", "-f", "docker-compose-https.yml",
            "-f", "docker-compose-source.yml", "up", "-d", "--build", "--no-deps", "ctfd",
        ])

    def test_source_environment_and_cli_precedence(self):
        result = self.start(environment={"CTFD_SOURCE_URL": SOURCE})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines()[1], SOURCE)
        other = "https://example.com/CTFd.git#v1"
        result = self.start("--source-url=" + other, environment={"CTFD_SOURCE_URL": SOURCE})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines()[1], other)

    def test_invalid_source_never_starts_compose_or_creates_secret(self):
        for args in (
            ("--source-url",), ("--source-url=",), ("--source-url", ""),
            ("--source-url", "--build"), ("--source-url", "file:///tmp/repo"),
            ("--source-url", "http://example.com/CTFd.git"),
            ("--source-url", "https://example.com/has space"),
            ("--source-url", "https://example.com/repo\nother"),
        ):
            with self.subTest(args=args):
                result = self.start(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())
                self.assertFalse((self.root / "secrets").exists())

    def test_help_is_read_only(self):
        result = self.start("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--source-url", result.stdout)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / "secrets").exists())

    def test_invalid_existing_secret_is_preserved(self):
        (self.root / "secrets").mkdir()
        secret = self.root / "secrets/.env"
        secret.write_text("SECRET_KEY=\n")
        result = self.start("--source-url", SOURCE)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(secret.read_text(), "SECRET_KEY=\n")
        self.assertFalse(self.log.exists())

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose is not installed")
    def test_real_compose_merge_uses_source_themes_and_preserves_storage(self):
        self.assertEqual(self.start().returncode, 0)
        command = ["docker", "compose", "-f", "docker-compose-https.yml"]
        environment = dict(os.environ, CTFD_SOURCE_URL=SOURCE)
        base = subprocess.run(
            [*command, "config", "--format", "json"], cwd=self.root,
            env=environment, text=True, capture_output=True, check=True,
        )
        custom = subprocess.run(
            [*command, "-f", "docker-compose-source.yml", "config", "--format", "json"],
            cwd=self.root, env=environment, text=True, capture_output=True, check=True,
        )
        base, custom = json.loads(base.stdout), json.loads(custom.stdout)
        app = custom["services"]["ctfd"]
        self.assertEqual(app["build"]["context"], SOURCE)
        self.assertEqual(app["image"], "ctfd-https-source:local")
        self.assertEqual(app["pull_policy"], "never")
        targets = {mount["target"] for mount in app["volumes"]}
        self.assertEqual(targets, {"/var/log/ctfd", "/var/uploads"})
        self.assertEqual(app["environment"]["UPLOAD_FOLDER"], "/var/uploads")
        self.assertEqual(app["environment"]["LOG_FOLDER"], "/var/log/ctfd")
        self.assertEqual(app["environment"]["SECRET_KEY"], base["services"]["ctfd"]["environment"]["SECRET_KEY"])
        self.assertEqual(app["depends_on"]["source-permissions"]["condition"], "service_completed_successfully")
        for service in ("db", "cache", "nginx", "cert-init"):
            self.assertEqual(custom["services"][service], base["services"][service])


if __name__ == "__main__":
    unittest.main()
