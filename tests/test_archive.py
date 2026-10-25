"""安全解压：路径穿越、盘符、链接与限额。"""
import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from cellflow.archive import ArchiveError, safe_extract


class ArchiveSafety(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.target = self.root / 'unpack'
        self.sentinel = self.root / 'sentinel.txt'
        self.sentinel.write_text('untouched', encoding='utf-8')

    def tearDown(self):
        self.folder.cleanup()

    def _zip(self, members):
        path = self.root / 'bad.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        return path

    def _tar(self, members, links=()):
        path = self.root / 'bad.tar.gz'
        with tarfile.open(path, 'w:gz') as tf:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            for name, linkname in links:
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = linkname
                tf.addfile(info)
        return path

    def test_zip_parent_traversal_is_refused(self):
        path = self._zip({'../escape.txt': b'x'})
        with self.assertRaises(ArchiveError):
            safe_extract(path, self.target)
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'untouched')
        self.assertFalse((self.root / 'escape.txt').exists())

    def test_zip_drive_and_unc_are_refused(self):
        for name in ('C:/Windows/evil.txt', 'C:\\Windows\\evil.txt', '//server/share/evil.txt'):
            with self.subTest(name=name):
                path = self._zip({name: b'x'})
                with self.assertRaises(ArchiveError):
                    safe_extract(path, self.target)

    def test_tar_symlink_is_refused(self):
        path = self._tar({'ok.txt': b'x'}, links=[('link.txt', '../sentinel.txt')])
        with self.assertRaises(ArchiveError):
            safe_extract(path, self.target)
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'untouched')

    def test_tar_traversal_is_refused(self):
        path = self._tar({'../escape.txt': b'x'})
        with self.assertRaises(ArchiveError):
            safe_extract(path, self.target)
        self.assertFalse((self.root / 'escape.txt').exists())

    def test_limits_are_enforced(self):
        path = self.root / 'many.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            for index in range(3):
                zf.writestr(f'f{index}.txt', b'x')
        with self.assertRaises(ArchiveError):
            safe_extract(path, self.target, max_files=1)

    def test_normal_archive_extracts_inside_target(self):
        path = self.root / 'good.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('sub/data.csv', 'a,b\n1,2\n')
        safe_extract(path, self.target)
        extracted = self.target / 'sub' / 'data.csv'
        self.assertTrue(extracted.is_file())
        self.assertTrue(extracted.resolve().is_relative_to(self.target.resolve()))

    def test_import_any_rejects_malicious_zip_without_side_effects(self):
        from cellflow import importers
        path = self._zip({'C:/evil.txt': b'x', 'data.csv': 'cell,g1,g2,g3,g4,g5\nc1,1,2,3,4,5\n'})
        with self.assertRaises(ValueError):
            importers.import_any(path)
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'untouched')
        self.assertFalse((self.root / 'evil.txt').exists())


if __name__ == '__main__':
    unittest.main()
