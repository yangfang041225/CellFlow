"""CellRank 官方参考 vs CellFlow：在 CellFlow 保存的同一预处理 AnnData 上对照。

流程：
1. 子进程运行 CellFlow `cellrank`，得到 analysis.h5ad 与 result.json(fate.probabilities)；
2. 子进程在这份 analysis.h5ad 上直接跑官方 CellRank（PseudotimeKernel + GPCCA）；
3. 父进程对齐终末类别，比较命运概率、吸收方程残差、终末状态数量。

失败/不一致必须报告，不用调参重跑掩盖。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

BASE_CONFIG = {
    'n_top_genes': 500,
    'n_neighbors': 15,
    'n_pcs': 20,
    'min_shared_counts': 10,
    'seed': 42,
    'use_umap': True,
    'raw_x_confirmed': True,
    'raw_su_confirmed': True,
    'group_key': 'clusters',
    'root_cell': None,
    'root_reason': 'Ductal 起点仅作为探索性先验，不是谱系追踪',
    'cellrank_n_states': 5,
    'cellrank_terminal': '',
}

TOLERANCE = 1e-6
RESIDUAL_TOLERANCE = 1e-8


def _worker_cellflow(subset: Path, config: dict, folder: Path) -> int:
    from cellflow.runner import run
    full = dict(config, method='cellrank')
    provenance = run(subset, full, folder / 'cellflow')
    (folder / 'cellflow_provenance.json').write_text(
        json.dumps({k: provenance.get(k) for k in ('status', 'failed_stage', 'error')},
                   ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if provenance.get('status') == 'completed' else 1


def _worker_reference(analysis: Path, config: dict, n_states: int, cluster_key: str,
                      folder: Path) -> int:
    import anndata as ad
    import cellrank as cr

    a = ad.read_h5ad(analysis)
    kernel = cr.kernels.PseudotimeKernel(a, time_key='dpt_pseudotime')
    kernel.compute_transition_matrix(n_jobs=1, backend='threading', show_progress_bar=False)
    estimator = cr.estimators.GPCCA(kernel)
    estimator.compute_schur(n_components=int(min(20, a.n_obs - 2)))
    estimator.compute_macrostates(n_states=int(n_states), cluster_key=cluster_key)
    fixed = [item.strip() for item in str(config.get('cellrank_terminal') or '').split(',')
             if item.strip()]
    if fixed:
        estimator.set_terminal_states(states=fixed, cluster_key=cluster_key)
    else:
        estimator.predict_terminal_states()
    estimator.compute_fate_probabilities(use_petsc=False, solver='direct', n_jobs=1,
                                         backend='threading', show_progress_bar=False)
    fp = estimator.fate_probabilities
    probs = np.asarray(fp.X, dtype=float)
    names = [str(x) for x in fp.names]
    np.savez_compressed(folder / 'reference_fate.npz', probs=probs,
                        cells=np.asarray(a.obs_names, dtype=str),
                        terminal_labels=np.asarray([str(v) if not __import__('pandas').isna(v) else ''
                                                    for v in estimator.terminal_states], dtype=str),
                        terminal_names=np.asarray(names, dtype=str))
    transition = kernel.transition_matrix
    import scipy.sparse as sp
    if not sp.issparse(transition):
        transition = sp.csr_matrix(np.asarray(transition))
    sp.save_npz(folder / 'transition.npz', transition.tocsr())
    return 0


def _run(command, cwd, timeout=3600):
    env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               MKL_NUM_THREADS='1', NUMBA_NUM_THREADS='1')
    proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=env, timeout=timeout)
    if proc.returncode != 0:
        return {'returncode': proc.returncode,
                'tail': ((proc.stdout or '') + (proc.stderr or ''))[-2500:]}
    return None


def _load_candidate(folder: Path):
    result = json.loads((folder / 'cellflow' / 'result.json').read_text(encoding='utf-8'))
    fate = result.get('fate') or {}
    probs = np.asarray(fate.get('probabilities'), dtype=float)
    names = [str(x) for x in fate.get('terminal_states') or []]
    return probs, names, fate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--subset', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=('cellflow', 'reference'))
    parser.add_argument('--config-json', type=Path)
    parser.add_argument('--worker-output', type=Path)
    parser.add_argument('--analysis', type=Path)
    parser.add_argument('--n-states', type=int)
    parser.add_argument('--cluster-key')
    parser.add_argument('--root-cell', default=None)
    parser.add_argument('--terminal', default='', help='固定终末类别，逗号分隔；留空则自动识别')
    args = parser.parse_args()
    if args.worker == 'cellflow':
        config = json.loads(args.config_json.read_text(encoding='utf-8'))
        return _worker_cellflow(args.subset, config, args.worker_output)
    if args.worker == 'reference':
        config = json.loads(args.config_json.read_text(encoding='utf-8'))
        return _worker_reference(args.analysis, config, args.n_states, args.cluster_key,
                                 args.worker_output)
    if not args.subset or not args.output:
        raise SystemExit('需要 --subset 与 --output')
    args.output.mkdir(parents=True, exist_ok=False)
    config = dict(BASE_CONFIG, cellrank_terminal=args.terminal or '')
    if args.root_cell:
        config['root_cell'] = args.root_cell
    else:
        import anndata as ad
        preview = ad.read_h5ad(args.subset)
        ductal = preview.obs_names[preview.obs['clusters'].astype(str) == 'Ductal']
        if not len(ductal):
            raise SystemExit('子集里没有 Ductal 细胞，不能设置 DPT 起点')
        config['root_cell'] = str(ductal[0])
    config_path = args.output / 'config.json'
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')

    command = [sys.executable, '-m', 'benchmarks.compare_cellrank', '--worker', 'cellflow',
               '--subset', str(args.subset), '--config-json', str(config_path),
               '--worker-output', str(args.output)]
    error = _run(command, Path.cwd())
    if error:
        report = {'ok': False, 'stage': 'cellflow', 'error': error}
    else:
        candidate_probs, candidate_names, fate = _load_candidate(args.output)
        n_states = int(fate.get('n_states') or config['cellrank_n_states'])
        cluster_key = fate.get('cluster_key') or config['group_key']
        analysis = args.output / 'cellflow' / 'analysis.h5ad'
        reference_folder = args.output / 'reference'
        reference_folder.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, '-m', 'benchmarks.compare_cellrank', '--worker', 'reference',
                   '--config-json', str(config_path), '--analysis', str(analysis),
                   '--n-states', str(n_states), '--cluster-key', str(cluster_key),
                   '--worker-output', str(reference_folder)]
        error = _run(command, Path.cwd())
        if error:
            report = {'ok': False, 'stage': 'reference', 'error': error}
        else:
            payload = np.load(reference_folder / 'reference_fate.npz', allow_pickle=False)
            try:
                from .fate_compare import compare
                import scipy.sparse as sp
                with np.load(args.output / 'cellflow' / 'fate.npz', allow_pickle=False) as candidate:
                    report = compare(candidate, payload, sp.load_npz(reference_folder / 'transition.npz'))
            except Exception as exc:
                report = {'ok': False, 'stage': 'compare',
                          'error': f'{type(exc).__name__}: {exc}'}
            report['candidate_terminal_states'] = candidate_names
            report['n_states'] = n_states
            report['cluster_key'] = cluster_key
            report['terminal_mode'] = ('fixed:' + args.terminal) if args.terminal else 'auto'
    (args.output / 'compare_cellrank.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
