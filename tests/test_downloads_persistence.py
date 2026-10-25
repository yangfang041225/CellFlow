"""下载：内容版本化、失败不覆盖、持久状态、项目过滤。"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from cellflow import downloads


class DownloadsPersistence(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.outdir = Path(self.folder.name) / 'public'
        self.outdir.mkdir()
        self._old_tasks = dict(downloads._TASKS)
        self._old_loaded = dict(downloads._LOADED)
        self._old_persist = dict(downloads._LAST_PERSIST)
        downloads._TASKS.clear()
        downloads._LOADED.clear()
        downloads._LAST_PERSIST.clear()

    def tearDown(self):
        downloads._TASKS.clear()
        downloads._TASKS.update(self._old_tasks)
        downloads._LOADED.clear()
        downloads._LOADED.update(self._old_loaded)
        downloads._LAST_PERSIST.clear()
        downloads._LAST_PERSIST.update(self._old_persist)
        self.folder.cleanup()

    def _wait(self, task_id, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = downloads.get(task_id)
            if task and task['state'] not in ('queued', 'running', 'cancelling'):
                return task
            time.sleep(0.02)
        self.fail('下载没有在超时内结束')

    def _fetcher(self, content):
        def fetch(source, identifier, staging, progress=None):
            path = Path(staging) / 'result.h5ad'
            path.write_bytes(content)
            return path
        return fetch

    def test_success_uses_content_versioned_name(self):
        task_id = downloads.submit('url', 'x', self.outdir, self._fetcher(b'A' * 100), project='p1')
        task = self._wait(task_id)
        self.assertEqual(task['state'], 'done')
        final = Path(task['path'])
        self.assertTrue(final.is_file())
        self.assertRegex(final.name, r'result__[0-9a-f]{8}\.h5ad')
        self.assertEqual(final.read_bytes(), b'A' * 100)
        self.assertEqual(task['project'], 'p1')

    def test_same_name_new_content_does_not_overwrite(self):
        first = self._wait(downloads.submit('url', 'x', self.outdir, self._fetcher(b'A' * 10), project='p1'))
        second = self._wait(downloads.submit('url', 'x', self.outdir, self._fetcher(b'B' * 10), project='p1'))
        self.assertNotEqual(first['path'], second['path'])
        self.assertTrue(Path(first['path']).is_file())
        self.assertEqual(Path(second['path']).read_bytes(), b'B' * 10)

    def test_failure_keeps_old_file(self):
        first = self._wait(downloads.submit('url', 'x', self.outdir, self._fetcher(b'A' * 10), project='p1'))

        def boom(source, identifier, staging, progress=None):
            raise RuntimeError('network down')

        failed = self._wait(downloads.submit('url', 'x', self.outdir, boom, project='p1'))
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('network down', failed['error'])
        self.assertTrue(Path(first['path']).is_file())

    def test_state_persists_and_running_becomes_interrupted(self):
        task_id = downloads.submit('url', 'x', self.outdir, self._fetcher(b'A'), project='p1')
        self._wait(task_id)
        state = json.loads((self.outdir / '.downloads' / 'state.json').read_text(encoding='utf-8'))
        self.assertTrue(any(item['id'] == task_id for item in state))
        # 模拟重启：清空内存，再写入一条 running 残留
        downloads._TASKS.clear()
        downloads._LOADED.clear()
        payload = json.loads((self.outdir / '.downloads' / 'state.json').read_text(encoding='utf-8'))
        payload.append({'id': 'stale000001', 'source': 'url', 'identifier': 'y',
                        'state': 'running', 'project': 'p1', 'outdir': str(self.outdir),
                        'created': time.time(), 'updated': time.time()})
        (self.outdir / '.downloads' / 'state.json').write_text(json.dumps(payload), encoding='utf-8')
        tasks = {item['id']: item for item in downloads.list_tasks(project='p1', outdir=self.outdir)}
        self.assertEqual(tasks['stale000001']['state'], 'interrupted')

    def test_project_filter(self):
        mine = self._wait(downloads.submit('url', 'x', self.outdir, self._fetcher(b'A'), project='pa'))
        other = self._wait(downloads.submit('url', 'x', self.outdir, self._fetcher(b'B'), project='pb'))
        ids = {item['id'] for item in downloads.list_tasks(project='pa', outdir=self.outdir)}
        self.assertIn(mine['id'], ids)
        self.assertNotIn(other['id'], ids)

    def test_species_filter_sidecar_is_recorded(self):
        def fetch(source, identifier, staging, progress=None):
            path = Path(staging) / 'geo.h5ad'
            path.write_bytes(b'A' * 10)
            (Path(staging) / 'geo.h5ad.meta.json').write_text(
                json.dumps({'species_kept': 'human', 'species_dropped': {'mouse': 2}}),
                encoding='utf-8')
            return path

        task = self._wait(downloads.submit('url', 'x', self.outdir, fetch, project='p1'))
        self.assertEqual(task['species_filtered']['species_kept'], 'human')
        self.assertEqual(task['species_filtered']['species_dropped']['mouse'], 2)


if __name__ == '__main__':
    unittest.main()