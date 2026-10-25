"""Negative regressions for independently confirmed Phase II review findings."""
import copy
import gzip
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from scipy import sparse

from benchmarks.matrix_compare import compare_velocity
from benchmarks.fate_compare import compare as compare_fate
from benchmarks.fetch import download, IntegrityError
from cellflow import api, importers
from cellflow.evidence import assess
from cellflow.reference_evidence import sha256


def payload(v, cells=('c1', 'c2'), genes=('a', 'b')):
    return dict(velocity=np.array(v, dtype=float), cells=np.array(cells), genes=np.array(genes))


class MatrixComparison(unittest.TestCase):
    def test_reordered_axes(self):
        a = payload([[1, 2], [3, 4]])
        b = payload([[4, 3], [2, 1]], ('c2', 'c1'), ('b', 'a'))
        self.assertEqual(compare_velocity(a, b)['max_abs_diff'], 0)

    def test_duplicate_or_missing_cells_rejected(self):
        a = payload([[1, 2], [3, 4]])
        for cells in [('c1', 'c1'), ('c1', 'other')]:
            with self.subTest(cells=cells), self.assertRaises(ValueError):
                compare_velocity(a, payload([[1, 2], [3, 4]], cells))

    def test_missing_finite_gene_never_complete(self):
        a = payload([[0, 2], [0, 4]])
        b = payload([[2], [4]], genes=('b',))
        report = compare_velocity(a, b)
        self.assertFalse(report['coverage_complete'])
        self.assertEqual(report['candidate_missing'], ['a'])
        self.assertIn('reference_all_zero', report['gene_classification'][0]['reasons'])

    def test_zero_is_comparable_and_pearson_undefined(self):
        a = payload([[0, 0], [0, 0]])
        self.assertIsNone(compare_velocity(a, a)['pearson'])
        self.assertEqual(compare_velocity(a, payload([[0, 1], [0, 0]]))['max_abs_diff'], 1)

    def test_nonfinite_masks_not_counted_as_equal_elements(self):
        a = payload([[np.nan, 1], [np.nan, 2]])
        report = compare_velocity(a, a)
        self.assertEqual(report['finite_mismatch'], 0)
        self.assertEqual(report['compared_finite_elements'], 2)
        self.assertEqual(report['jointly_nonfinite_elements'], 2)
        self.assertEqual(compare_velocity(a, payload([[0, 1], [0, 2]]))['finite_mismatch'], 2)


class BoundEvidence(unittest.TestCase):
    def test_failed_quality_or_run_cannot_upgrade(self):
        for status, checks in [('failed', [{'ok':True}]), ('completed', [{'ok':False}]), ('completed', [])]:
            with self.subTest(status=status, checks=checks):
                levels = assess({}, {'ok':True,'checks':checks}, {'status':status,'reference_comparison':{'passed':True}})
                self.assertLessEqual(levels['max_level'], 0)

    def test_bound_manifest_is_recomputed_and_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, data in [('config.json', {'method':'scvelo_deterministic'}), ('environment.json', {'numpy':np.__version__})]:
                (root/name).write_text(json.dumps(data))
            (root/'input.bin').write_bytes(b'input')
            (root/'source').mkdir()
            (root/'source/code.py').write_bytes(b'code')
            p = payload([[1,2],[3,4]])
            for name in ('velocity.npz', 'reference.npz'):
                np.savez(root/name, **p)
            provenance = dict(status='completed', started_at='fixture-task', input_path=str(root/'input.bin'),
                              input_sha256=sha256(root/'input.bin'), code_sha256={'code.py':sha256(root/'source/code.py')})
            binding = {k:provenance[k] for k in ('started_at','input_sha256','code_sha256')}
            binding.update(config_sha256=sha256(root/'config.json'), environment_sha256=sha256(root/'environment.json'))
            manifest = dict(schema=1, passed=True, method='scvelo_deterministic', binding=binding,
                            candidate={'path':'velocity.npz','sha256':sha256(root/'velocity.npz')},
                            reference={'path':'reference.npz','sha256':sha256(root/'reference.npz')})
            def level(m):
                (root/'comparison.json').write_text(json.dumps(m))
                prov = dict(provenance, reference_comparison={'manifest':'comparison.json','sha256':sha256(root/'comparison.json')})
                return assess({}, {'ok':True,'checks':[{'ok':True}]}, prov, root)['max_level']
            self.assertEqual(level(manifest), 2)
            for key in ('started_at', 'input_sha256', 'code_sha256', 'config_sha256', 'environment_sha256'):
                bad = copy.deepcopy(manifest)
                bad['binding'][key] = 'wrong'
                self.assertEqual(level(bad), 1)
            bad = copy.deepcopy(manifest)
            bad['passed'] = False
            self.assertEqual(level(bad), 1)
            np.savez(root/'reference.npz', **payload([[1,2],[3,9]]))
            manifest['reference']['sha256'] = sha256(root/'reference.npz')
            self.assertEqual(level(manifest), 1)


class TableSafety(unittest.TestCase):
    content = b'cell,annotation,g1,g2,g3,g4,g5\nc1,A,1,2,3,4,5\nc2,,2,3,4,5,6\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(api, 'ROOT', self.root),
                        patch.object(api.workspace, 'active_project', return_value={'id':'A'}),
                        patch.object(api.workspace, 'register_dataset')]
        self.mocks = [p.start() for p in self.patches]
        self.client = TestClient(api.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def preview(self, data=None, name='table.csv'):
        r = self.client.post('/api/datasets/table-preview', files={'file':(name, data or self.content)})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['preview_id']

    def confirm(self, token):
        return self.client.post('/api/datasets/table-import', json={'preview_id':token,
                                'orientation':'cells_by_genes','metadata_columns':['annotation']})

    def test_project_content_expiry_and_replay(self):
        token = self.preview()
        self.mocks[1].return_value = {'id':'B'}
        self.assertEqual(self.confirm(token).status_code, 404)
        self.mocks[1].return_value = {'id':'A'}
        source = self.root/'results/inputs/.preview'/f'{token}.csv'
        original = source.read_bytes()
        st = source.stat()
        source.write_bytes(original.replace(b'c1,A,1', b'c1,A,9'))
        os.utime(source, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(self.confirm(token).status_code, 409)
        source.write_bytes(original)
        meta = source.with_suffix('.json')
        data = json.loads(meta.read_text())
        data['expires_at'] = time.time()-1
        meta.write_text(json.dumps(data))
        self.assertEqual(self.confirm(token).status_code, 410)
        token = self.preview()
        self.assertEqual(self.confirm(token).status_code, 200)
        self.assertEqual(self.confirm(token).status_code, 404)

    def test_compressed_table_preview_confirm(self):
        self.assertEqual(self.confirm(self.preview(gzip.compress(self.content), 'table.csv.gz')).status_code, 200)

    def test_direct_upload_requires_explicit_semantics(self):
        r = self.client.post('/api/datasets/upload', files={'file':('a.csv', self.content)})
        self.assertEqual(r.status_code, 409)

    def test_streaming_metadata_matches_small_table(self):
        path = self.root/'a.csv'
        path.write_bytes(self.content)
        small = importers.from_table(path, 'cells_by_genes', ['annotation'])
        streamed = importers._from_table_streaming(path, ',', 1, False, ['annotation'])
        np.testing.assert_array_equal(small.dataset.X.toarray(), streamed.dataset.X.toarray())
        self.assertEqual(list(streamed.dataset.obs['annotation']), ['A', None])

    def test_late_bad_expression_and_duplicates_rejected(self):
        for raw in (self.content.replace(b'2,3,4,5,6', b'bad,3,4,5,6'),
                    self.content.replace(b'g2', b'g1'), self.content.replace(b'c2,', b'c1,')):
            path = self.root/'bad.csv'
            path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                importers._from_table_streaming(path, ',', 1, False, ['annotation'])


class FateEquations(unittest.TestCase):
    def example(self):
        p = dict(probs=np.array([[1,0],[1,0],[0,1],[.6,.4]]), cells=np.array(['a','b','c','d']),
                 terminal_names=np.array(['A','B']), terminal_labels=np.array(['A','A','B','']))
        t = np.eye(4)
        t[3] = [.1,.2,.2,.5]
        return p, t

    def test_multiple_terminal_cells_and_reordered_axes(self):
        p,t = self.example()
        other = dict(p, probs=p['probs'][::-1,::-1], cells=p['cells'][::-1], terminal_names=p['terminal_names'][::-1])
        self.assertTrue(compare_fate(other,p,t)['ok'])

    def test_no_probability_based_boundary_inference(self):
        p,t = self.example()
        t[3] = [1,0,0,0]
        p['probs'][3] = [1,0]
        report = compare_fate(p,p,t)
        self.assertTrue(report['ok'])
        self.assertEqual(report['terminal_cells'], 3)

    def test_invalid_probs_states_and_unreachable(self):
        p,t = self.example()
        for v in (-.2, np.nan, 1.2):
            bad = copy.deepcopy(p)
            bad['probs'][3,0] = v
            with self.subTest(v=v), self.assertRaises(ValueError):
                compare_fate(bad,p,t)
        t[3] = [0,0,0,1]
        with self.assertRaisesRegex(ValueError, '不可达'):
            compare_fate(p,p,sparse.csr_matrix(t))


class DownloadSafety(unittest.TestCase):
    def test_http_faults_publish_only_valid_complete_bytes(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            mode = '200'
            def do_GET(self):
                data = b'abcdef'
                if self.mode == 'bad-range':
                    self.send_response(206)
                    self.send_header('Content-Range','bytes 1-3/6')
                    data = b'bcd'
                elif self.mode == '206':
                    self.send_response(206)
                    self.send_header('Content-Range','bytes 3-5/6')
                    data = b'def'
                elif self.mode == '416':
                    self.send_response(416)
                    self.send_header('Content-Range','bytes */6')
                    data = b''
                else:
                    self.send_response(200)
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self,*args): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                url = f'http://127.0.0.1:{server.server_port}/data'
                digest = hashlib.sha256(b'abcdef').hexdigest()
                for mode in ('200','206','416','bad-range'):
                    Handler.mode = mode
                    target = base/mode
                    target.with_name(target.name+'.part').write_bytes(b'abcdef' if mode == '416' else b'abc')
                    if mode == 'bad-range':
                        with self.assertRaises(IntegrityError): download(url,target,digest,6,retries=1)
                        self.assertFalse(target.exists())
                    else:
                        download(url,target,digest,6,retries=1)
                        self.assertEqual(target.read_bytes(), b'abcdef')
                Handler.mode = '200'
                with self.assertRaises(IntegrityError): download(url,base/'invalid.h5ad',digest,6,retries=1)
                self.assertFalse((base/'invalid.h5ad').exists())
                with self.assertRaises(IntegrityError): download(url,base/'oversize',max_bytes=3,retries=1)
                self.assertFalse((base/'oversize').exists())
        finally:
            server.shutdown()
            server.server_close()


class WriteFailure(unittest.TestCase):
    def test_snapshot_enospc_is_recorded_failed(self):
        from cellflow.runner import run
        from errno import ENOSPC
        demo = Path(__file__).resolve().parents[1]/'examples/synthetic.npz'
        with tempfile.TemporaryDirectory() as tmp, patch('cellflow.runner.shutil.copyfile', side_effect=OSError(ENOSPC,'injected disk full')):
            p = run(demo, {'method':'qc'}, Path(tmp)/'run')
            self.assertEqual(p['status'], 'failed')
            self.assertEqual(p['failed_stage'], 'prepare')
            self.assertIn('injected disk full', p['error'])

    def test_fixed_terminal_configuration_never_silently_skipped_in_auto(self):
        from cellflow.runner import run, normalize_config
        for invalid in (' ', 'A,', 'A,A', ','):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_config({'cellrank_terminal':invalid})
        from cellflow.data import load, Dataset
        demo = Path(__file__).resolve().parents[1]/'examples/synthetic.npz'
        data = load(demo)
        data.layers.clear()  # no velocity and no root means automatic fate cannot run
        with tempfile.TemporaryDirectory() as tmp, patch('cellflow.runner.load',return_value=data):
            result = run(demo, {'method':'auto','raw_x_confirmed':True,'n_top_genes':40,
                               'n_pcs':8,'n_neighbors':8,'use_umap':False,'cellrank_terminal':'A'},Path(tmp)/'run')
            self.assertEqual(result['status'],'failed')
            self.assertIn('固定终末',result['error'])

    def test_raw_table_run_requires_explicit_inference_confirmation(self):
        from cellflow.runner import run
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            table=root/'input.csv'
            table.write_text('cell,g1,g2,g3,g4,g5\nc1,1,2,3,4,5\n')
            result=run(table,{'method':'qc'},root/'run')
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['failed_stage'],'read_input')
            self.assertIn('table_inference_confirmed=true',result['error'])
