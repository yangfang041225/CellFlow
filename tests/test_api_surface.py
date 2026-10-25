"""API 表面审计：路由存在、函数名不以下划线开头、无参 GET 不 500。

用途：捕捉“装饰器挂错函数”这类问题（真实发生过一次）。
"""
import unittest

from fastapi.testclient import TestClient

from cellflow import api

EXPECTED = {
    ('GET', '/api/health'),
    ('GET', '/api/meta'),
    ('GET', '/api/methods'),
    ('GET', '/api/datasets'),
    ('GET', '/api/runs'),
    ('GET', '/api/env'),
    ('GET', '/api/downloads'),
    ('POST', '/api/runs'),
    ('POST', '/api/runs/{job_id}/cancel'),
    ('POST', '/api/projects'),
    ('POST', '/api/projects/{project_id}/archive'),
    ('POST', '/api/downloads'),
    ('DELETE', '/api/projects/{project_id}'),
    ('PATCH', '/api/runs/{job_id}'),
}


class ApiSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)
        from tests.fixtures import ensure_completed_run
        ensure_completed_run()
        cls.routes = []
        for route in api.app.routes:
            methods = getattr(route, 'methods', None)
            if not methods:
                continue
            for method in methods:
                if method in ('HEAD', 'OPTIONS'):
                    continue
                cls.routes.append((method, getattr(route, 'path', ''),
                                   getattr(route, 'endpoint', None)))

    def test_expected_routes_exist(self):
        actual = {(method, path) for method, path, _ in self.routes}
        missing = EXPECTED - actual
        self.assertFalse(missing, f'缺少路由：{missing}')

    def test_endpoint_functions_are_public(self):
        bad = [(method, path, endpoint.__name__) for method, path, endpoint in self.routes
               if endpoint is not None and endpoint.__name__.startswith('_')]
        self.assertFalse(bad, f'路由绑定到了内部函数（可能是装饰器挂错）：{bad}')

    def test_no_duplicate_method_path(self):
        seen = {}
        for method, path, endpoint in self.routes:
            key = (method, path)
            self.assertNotIn(key, seen, f'重复路由：{key}')
            seen[key] = endpoint

    def test_parameterless_gets_do_not_crash(self):
        for method, path, _ in self.routes:
            if method != 'GET' or '{' in path or path.startswith('/openapi'):
                continue
            response = self.client.get(path)
            self.assertNotEqual(response.status_code, 500, f'{path} 返回 500：{response.text[:200]}')

    def test_run_detail_endpoints_do_not_crash(self):
        runs = self.client.get('/api/runs').json()['runs']
        completed = [item for item in runs if item['state'] == 'completed']
        if not completed:
            self.skipTest('没有已完成任务')
        job_id = completed[0]['id']
        for path in (f'/api/runs/{job_id}', f'/api/runs/{job_id}/zip',
                     f'/api/logs/{job_id}'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f'{path} -> {response.status_code}')
        detail = self.client.get(f'/api/runs/{job_id}').json()
        for figure in detail.get('figures', [])[:2]:
            response = self.client.get(f'/api/runs/{job_id}/figure/{figure}')
            self.assertEqual(response.status_code, 200)
        report = self.client.get(f'/api/runs/{job_id}/report/a')
        self.assertEqual(report.status_code, 200, report.text[:200])
        pdf = self.client.get(f'/api/runs/{job_id}/report/a/pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF-'))

    def test_unhandled_exception_returns_json_and_logs(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        client = TestClient(api.app, raise_server_exceptions=False)
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(api, 'LOGS', Path(folder)), \
                    mock.patch('cellflow.workspace.list_datasets', side_effect=ValueError('boom')):
                response = client.get('/api/meta')
            log = Path(folder) / 'api-errors.log'
            self.assertTrue(log.exists())
            self.assertIn('boom', log.read_text(encoding='utf-8'))
        self.assertEqual(response.status_code, 500)
        self.assertIn('内部错误', response.json()['detail'])
        self.assertIn('ValueError: boom', response.json()['error'])

    def test_upload_overflow_is_rejected_without_leftovers(self):
        import io
        from unittest import mock
        inputs = api.ROOT / 'results' / 'inputs'
        before = {item.name for item in inputs.glob('*')} if inputs.exists() else set()
        client = TestClient(api.app)
        with mock.patch.object(api, 'MAX_UPLOAD_BYTES', 32):
            response = client.post('/api/datasets/upload',
                                   files={'file': ('big.csv', io.BytesIO(b'a' * 200), 'text/csv')})
        self.assertEqual(response.status_code, 413)
        after = {item.name for item in inputs.glob('*')} if inputs.exists() else set()
        self.assertEqual(after - before, set())


if __name__ == '__main__':
    unittest.main()
