"""P3-02 数据根与临时文件治理的回归测试。

覆盖的缺陷：解压/转换/下载中间文件曾落在 %TEMP%（默认在 C 盘）。
现在一律走「用户数据根目录 / staging」，并且默认值等于改造前的位置，
保证既有安装升级后已登记数据集的绝对路径不变。
"""
import os
import tempfile
import unittest
from pathlib import Path

from cellflow import paths


class _Isolated(unittest.TestCase):
    """每个用例都用独立的数据根与设置文件，绝不碰用户真实配置。"""

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.root = Path(self._folder.name) / 'data-root'
        self.root.mkdir(parents=True, exist_ok=True)
        self._env = {
            paths.DATA_ROOT_ENV: str(self.root),
            paths.SETTINGS_ENV: str(Path(self._folder.name) / 'settings.json'),
        }
        self._saved = {key: os.environ.get(key) for key in self._env}
        os.environ.update(self._env)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._folder.cleanup()


class RootResolution(_Isolated):
    def test_env_override_wins(self):
        self.assertEqual(paths.data_root(), self.root)

    def test_default_is_legacy_data_dir(self):
        os.environ.pop(paths.DATA_ROOT_ENV, None)
        self.assertEqual(paths.data_root(), paths.repo_root() / 'data',
                         '默认必须是改造前的 <repo>/data，否则老数据路径会变')

    def test_settings_file_is_used_when_no_env(self):
        os.environ.pop(paths.DATA_ROOT_ENV, None)
        paths.write_settings({'data_root': str(self.root)})
        self.assertEqual(paths.data_root(), self.root)

    def test_validate_root_reports_missing(self):
        report = paths.validate_root(Path(self._folder.name) / 'nope')
        self.assertFalse(report['exists'])
        self.assertFalse(report['writable'])
        self.assertTrue(report['problem'])

    def test_set_data_root_requires_writable_dir(self):
        with self.assertRaises(paths.StorageError):
            paths.set_data_root(Path(self._folder.name) / 'missing-dir', create=False)

    def test_set_data_root_records_choice(self):
        target = Path(self._folder.name) / 'chosen'
        paths.set_data_root(target, create=True)
        self.assertEqual(paths.read_settings()['data_root'], str(target))
        self.assertTrue((target / 'staging').is_dir())


class Layout(_Isolated):
    def test_legacy_public_dir_is_preferred(self):
        legacy = self.root / 'public'
        legacy.mkdir(parents=True, exist_ok=True)
        self.assertEqual(paths.datasets_dir(), legacy,
                         '旧布局 <root>/public 已存在时必须继续用它')

    def test_new_install_uses_datasets_downloaded(self):
        self.assertEqual(paths.datasets_dir(), self.root / 'datasets' / 'downloaded')

    def test_make_workdir_lives_under_data_root(self):
        work = paths.make_workdir('cellflow_geo_GSE1_', kind='conversion')
        self.assertTrue(str(work).startswith(str(self.root / 'staging' / 'conversion')))
        self.assertTrue(work.is_dir())

    def test_make_workdir_is_unique(self):
        first = paths.make_workdir('same-prefix', kind='extraction')
        second = paths.make_workdir('same-prefix', kind='extraction')
        self.assertNotEqual(first, second)

    def test_unknown_staging_kind_is_rejected(self):
        with self.assertRaises(paths.StorageError):
            paths.staging_dir('nonsense')

    def test_partial_path_suffix(self):
        self.assertEqual(paths.partial_path(Path('a/b.h5ad')).name, 'b.h5ad.part')


class Space(_Isolated):
    def test_required_bytes_has_margin_and_floor(self):
        need = 10 * 1024 * 1024
        self.assertGreater(paths.required_bytes(need), need)
        self.assertGreaterEqual(paths.required_bytes(0), paths.MIN_FREE_BYTES)

    def test_insufficient_space_raises_readable_error(self):
        with self.assertRaises(paths.InsufficientSpace) as ctx:
            paths.ensure_free_space(self.root, need_bytes=10 ** 15, label='下载')
        message = str(ctx.exception)
        self.assertIn('下载', message)
        self.assertIn('设置', message, '提示要指向“设置 → 数据与存储”')

    def test_small_request_passes(self):
        report = paths.ensure_free_space(self.root, need_bytes=1024, label='下载')
        self.assertTrue(report['checked'])


class Cleanup(_Isolated):
    def test_cleanup_refuses_paths_outside_staging(self):
        outside = self.root / 'datasets' / 'keepme'
        outside.mkdir(parents=True, exist_ok=True)
        (outside / 'f.bin').write_bytes(b'x' * 16)
        result = paths.cleanup_staging(max_age_seconds=0, dry_run=False)
        self.assertTrue(outside.exists(), 'staging 之外的目录绝不能被清理')
        self.assertEqual(result['removed'], [])

    def test_cleanup_dry_run_does_not_delete(self):
        work = paths.make_workdir('old-task', kind='downloads')
        (work / 'part.bin').write_bytes(b'x' * 8)
        result = paths.cleanup_staging(max_age_seconds=0, dry_run=True)
        self.assertTrue(work.exists())
        self.assertEqual(len(result['removed']), 1)
        self.assertTrue(result['dry_run'])

    def test_cleanup_deletes_only_staging_children(self):
        work = paths.make_workdir('old-task', kind='downloads')
        (work / 'part.bin').write_bytes(b'x' * 8)
        result = paths.cleanup_staging(max_age_seconds=0, dry_run=False)
        self.assertFalse(work.exists())
        self.assertGreater(result['removed_bytes'], 0)

    def test_fresh_staging_is_kept(self):
        work = paths.make_workdir('fresh', kind='conversion')
        result = paths.cleanup_staging(max_age_seconds=3600, dry_run=False)
        self.assertTrue(work.exists())
        self.assertEqual(result['removed'], [])


class CommitFile(_Isolated):
    def test_commit_file_moves_and_refuses_overwrite(self):
        staging = paths.staging_dir('downloads')
        temp = staging / 'x.bin'
        temp.write_bytes(b'payload')
        final = self.root / 'datasets' / 'x.bin'
        paths.commit_file(temp, final)
        self.assertEqual(final.read_bytes(), b'payload')
        self.assertFalse(temp.exists())
        again = staging / 'x2.bin'
        again.write_bytes(b'other')
        with self.assertRaises(paths.StorageError):
            paths.commit_file(again, final)


if __name__ == '__main__':
    unittest.main()
