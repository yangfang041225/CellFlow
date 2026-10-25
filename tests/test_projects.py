"""项目/工作区：创建、切换、按项目隔离数据集与任务。"""
import tempfile
import unittest
from pathlib import Path

from cellflow import workspace

ROOT = Path(__file__).resolve().parents[1]


class Projects(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Path(self.folder.name)
        self._old = (workspace.STORE, workspace.PROJECTS_FILE, workspace.PROJECT_FILE,
                     workspace.DATASET_FILE)
        workspace.STORE = self.store
        workspace.PROJECTS_FILE = self.store / 'projects.json'
        workspace.PROJECT_FILE = self.store / 'workspace.json'
        workspace.DATASET_FILE = self.store / 'datasets.json'

    def tearDown(self):
        (workspace.STORE, workspace.PROJECTS_FILE, workspace.PROJECT_FILE,
         workspace.DATASET_FILE) = self._old
        self.folder.cleanup()

    def test_default_project_and_switch(self):
        data = workspace.projects()
        self.assertEqual(data['active'], 'default')
        item = workspace.create_project('造血分化')
        self.assertEqual(workspace.active_project()['name'], '造血分化')
        workspace.activate_project('default')
        self.assertEqual(workspace.active_project()['id'], 'default')
        self.assertIsNone(workspace.activate_project('missing'))
        workspace.rename_project('改名后')
        self.assertEqual(workspace.active_project()['name'], '改名后')
        self.assertEqual(item['name'], '造血分化')

    def test_datasets_are_scoped_per_project(self):
        source = self.store / 'sample.h5ad'
        source.write_bytes(b'placeholder')
        workspace.register_dataset(source)
        default_rows = workspace.list_datasets(project='default')
        self.assertTrue(any(row['path'] == source for row in default_rows))
        other = workspace.create_project('另一个项目')
        self.assertFalse(any(row['path'] == source for row in workspace.list_datasets(project=other['id'])))
        workspace.register_dataset(source)
        self.assertTrue(any(row['path'] == source for row in workspace.list_datasets(project=other['id'])))

    def test_queue_filters_by_project(self):
        from cellflow import queue
        store = self.store / 'results'
        workspace.create_project('项目B')
        job = queue.submit(ROOT / 'examples' / 'synthetic.npz',
                           {'method': 'qc', 'seed': 1}, store=store)
        rows = queue.list_jobs(store=store)
        self.assertEqual(len(rows), 1)
        active = workspace.active_project()['id']
        self.assertEqual(queue.list_jobs(store=store, project=active)[0]['id'], job)
        self.assertEqual(queue.list_jobs(store=store, project='default'), [])
        self.assertEqual(queue.list_jobs(store=store, project='all')[0]['id'], job)

    def test_api_project_lifecycle(self):
        from fastapi.testclient import TestClient
        from cellflow import api
        client = TestClient(api.app)
        meta = client.get('/api/meta').json()
        self.assertIn('projects', meta)
        created = client.post('/api/projects', json={'name': 'API 新项目'})
        self.assertEqual(created.status_code, 200, created.text[:200])
        project_id = created.json()['project']['id']
        self.assertEqual(client.post(f'/api/projects/{project_id}/activate').status_code, 200)
        archived = client.post(f'/api/projects/{project_id}/archive?archived=1')
        self.assertEqual(archived.status_code, 200)
        self.assertTrue(archived.json()['project']['archived'])
        self.assertEqual(client.post(f'/api/projects/{project_id}/activate').status_code, 404)
        deleted = client.delete(f'/api/projects/{project_id}')
        self.assertEqual(deleted.status_code, 200, deleted.text[:200])
        self.assertEqual(client.delete('/api/projects/default').status_code, 400)

    def test_archive_switches_active_and_blocks_activation(self):
        item = workspace.create_project('待归档')
        archived = workspace.archive_project(item['id'])
        self.assertTrue(archived['archived'])
        self.assertEqual(workspace.active_project()['id'], 'default')
        self.assertIsNone(workspace.activate_project(item['id']))
        self.assertIsNone(workspace.archive_project('default'), '最后一个未归档项目不能归档')
        restored = workspace.archive_project(item['id'], archived=False)
        self.assertFalse(restored['archived'])
        self.assertEqual(workspace.activate_project(item['id'])['id'], item['id'])

    def test_delete_project_guards(self):
        item = workspace.create_project('空项目')
        workspace.activate_project('default')
        ok, message = workspace.delete_project('default')
        self.assertFalse(ok)
        self.assertIn('默认项目', message)
        ok, message = workspace.delete_project(item['id'])
        self.assertTrue(ok, message)
        self.assertNotIn(item['id'], [entry['id'] for entry in workspace.projects()['items']])

    def test_delete_project_with_dataset_is_refused(self):
        item = workspace.create_project('有数据')
        source = self.store / 'sample2.h5ad'
        source.write_bytes(b'x')
        workspace.register_dataset(source)
        workspace.activate_project('default')
        ok, message = workspace.delete_project(item['id'])
        self.assertFalse(ok)
        self.assertIn('数据集', message)

    def test_delete_project_with_task_is_refused(self):
        from unittest import mock
        item = workspace.create_project('有任务')
        workspace.activate_project('default')
        with mock.patch('cellflow.queue.list_jobs', return_value=[{'id': 'fake-task'}]):
            ok, message = workspace.delete_project(item['id'])
        self.assertFalse(ok)
        self.assertIn('任务', message)


if __name__ == '__main__':
    unittest.main()
