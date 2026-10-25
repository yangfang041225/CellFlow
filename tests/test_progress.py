"""运行阶段即时写盘：界面在任务运行中也能看到真实阶段。"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples' / 'synthetic.npz'


class LiveStage(unittest.TestCase):
    def test_stage_is_written_during_run(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'run'
            config = Path(folder) / 'config.json'
            config.write_text(json.dumps({
                'method': 'qc', 'seed': 1, 'n_top_genes': 60, 'n_neighbors': 10,
                'n_pcs': 10, 'raw_x_confirmed': True,
            }), encoding='utf-8')
            errlog = Path(folder) / 'stderr.log'
            error_handle = open(errlog, 'wb')
            process = subprocess.Popen(
                [sys.executable, '-m', 'cellflow.cli', 'run',
                 '--input', str(DEMO), '--config', str(config), '--output', str(out)],
                cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=error_handle)
            seen = set()
            deadline = time.time() + 120
            while time.time() < deadline:
                path = out / 'provenance.json'
                if path.exists():
                    try:
                        seen.add(json.loads(path.read_text(encoding='utf-8')).get('last_stage'))
                    except Exception:
                        pass
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            process.wait(timeout=60)
            error_handle.close()
            tail = errlog.read_text(encoding='utf-8', errors='replace')[-1500:] if errlog.exists() else ''
            runlog = out / 'run.log'
            if runlog.exists():
                tail += '\n--- run.log ---\n' + runlog.read_text(encoding='utf-8', errors='replace')[-2500:]
            self.assertEqual(process.returncode, 0, tail)
            seen.discard(None)
            self.assertIn('reports', seen, seen)
            self.assertGreaterEqual(len(seen), 2, f'运行中应看到多个阶段：{seen}')


if __name__ == '__main__':
    unittest.main()
