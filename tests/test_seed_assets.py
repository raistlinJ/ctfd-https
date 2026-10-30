"""Check safe initialization of directories that hide bundled CTFd code."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('seed_assets', ROOT / 'scripts/seed_assets.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SeedAssetsTests(unittest.TestCase):
    def test_initialization_fills_missing_files_and_preserves_local_customization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'image'
            target = root / 'host'
            (source / 'core/static').mkdir(parents=True)
            (source / 'core/static/main.js').write_text('compiled frontend')
            (source / '__init__.py').write_text('plugin package')
            (target / 'evergreen').mkdir(parents=True)
            (target / 'evergreen/custom.css').write_text('custom theme')
            self.assertEqual(module.seed_assets(source, target), 2)
            self.assertEqual((target / '__init__.py').read_text(), 'plugin package')
            self.assertEqual((target / 'core/static/main.js').read_text(), 'compiled frontend')
            (target / 'core/static/main.js').write_text('local customization')
            (source / 'core/static/new.js').write_text('new bundled file')
            self.assertEqual(module.seed_assets(source, target), 1)
            self.assertEqual((target / 'core/static/main.js').read_text(), 'local customization')
            self.assertEqual((target / 'core/static/new.js').read_text(), 'new bundled file')
            self.assertEqual((target / 'evergreen/custom.css').read_text(), 'custom theme')
            self.assertEqual(module.seed_assets(source, target), 0)

    def test_missing_image_directory_fails_before_creating_host_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RuntimeError):
                module.seed_assets(root / 'missing', root / 'host')
            self.assertFalse((root / 'host').exists())


if __name__ == '__main__':
    unittest.main()
