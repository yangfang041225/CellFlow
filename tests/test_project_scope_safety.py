import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from cellflow import api


class ProjectScopeSafety(unittest.TestCase):
    def test_foreign_job_id_cannot_read_or_mutate(self):
        with patch('cellflow.queue.get_job', return_value={'project_id': 'B'}), \
                patch.object(api.workspace, 'active_project', return_value={'id': 'A'}):
            for action in [lambda: api._job('foreign'),
                           lambda: api.cancel_run('foreign'),
                           lambda: api.delete_run('foreign'),
                           lambda: api.rename_run('foreign', api.RenameBody(name='changed'))]:
                with self.assertRaises(HTTPException) as error:
                    action()
                self.assertEqual(error.exception.status_code, 404)

    def test_list_cannot_escape_active_project(self):
        with patch.object(api.workspace, 'active_project', return_value={'id': 'A'}):
            for project in ['B', 'all']:
                with self.assertRaises(HTTPException):
                    api.runs(project=project)

    def test_path_under_allowed_root_still_requires_project_membership(self):
        target = Path(__file__).resolve()
        with patch.object(api, '_safe', return_value=target), patch.object(api, '_dataset_rows', return_value=[]):
            with self.assertRaises(HTTPException):
                api._project_dataset(target)
