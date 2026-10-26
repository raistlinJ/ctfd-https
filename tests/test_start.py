"""Validate configuration and launch behavior without starting containers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://github.com/raistlinJ/CTFd.git#feature/retract-solves'


class StartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = (Path(self.temp.name) / 'deployment with spaces').resolve()
        self.root.mkdir()
        for name in ('start.sh', 'docker-compose-https.yml', '.env.example'):
            shutil.copy2(ROOT / name, self.root / name)
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        docker = self.bin / 'docker'
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "${CTFD_SOURCE_URL:-}" "$@" > "$START_LOG"\n')
        docker.chmod(0o755)
        self.log = self.root / 'docker-args'
        self.env = {key: value for key, value in os.environ.items() if not key.startswith(('CTFD_', 'COMPOSE_'))}
        self.env.update(PATH=f'{self.bin}:{os.environ["PATH"]}', START_LOG=str(self.log))

    def start(self, *args, environment=None):
        return subprocess.run(['sh', str(self.root / 'start.sh'), *args], cwd=self.temp.name,
                              env=dict(self.env, **(environment or {})), capture_output=True, text=True)

    def compose_config(self):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('CTFD_', 'COMPOSE_', 'MYSQL_', 'HTTP_', 'HTTPS_', 'NGINX_', 'MARIADB_', 'REDIS_'))}
        result = subprocess.run(['docker', 'compose', 'config', '--format', 'json'], cwd=self.root,
                                env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_start_uses_one_compose_file_and_preserves_configuration_and_key(self):
        result = self.start('--force-recreate', 'ctfd')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            str(self.root), '', 'compose', '--env-file', '.env', '-f', 'docker-compose-https.yml',
            'up', '-d', '--build', '--force-recreate', 'ctfd'])
        secret = self.root / 'secrets/.env'
        original = secret.read_bytes()
        self.assertRegex(original.decode(), r'^SECRET_KEY=[0-9a-f]{64}\n$')
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        config = self.root / '.env'
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        config.write_text(config.read_text().replace('HTTP_PORT=80', 'HTTP_PORT=8080'))
        configured = config.read_bytes()
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual(secret.read_bytes(), original)
        self.assertEqual(config.read_bytes(), configured)

    def test_initialize_prepares_files_without_docker(self):
        result = self.start('--init')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '.env').exists())
        self.assertTrue((self.root / 'secrets/.env').exists())
        self.assertFalse(self.log.exists())

    def test_source_selection_persists_and_cli_overrides_environment(self):
        self.assertEqual(self.start('--init').returncode, 0)
        original_key = (self.root / 'secrets/.env').read_bytes()
        other = 'https://example.com/CTFd.git#other'
        result = self.start('--source-url=' + other, environment={'CTFD_SOURCE_URL': SOURCE})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines()[1], other)
        self.assertIn("CTFD_SOURCE_URL='" + other + "'", (self.root / '.env').read_text())
        self.assertEqual((self.root / '.env').read_text().count('CTFD_SOURCE_URL='), 1)
        self.assertEqual(self.start().returncode, 0)
        self.assertIn(other, (self.root / '.env').read_text())
        self.assertEqual((self.root / 'secrets/.env').read_bytes(), original_key)

    def test_environment_source_is_saved_for_future_starts(self):
        other = 'https://example.com/CTFd.git#persistent'
        self.assertEqual(self.start('--init', environment={'CTFD_SOURCE_URL': other}).returncode, 0)
        self.assertIn(other, (self.root / '.env').read_text())
        self.assertFalse(self.log.exists())

    def test_invalid_source_never_changes_configuration_or_calls_docker(self):
        for args in (('--source-url',), ('--source-url=',), ('--source-url', ''),
                     ('--source-url', '--build'), ('--source-url', 'file:///tmp/repo'),
                     ('--source-url', 'http://example.com/CTFd.git'),
                     ('--source-url', 'https://example.com/has space'),
                     ('--source-url', 'https://example.com/repo\nother')):
            with self.subTest(args=args):
                result = self.start(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())
                self.assertFalse((self.root / '.env').exists())
                self.assertFalse((self.root / 'secrets').exists())

    def test_help_is_read_only(self):
        result = self.start('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--init', result.stdout)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / '.env').exists())

    def test_existing_signing_key_is_reused(self):
        (self.root / 'secrets').mkdir()
        secret = self.root / 'secrets/.env'
        secret.write_text('SECRET_KEY=existing-signing-key\n')
        self.assertEqual(self.start('--init').returncode, 0)
        self.assertEqual(secret.read_text(), 'SECRET_KEY=existing-signing-key\n')

    def test_invalid_existing_secret_is_preserved(self):
        (self.root / 'secrets').mkdir()
        secret = self.root / 'secrets/.env'
        secret.write_text('SECRET_KEY=\n')
        result = self.start('--init')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(secret.read_text(), 'SECRET_KEY=\n')
        self.assertFalse(self.log.exists())

    @unittest.skipUnless(shutil.which('docker'), 'Docker Compose is not installed')
    def test_one_compose_file_defaults_to_fork_and_preserves_persistent_paths(self):
        self.assertEqual(self.start('--init').returncode, 0)
        config = self.compose_config()
        self.assertEqual(set(config['services']), {'ctfd', 'nginx', 'db', 'cache', 'cert-init', 'source-permissions'})
        app = config['services']['ctfd']
        self.assertEqual(app['build']['context'], SOURCE)
        self.assertEqual(app['pull_policy'], 'build')
        self.assertEqual(app['image'], 'ctfd-https-source:local')
        mounts = {mount['target']: mount['source'] for mount in app['volumes']}
        self.assertEqual(mounts, {'/var/log/ctfd': str(self.root / 'ctfd/logs'), '/var/uploads': str(self.root / 'ctfd/uploads')})
        self.assertEqual(config['services']['db']['volumes'][0]['source'], str(self.root / 'db'))
        proxy_mounts = {item['target']: item['source'] for item in config['services']['nginx']['volumes']}
        self.assertEqual(proxy_mounts['/etc/nginx/certs'], str(self.root / 'certs'))
        stored_key = (self.root / 'secrets/.env').read_text().strip().split('=', 1)[1]
        self.assertEqual(app['environment']['SECRET_KEY'], stored_key)
        self.assertEqual(app['depends_on']['source-permissions']['condition'], 'service_completed_successfully')
        helper = config['services']['source-permissions']
        self.assertEqual(helper['network_mode'], 'none')
        self.assertNotIn('networks', helper)
        for service in ('ctfd', 'db', 'cache', 'nginx'):
            self.assertEqual(set(config['services'][service]['networks']), {'ctfd-net'})

    @unittest.skipUnless(shutil.which('docker'), 'Docker Compose is not installed')
    def test_env_controls_ports_workers_images_and_source_without_shell_exports(self):
        self.assertEqual(self.start('--init').returncode, 0)
        other = 'https://example.com/CTFd.git#v1'
        with (self.root / '.env').open('a') as config:
            config.write('\nHTTP_PORT=8080\nHTTPS_PORT=8443\nCTFD_WORKERS=2\nNGINX_IMAGE=nginx:test\n')
            config.write("CTFD_SOURCE_URL='" + other + "'\n")
        config = self.compose_config()
        self.assertEqual(config['services']['ctfd']['build']['context'], other)
        self.assertEqual(config['services']['ctfd']['environment']['WORKERS'], '2')
        self.assertEqual(config['services']['nginx']['image'], 'nginx:test')
        self.assertEqual({item['published'] for item in config['services']['nginx']['ports']}, {'8080', '8443'})

    @unittest.skipUnless(shutil.which('docker'), 'Docker Compose is not installed')
    def test_saved_source_is_literal_data_including_quotes_and_dollars(self):
        for source in ("https://example.com/CTFd.git#it's-$literal", 'https://example.com/CTFd.git#$(touch${IFS}marker)'):
            with self.subTest(source=source):
                result = self.start('--init', '--source-url', source)
                self.assertEqual(result.returncode, 0, result.stderr)
                # Compose escapes dollars when serializing its reusable config.
                context = self.compose_config()['services']['ctfd']['build']['context']
                self.assertEqual(context.replace('$$', '$'), source)
                self.assertFalse((self.root / 'marker').exists())


if __name__ == '__main__':
    unittest.main()
