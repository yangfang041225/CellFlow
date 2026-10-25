"""官方 scVelo 参考实现 vs CellFlow 适配器：同输入/同参数数值对照。

参考与候选分别在独立子进程中运行（避免共享 BLAS/numba 状态导致的原生崩溃），
父进程只负责对齐与统计。用法：

  python -m benchmarks.compare_scvelo --subset data/benchmarks/pancreas_subset.h5ad \
      --output data/benchmarks/compare_scvelo --modes deterministic,stochastic
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
import anndata as ad
import scipy.sparse as sp

MODE_METHOD = {
    'deterministic': 'scvelo_deterministic',
    'stochastic': 'scvelo_stochastic',
    'dynamical': 'scvelo_dynamical',
}

BASE_CONFIG = {
    'n_top_genes': 500,
    'n_neighbors': 15,
    'n_pcs': 20,
    'min_shared_counts': 10,
    'seed': 42,
    'use_umap': True,
    'max_iter': 30,
    'raw_x_confirmed': True,
    'raw_su_confirmed': True,
    'group_key': 'clusters',
}

TOLERANCE = {'deterministic': 1e-6, 'stochastic': 1e-6, 'dynamical': 1e-5}


def _integer_like(matrix) -> bool:
    values = matrix.data if sp.issparse(matrix) else np.asarray(matrix)
    values = np.asarray(values, dtype=float)
    finite = values[np.isfinite(values)]
    return bool(finite.size and np.allclose(finite, np.round(finite), atol=1e-8))


def _prepare_reference(path: Path, config: dict):
    import scanpy as sc
    import scvelo as scv

    a = ad.read_h5ad(path)
    if not _integer_like(a.X):
        total = a.layers['spliced'] + a.layers['unspliced']
        a.X = sp.csr_matrix(total) if sp.issparse(total) else np.asarray(total)
    scv.pp.filter_genes(a, min_shared_counts=config['min_shared_counts'])
    scv.pp.normalize_per_cell(a, counts_per_cell_after=1e4,
                              layers=['spliced', 'unspliced'], enforce=True)
    sc.pp.log1p(a)
    n_top = int(min(config['n_top_genes'], a.n_vars))
    sc.pp.highly_variable_genes(a, n_top_genes=n_top, flavor='seurat')
    a = a[:, a.var['highly_variable'].to_numpy()].copy()
    n_pcs = int(min(config['n_pcs'], a.n_vars - 1, a.n_obs - 1))
    sc.pp.pca(a, n_comps=n_pcs, random_state=config['seed'])
    k = int(min(config['n_neighbors'], a.n_obs - 1))
    sc.pp.neighbors(a, n_neighbors=k, n_pcs=n_pcs, random_state=config['seed'])
    if config.get('use_umap') and a.n_obs >= 50:
        sc.tl.umap(a, random_state=config['seed'])
    scv.pp.moments(a, n_pcs=None, n_neighbors=None)
    return a


def _reference_run(path: Path, mode: str, config: dict) -> dict:
    import scvelo as scv

    a = _prepare_reference(path, config)
    if mode == 'dynamical':
        scv.tl.recover_dynamics(a, n_jobs=1, max_iter=config['max_iter'],
                                show_progress_bar=False)
    scv.tl.velocity(a, mode=mode)
    matrix = a.layers['velocity']
    matrix = matrix.toarray() if sp.issparse(matrix) else np.asarray(matrix)
    scv.tl.velocity_graph(a, n_jobs=1, show_progress_bar=False)
    scv.tl.velocity_confidence(a)
    metrics = {key + '_median': float(np.nanmedian(np.asarray(a.obs[key], dtype=float)))
               for key in ('velocity_length', 'velocity_confidence')
               if key in a.obs}
    return {'velocity': matrix, 'cells': np.asarray(a.obs_names, dtype=str),
            'genes': np.asarray(a.var_names, dtype=str), 'metrics': metrics}


def _cellflow_run(path: Path, mode: str, config: dict, output: Path) -> dict:
    from cellflow.runner import run
    full = dict(config)
    full['method'] = MODE_METHOD[mode]
    provenance = run(path, full, output)
    if provenance.get('status') != 'completed':
        return {'error': provenance.get('error') or str(provenance)[:500],
                'provenance': {k: provenance.get(k) for k in ('status', 'failed_stage', 'error')}}
    payload = np.load(output / 'velocity_full.npz', allow_pickle=False)
    result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    metrics = {key: value for key, value in (result.get('metrics') or {}).items()
               if key.endswith('_median') and 'velocity' in key}
    return {'velocity': payload['velocity'], 'cells': payload['cells'].astype(str),
            'genes': payload['genes'].astype(str), 'metrics': metrics,
            'provenance': {k: provenance.get(k) for k in
                           ('status', 'failed_stage', 'elapsed_seconds')}}


def _save(result: dict, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(folder / 'velocity.npz', velocity=np.asarray(result['velocity']),
                        cells=np.asarray(result['cells'], dtype=str),
                        genes=np.asarray(result['genes'], dtype=str))
    (folder / 'metrics.json').write_text(json.dumps(result.get('metrics') or {},
                                                    ensure_ascii=False, indent=2), encoding='utf-8')


def _worker(kind: str, path: Path, mode: str, config: dict, folder: Path) -> int:
    try:
        if kind == 'reference':
            result = _reference_run(path, mode, config)
        elif kind == 'candidate':
            result = _cellflow_run(path, mode, config, folder / 'cellflow')
        else:
            raise SystemExit('kind 必须是 reference/candidate')
    except Exception as exc:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'error.json').write_text(json.dumps(
            {'worker': kind, 'mode': mode, 'error': f'{type(exc).__name__}: {exc}'},
            ensure_ascii=False, indent=2), encoding='utf-8')
        return 1
    _save(result, folder)
    return 0


def _load(folder: Path) -> dict:
    error = folder / 'error.json'
    if error.is_file():
        return {'error': json.loads(error.read_text(encoding='utf-8'))}
    payload = np.load(folder / 'velocity.npz', allow_pickle=False)
    metrics = json.loads((folder / 'metrics.json').read_text(encoding='utf-8'))
    return {'velocity': payload['velocity'], 'cells': payload['cells'].astype(str),
            'genes': payload['genes'].astype(str), 'metrics': metrics}


def _compare_arrays(reference, candidate):
    from .matrix_compare import compare_velocity
    return compare_velocity(reference, candidate)


def _run_worker(kind, subset, mode, config, folder):
    folder.mkdir(parents=True, exist_ok=True)
    config_path = folder / 'config.json'
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    command = [sys.executable, '-m', 'benchmarks.compare_scvelo', '--worker', kind,
               '--subset', str(subset), '--mode', mode, '--config-json', str(config_path),
               '--worker-output', str(folder)]
    env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               MKL_NUM_THREADS='1', NUMBA_NUM_THREADS='1')
    proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', env=env, timeout=1800)
    if proc.returncode != 0:
        tail = ((proc.stdout or '') + (proc.stderr or ''))[-2000:]
        return {'error': {'worker': kind, 'mode': mode, 'returncode': proc.returncode,
                          'tail': tail}}
    return _load(folder)


def compare_mode(subset: Path, mode: str, config: dict, folder: Path) -> dict:
    ref_folder = folder / f'ref-{mode}'
    cand_folder = folder / f'cand-{mode}'
    reference = _run_worker('reference', subset, mode, config, ref_folder)
    candidate = _run_worker('candidate', subset, mode, config, cand_folder)
    if 'error' in reference:
        return {'mode': mode, 'ok': False, 'error': reference['error']}
    if 'error' in candidate:
        return {'mode': mode, 'ok': False, 'error': candidate['error']}
    try:
        stats = _compare_arrays(reference, candidate)
    except Exception as exc:
        return {'mode': mode, 'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    tolerance = TOLERANCE.get(mode, 1e-6)
    ok = bool(stats['coverage_complete'] and stats['finite_mismatch'] == 0
              and stats['max_abs_diff'] is not None and stats['max_abs_diff'] <= tolerance)
    return {'mode': mode, 'ok': ok, 'tolerance': tolerance, **stats,
            'metrics': {'reference': reference['metrics'], 'cellflow': candidate['metrics']},
            'reference': {'cells': int(len(reference['cells'])),
                          'genes': int(len(reference['genes']))},
            'config': {k: config[k] for k in sorted(config)}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--subset', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--modes', default='deterministic,stochastic')
    parser.add_argument('--n-top-genes', type=int, default=BASE_CONFIG['n_top_genes'])
    parser.add_argument('--worker', choices=('reference', 'candidate'))
    parser.add_argument('--mode', choices=tuple(MODE_METHOD))
    parser.add_argument('--config-json', type=Path)
    parser.add_argument('--worker-output', type=Path)
    args = parser.parse_args()
    if args.worker:
        config = json.loads(args.config_json.read_text(encoding='utf-8'))
        return _worker(args.worker, args.subset, args.mode, config, args.worker_output)
    if not args.subset or not args.output:
        raise SystemExit('需要 --subset 与 --output')
    args.output.mkdir(parents=True, exist_ok=False)
    config = dict(BASE_CONFIG, n_top_genes=args.n_top_genes)
    results = []
    for mode in [item.strip() for item in args.modes.split(',') if item.strip()]:
        if mode not in MODE_METHOD:
            raise SystemExit(f'未知模式：{mode}')
        print(f'[{mode}] reference + CellFlow (isolated subprocesses) ...', flush=True)
        results.append(compare_mode(args.subset, mode, config, args.output))
        short = {key: results[-1].get(key) for key in
                 ('mode', 'ok', 'max_abs_diff', 'mean_abs_diff', 'pearson', 'error')}
        print(json.dumps(short, ensure_ascii=False), flush=True)
    report = {'subset': str(args.subset), 'config': config, 'modes': results,
              'ok': all(item['ok'] for item in results)}
    (args.output / 'compare_scvelo.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': report['ok'], 'modes': [item['mode'] for item in results]}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
