import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import convert_ctfd_export as converter


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, name, password="original-hash", upload=b"original upload", extra=None):
        path = self.root / name
        tables = {
            "alembic_version": [{"version_num": "a49ad66aa0f1"}],
            "config": [{"id": 1, "key": "ctf_version", "value": "3.7.6"}],
            "users": [{"id": 7, "password": password}],
        }
        with zipfile.ZipFile(path, "w") as archive:
            for table, records in tables.items():
                archive.writestr(f"db/{table}.json", json.dumps({"results": records}))
            archive.writestr("db/awards.json", b"")
            archive.writestr("uploads/abc/file.txt", upload)
            if extra:
                archive.writestr(extra, b"bad")
        return path

    def test_accepts_empty_legacy_tables(self):
        revision, version, tables = converter.inspect_archive(self.archive("input.zip"))
        self.assertEqual((revision, version), ("a49ad66aa0f1", "3.7.6"))
        self.assertEqual(tables["db/awards.json"], [])

    def test_rejects_traversal_and_absolute_paths(self):
        for index, name in enumerate(("uploads/../../bad", "/absolute", "uploads\\bad")):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe"):
                converter.inspect_archive(self.archive(f"unsafe-{index}.zip", extra=name))

    def test_rejects_duplicate_members(self):
        path = self.archive("duplicate.zip")
        with zipfile.ZipFile(path, "a") as archive:
            with self.assertWarns(UserWarning):
                archive.writestr("db/awards.json", b"")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            converter.inspect_archive(path)

    def test_detects_password_and_upload_changes(self):
        original = self.archive("original.zip")
        changed_password = self.archive("password.zip", password="different")
        changed_upload = self.archive("upload.zip", upload=b"different")
        with self.assertRaisesRegex(ValueError, "users.password"):
            converter.check_preservation(original, changed_password)
        with self.assertRaisesRegex(ValueError, "Upload contents changed"):
            converter.check_preservation(original, changed_upload)

    def test_detects_records_lost_on_import(self):
        original = self.archive("original.zip")
        changed = self.archive("changed.zip")
        # Build a second valid ZIP with one table emptied.
        filtered = self.root / "filtered.zip"
        with zipfile.ZipFile(changed) as src, zipfile.ZipFile(filtered, "w") as dst:
            for name in src.namelist():
                dst.writestr(name, b"" if name == "db/users.json" else src.read(name))
        with self.assertRaisesRegex(ValueError, "Record count changed"):
            converter.check_preservation(original, filtered)

    def test_compression_preserves_tables_and_uploads(self):
        original = self.archive("original.zip")
        output = self.root / "compressed.zip"
        converter.compress_archive(original, output)
        self.assertEqual(converter.check_preservation(original, output, exact=True), 1)

    def test_refuses_overwrite_before_starting_docker(self):
        source = self.archive("original.zip")
        existing = self.archive("existing.zip")
        for output in (source, existing):
            with self.subTest(output=output), patch("subprocess.run") as run:
                with self.assertRaises(ValueError):
                    converter.convert(argparse.Namespace(input=source, output=output, image=converter.DEFAULT_IMAGE))
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
