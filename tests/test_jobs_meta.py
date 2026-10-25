"""任务元数据与结果质量校验的回归测试（可读名称/筛选/取消/质量）。"""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'


class JobsMeta(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cellflow import api
        cls.client = TestClient(api.app)

    def test_display_name_is_human_readable(self):
        from cellflow.queue import list_jobs, submit
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / 'store'
            job = submit(DEMO, {'method': 'qc', 'seed': 1}, store=store)
            row = next(item for item in list_jobs(store=store) if item['id'] == job)
            self.assertIn('数据质控', row['name'])
            self.assertNotEqual(row['name'].strip(), job)

    def test_filters_by_state_and_query(self):
        from cellflow.queue import list_jobs, submit
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / 'store'
            submit(DEMO, {'method': 'qc', 'seed': 1}, store=store)
            submit(DEMO, {'method': 'demo', 'seed': 1}, store=store)
            self.assertEqual(len(list_jobs(store=store, state='queued')), 2)
            hit = list_jobs(store=store, query='数据质控')
            self.assertEqual(len(hit), 1)
            self.assertEqual(hit[0]['method'], 'qc')
            self.assertEqual(list_jobs(store=store, method='demo')[0]['method'], 'demo')

    def test_cancel_queued_job(self):
        from cellflow.queue import cancel, list_jobs, submit
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / 'store'
            job = submit(DEMO, {'method': 'qc', 'seed': 1}, store=store)
            ok, message = cancel(job, store=store)
            self.assertTrue(ok, message)
            row = next(item for item in list_jobs(store=store) if item['id'] == job)
            self.assertEqual(row['state'], 'cancelled')
            again, reason = cancel(job, store=store)
            self.assertFalse(again)
            self.assertIn('不能取消', reason)

    def test_api_cancel_unknown_job_returns_400(self):
        response = self.client.post('/api/runs/not-a-real-job/cancel')
        self.assertEqual(response.status_code, 400)


    def test_health_reports_queue_and_device(self):
        body = self.client.get('/api/health').json()
        self.assertTrue(body['ok'])
        self.assertIn('queue', body)
        self.assertIn('running', body['queue'])
        self.assertIn(body['device'], ('cpu', 'cuda'))
        self.assertIn('projects', body)
    def test_env_endpoint_reports_environment(self):
        response = self.client.get('/api/env')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in ('python', 'versions', 'device', 'disk', 'paths', 'queue', 'web_built'):
            self.assertIn(key, body)
        self.assertIn('device', body['device'])
        self.assertIn('free_gb', body['disk'])

    def test_run_detail_exposes_stage(self):
        runs = self.client.get('/api/runs').json()['runs']
        if not runs:
            self.skipTest('没有任务可查')
        detail = self.client.get(f"/api/runs/{runs[0]['id']}").json()
        self.assertIn('stage', detail)
        self.assertIn('provenance', detail)
        self.assertIn('quality', detail)

    def test_rename_and_delete_job_record(self):
        from cellflow.queue import cancel, delete, list_jobs, rename, submit
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / 'store'
            job = submit(DEMO, {'method': 'qc', 'seed': 1}, store=store)
            ok, _ = rename(job, '我的第一个任务', store=store)
            self.assertTrue(ok)
            row = next(item for item in list_jobs(store=store) if item['id'] == job)
            self.assertEqual(row['name'], '我的第一个任务')
            self.assertFalse(rename(job, '   ', store=store)[0])
            self.assertFalse(delete(job, store=store)[0], '排队任务不允许删除')
            cancel(job, store=store)
            ok, message = delete(job, store=store)
            self.assertTrue(ok, message)
            self.assertEqual(list_jobs(store=store), [])

    def test_compare_configs_between_runs(self):
        runs = self.client.get('/api/runs').json()['runs']
        if len(runs) < 2:
            self.skipTest('需要至少两个任务')
        first, second = runs[0]['id'], runs[1]['id']
        response = self.client.get(f'/api/runs/{first}/compare/{second}')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['a'], first)
        self.assertIn('changes', body)
        same = self.client.get(f'/api/runs/{first}/compare/{first}').json()
        self.assertTrue(same['same'])
        self.assertEqual(same['changes'], [])
        self.assertIn('can_compare_figures', same)
        self.assertIn('metric_changes', same)
        if 'embedding.svg' in same['figures']['a']:
            self.assertTrue(same['can_compare_figures'], same['compare_reason'])
        self.assertIn('can_compare_figures', body)
        self.assertIsInstance(body['compare_reason'], str)

    def test_pdf_export_when_completed_run_exists(self):
        runs = self.client.get('/api/runs').json()['runs']
        completed = [item for item in runs if item['state'] == 'completed']
        if not completed:
            self.skipTest('没有已完成任务')
        response = self.client.get(f"/api/runs/{completed[0]['id']}/report/a/pdf")
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertGreater(len(response.content), 5000)

    def test_zip_contains_manifest_and_pdfs(self):
        import io
        import re
        import zipfile
        runs = self.client.get('/api/runs').json()['runs']
        completed = [item for item in runs if item['state'] == 'completed']
        if not completed:
            self.skipTest('没有已完成任务')
        job = completed[0]
        response = self.client.get(f"/api/runs/{job['id']}/zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:2], b'PK')
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        self.assertIn('manifest.json', names)
        self.assertIn('A_report.pdf', names)
        manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['task_id'], job['id'])
        self.assertIn('config', manifest)
        # F18：HTML 引用的每个本地图片都必须在 ZIP 内，否则离线打开会断图
        html = archive.read('A_report.html').decode('utf-8', errors='replace')
        for ref in re.findall(r'src="(figures/[^"]+)"', html):
            self.assertIn(ref, names, f'{ref} 不在 ZIP 内（离线断图）')
        # 该任务确实生成过流线图时，PNG 必须被打包（旧实现只收 *.svg）
        output = Path(job.get('output') or '')
        stream = (output / 'figures' / 'stream.png')
        if 'output' in job and stream.is_file():
            self.assertIn('figures/stream.png', names)

    def test_zip_declares_package_kind_and_hashes(self):
        import io
        import zipfile
        runs = self.client.get('/api/runs').json()['runs']
        completed = [item for item in runs if item['state'] == 'completed']
        if not completed:
            self.skipTest('没有已完成任务')
        job = completed[0]
        report = self.client.get(f"/api/runs/{job['id']}/zip")
        self.assertEqual(report.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(report.content))
        names = archive.namelist()
        manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['package_kind'], 'report-pack')
        self.assertIn('file_sha256', manifest)
        self.assertIn('REPRODUCE.md', names)
        self.assertIn('config.json', names)
        self.assertEqual(len(manifest['file_sha256']['config.json']), 64)
        full = self.client.get(f"/api/runs/{job['id']}/zip?full=1")
        self.assertEqual(full.status_code, 200)
        full_manifest = json.loads(zipfile.ZipFile(io.BytesIO(full.content)).read('manifest.json'))
        self.assertEqual(full_manifest['package_kind'], 'reproducibility-pack')

    def test_quality_report_flags_shape_mismatch(self):
        from cellflow.data import load
        from cellflow.runner import _quality_report
        data = load(DEMO)
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            (folder / 'figures').mkdir()
            (folder / 'figures' / 'qc.svg').write_text('<svg/>', encoding='utf-8')
            (folder / 'result.json').write_text(json.dumps({'metrics': {'a': 1.0}}), encoding='utf-8')
            payload = {'velocity': [[0.0] * 3 for _ in range(2)],
                       'cells': ['a', 'b'], 'genes': ['x', 'y', 'z']}
            import numpy as np
            np.savez_compressed(folder / 'velocity.npz', **payload)
            report = _quality_report(folder, {'metrics': {'a': 1.0}}, data)
            self.assertFalse(report['ok'])
            self.assertTrue((folder / 'quality.json').exists())
            failed = [item['check'] for item in report['checks'] if not item['ok']]
            self.assertTrue(any('维度' in item for item in failed))

    def test_figure_captions_explain_limits(self):
        from cellflow.reports import _figure_caption
        self.assertIn('不代表真实谱系', _figure_caption('paga.svg', {}, {}, 3))
        self.assertIn('模型推断', _figure_caption('fate.svg', {}, {}, 2))
        caption = _figure_caption('embedding.svg',
                                  {'embedding_basis': 'umap', 'arrows': [[1.0, 2.0]]}, {}, 1)
        self.assertIn('UMAP', caption)
        self.assertIn('归一化', caption)
        self.assertIn('图 1', caption)
        self.assertIn('不自动过滤', _figure_caption('qc.svg', {}, {}, 4))

    def test_quality_report_ok_for_consistent_outputs(self):
        from cellflow.data import load
        from cellflow.runner import _quality_report
        import numpy as np
        data = load(DEMO)
        n_cells, n_genes = data.X.shape
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            (folder / 'figures').mkdir()
            (folder / 'figures' / 'qc.svg').write_text('<svg/>', encoding='utf-8')
            (folder / 'result.json').write_text(json.dumps({'metrics': {'a': 1.0}}), encoding='utf-8')
            np.savez_compressed(folder / 'velocity.npz',
                                velocity=np.zeros((n_cells, n_genes)),
                                cells=np.asarray(data.cells, dtype=str),
                                genes=np.asarray(data.genes, dtype=str))
            report = _quality_report(folder, {'metrics': {'a': 1.0}}, data)
            self.assertTrue(report['ok'], report['warnings'])

    def test_interpretation_layers_are_separated(self):
        from cellflow.reports import interpretation_layers
        result = {'metrics': {'n_groups': 4, 'velocity_length_median': 1.2,
                              'velocity_confidence_median': 0.7,
                              'n_terminal_states': 2},
                  'groups': {'key': 'leiden'},
                  'fate': {'states': ['0', '3']},
                  'gene_table': [{'gene': 'A', 'diff': 1.0}]}
        text = interpretation_layers(result, {'velocity_model': 'stochastic'}, 'auto')
        self.assertIn('直接观察', text)
        self.assertIn('计算推断', text)
        self.assertIn('生物学假说', text)
        self.assertIn('不代表标记基因', text)
        self.assertIn('需要独立证据', text)

    def test_quality_flags_inconsistent_fate_and_embedding(self):
        import numpy as np
        from cellflow.data import load
        from cellflow.runner import _quality_report
        data = load(DEMO)
        n_cells, n_genes = data.X.shape
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            (folder / 'figures').mkdir()
            (folder / 'figures' / 'qc.svg').write_text('<svg/>', encoding='utf-8')
            np.savez_compressed(folder / 'velocity.npz',
                                velocity=np.zeros((n_cells, n_genes)),
                                cells=np.asarray(data.cells, dtype=str),
                                genes=np.asarray(data.genes, dtype=str))
            result = {
                'metrics': {'velocity_confidence_median': 1.4},
                'embedding': [[0.0, 0.0]] * (n_cells - 1),
                'fate': {'probabilities': [[0.6, 0.2]] * n_cells, 'states': ['0']},
                'gene_table': [{'gene': 'NOT_A_REAL_GENE', 'diff': 1.0}],
            }
            (folder / 'result.json').write_text(json.dumps(result), encoding='utf-8')
            report = _quality_report(folder, result, data)
            self.assertFalse(report['ok'], '嵌入行数不一致是结构性错误')
            text = ' '.join(report['warnings'])
            self.assertIn('嵌入坐标维度', text)
            self.assertIn('命运概率每行之和', text)
            self.assertIn('速度置信度', text)
            self.assertIn('候选基因', text)
            soft = [item for item in report['checks'] if item.get('soft')]
            self.assertTrue(soft)

    def test_unregister_dataset_keeps_file(self):
        from cellflow import workspace
        old_file = workspace.DATASET_FILE
        try:
            with tempfile.TemporaryDirectory() as folder:
                store = Path(folder)
                (store / 'data.h5ad').write_bytes(b'placeholder')
                workspace.DATASET_FILE = store / 'datasets.json'
                workspace.register_dataset(store / 'data.h5ad')
                self.assertTrue((store / 'datasets.json').exists())
                self.assertTrue(workspace.unregister_dataset(store / 'data.h5ad'))
                self.assertEqual(workspace.registry(), [])
                self.assertTrue((store / 'data.h5ad').exists(), '移除登记不能删除文件')
                self.assertFalse(workspace.unregister_dataset(store / 'data.h5ad'))
        finally:
            workspace.DATASET_FILE = old_file

    def test_delete_dataset_unknown_returns_404(self):
        response = self.client.delete('/api/datasets/not-a-real-dataset')
        self.assertEqual(response.status_code, 404)

    def test_dataset_detail_reports_hash_and_shape(self):
        datasets = self.client.get('/api/meta').json()['datasets']
        if not datasets:
            self.skipTest('没有数据集')
        item = next((row for row in datasets if row['source'] == '示例'), datasets[0])
        response = self.client.get('/api/datasets/' + item['id'] + '/detail')
        self.assertEqual(response.status_code, 200, response.text[:200])
        body = response.json()
        self.assertEqual(len(body['content_hash']), 64)
        self.assertIn(body['hash_kind'], ('sha256', 'sha256-partial-headtail-size'))
        self.assertGreater(body['n_cells'], 0)
        self.assertGreater(body['n_genes'], 0)
        self.assertIsInstance(body['obs'], list)
        self.assertIsInstance(body['tasks'], list)
        self.assertEqual(body['task_count'], len(body['tasks']))
        preview = body['preview']
        self.assertIn('nonzero_pct', preview['stats'])
        self.assertGreaterEqual(preview['stats']['max'], preview['stats']['min'])
        self.assertTrue(preview['cells'])
        self.assertTrue(preview['genes'])
        self.assertIsInstance(preview['obs_preview'], dict)
        self.assertIsInstance(preview['obs_summary'], dict)
        self.assertTrue(preview['obs_summary'])
        self.assertTrue(all('unique' in item for item in preview['obs_summary'].values()))
        unknown = self.client.get('/api/datasets/not-a-real-dataset/detail')
        self.assertEqual(unknown.status_code, 404)

    def test_delete_referenced_dataset_is_refused(self):
        from cellflow.queue import list_jobs
        datasets = self.client.get('/api/meta').json()['datasets']
        demo = next((item for item in datasets if item['source'] == '示例'), None)
        if demo is None or not list_jobs(dataset=demo['path']):
            self.skipTest('没有可验证的引用')
        response = self.client.delete('/api/datasets/' + demo['id'])
        self.assertEqual(response.status_code, 400)
        self.assertIn('仍被', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
