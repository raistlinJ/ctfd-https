"""Exercise installer input handling without a Proxmox host or root access."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts/provision/proxmox/install-ctfd.sh"


class ProxmoxInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.key = cls.root / "admin"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(cls.key)],
            check=True,
        )
        cls.env = {key: value for key, value in os.environ.items() if not key.startswith("CTFD_")}
        cls.env["PATH"] = f"{cls.root}:{os.environ['PATH']}"
        # Dry-run must never touch Proxmox or download anything.
        for name in ("qm", "pvesh", "pvesm", "curl"):
            mock = cls.root / name
            mock.write_text(f"#!/bin/sh\ntouch '{cls.root / 'unexpected-command'}'\nexit 99\n")
            mock.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_installer(self, *args, public_key=True, dry_run=True, environment=None):
        key_args = ["--ssh-public-key", str(self.key) + ".pub"] if public_key else []
        result = subprocess.run(
            ["bash", str(INSTALLER), *(["--dry-run"] if dry_run else []), *key_args, *args],
            env=dict(self.env, **(environment or {})), text=True, capture_output=True,
        )
        self.assertFalse((self.root / "unexpected-command").exists())
        return result

    def test_default_dry_run_has_requested_resources(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in ("Debian 12 minimal", "4096 MiB RAM", "40 GiB disk", "IPv4: dhcp"):
            self.assertIn(value, result.stdout)

    def test_static_network(self):
        result = self.run_installer("--ip", "192.0.2.10/24", "--gateway", "192.0.2.1", "--dns", "192.0.2.53")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("192.0.2.10/24", result.stdout)

    def test_rejects_invalid_networks(self):
        for args in (
            ("--ip", "192.0.2.10/24"),
            ("--gateway", "192.0.2.1"),
            ("--ip", "192.0.2.10/24", "--gateway", "198.51.100.1"),
            ("--ip", "192.0.2.10/24", "--gateway", "192.0.2.10"),
            ("--dns", "invalid"),
        ):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_installer(*args).returncode, 0)

    def test_rejects_missing_and_private_key(self):
        self.assertNotEqual(self.run_installer(public_key=False).returncode, 0)
        result = self.run_installer("--ssh-public-key", str(self.key), public_key=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public key", result.stderr)

    def test_rejects_invalid_options(self):
        for args in (
            ("--vmid", "99"), ("--cores", "0"), ("--wait-minutes", "-1"),
            ("--name", "ctfd;echo bad"), ("--bridge", "vmbr0,tag=123"),
            ("--repo-url", "file:///tmp/repo"), ("--repo-ref", "--upload-pack=bad"),
            ("--does-not-exist", "value"), ("--vmid",),
        ):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_installer(*args).returncode, 0)

    def test_help_does_not_require_key(self):
        result = self.run_installer("--help", public_key=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)

    def write_config(self, content):
        config = self.root / "installer config.conf"
        config.write_bytes(content.encode())
        return str(config)

    def test_config_only_with_quotes_crlf_and_no_final_newline(self):
        key_with_spaces = self.root / "admin public key.pub"
        key_with_spaces.write_bytes(Path(str(self.key) + ".pub").read_bytes())
        config = self.write_config(
            "  # Comment\r\n\r\n vmid = 9600\r\n name = 'ctfd-config'\r\n"
            f' ssh_public_key = "{key_with_spaces}"\r\n'
            "storage=vmdata\r\nsnippet_storage=snippets\r\nbridge=vmbr1\r\n"
            "cores=4\r\nip=192.0.2.10/24\r\ngateway=192.0.2.1\r\ndns=192.0.2.53\r\n"
            "repo_url=https://example.com/ctfd.git\r\nrepo_ref=event\r\n"
            "wait_minutes=60\r\nno_wait=YES\r\ndry_run=On"
        )
        result = self.run_installer("--config", config, public_key=False, dry_run=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in ("VM 9600 (ctfd-config)", "4 cores", "Storage: vmdata", "snippets: snippets",
                      "bridge: vmbr1", "IPv4: 192.0.2.10/24", "https://example.com/ctfd.git (event)"):
            self.assertIn(value, result.stdout)

    def test_config_environment_cli_precedence_in_either_order(self):
        config = self.write_config("vmid=9600\nname=config-name\ndry_run=false\n")
        result = self.run_installer("--config", config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VM 9600 (config-name)", result.stdout)
        result = self.run_installer("--config", config, environment={"CTFD_VMID": "9601"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VM 9601 (config-name)", result.stdout)
        for args in (("--vmid", "9602", "--config", config), ("--config=" + config, "--vmid", "9602")):
            with self.subTest(args=args):
                result = self.run_installer(*args, environment={"CTFD_VMID": "9601"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("VM 9602 (config-name)", result.stdout)

    def test_environment_can_supply_key_and_boolean(self):
        result = self.run_installer(public_key=False, dry_run=False, environment={
            "CTFD_SSH_PUBLIC_KEY": str(self.key) + ".pub", "CTFD_DRY_RUN": "TRUE",
        })
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_config_does_not_evaluate_shell_values(self):
        marker = self.root / "must-not-execute"
        literal = f"$(touch '{marker}')`touch '{marker}'`$HOME#literal=value"
        config = self.write_config(f'repo_ref="{literal}"\n')
        result = self.run_installer("--config", config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(literal, result.stdout)
        self.assertFalse(marker.exists())

    def test_rejects_config_syntax_with_line_number(self):
        for line, message in (
            ("unknown=value", "unknown config key"),
            ("VMID=9600", "invalid config key"),
            ("vmid 9600", "expected key=value"),
            ('name="ctfd', "unmatched quote"),
            ("name='", "unmatched quote"),
            ("dry_run=maybe", "must be true or false"),
            ("no_wait=", "must be true or false"),
        ):
            with self.subTest(line=line):
                config = self.write_config("# header\n" + line + "\n")
                result = self.run_installer("--config", config)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(config + ":2:", result.stderr)
                self.assertIn(message, result.stderr)

    def test_config_values_receive_normal_validation(self):
        for line in ("cores=0", "repo_ref=", "ip=192.0.2.10/24", "vmid=99"):
            with self.subTest(line=line):
                result = self.run_installer("--config", self.write_config(line))
                self.assertNotEqual(result.returncode, 0)

    def test_rejects_missing_or_multiple_config_files(self):
        config = self.write_config("vmid=9600\n")
        for args in (
            ("--config",), ("--config=",), ("--config", "--dry-run"),
            ("--config", str(self.root / "missing.conf")),
            ("--config", config, "--config=" + config),
        ):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_installer(*args).returncode, 0)

    def test_example_config_is_usable_with_key_override(self):
        example = INSTALLER.parent / "ctfd.conf.example"
        result = self.run_installer("--config", str(example))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VM 9501 (ctfd)", result.stdout)

    def test_repeated_keys_use_last_value(self):
        result = self.run_installer("--config", self.write_config("vmid=9600\nvmid=9601\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VM 9601", result.stdout)


if __name__ == "__main__":
    unittest.main()
