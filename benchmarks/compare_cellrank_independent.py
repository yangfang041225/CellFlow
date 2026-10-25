"""CellRank 独立预处理对照：参考进程从原始子集重新走 CellFlow 等价预处理。

与 compare_cellrank.py 的区别：不再共享 CellFlow 的 analysis.h5ad，而是独立执行
normalize_total/log1p/HVG/PCA/邻居/UMAP/DPT，再跑官方 CellRank。
用于检查“候选与参考是否只因共享中间文件而一致”。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np


def _worker(subset: Path, config: dict, folder: Path) -> int:
    import anndata as ad
    import scanpy as sc
    import scipy.sparse as sp
    import cellrank as cr

    a = ad.read_h5ad(subset)
    values = a.X.data if sp.issparse(a.X) else np.asarray(a.X)
    integer = np.allclose(np.asarray(values, dtype=float), np.round(np.asarray(values, dtype=float)),
                          atol=1e-8)
    if integer:
        sc.pp.normalize_total(a, target_sum=1e4)
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
    root = config['root_cell']
    a.uns['iroot'] = int(a.obs_names.get_loc(root))
    n_dcs = int(min(10, a.n_obs - 2))
    sc.tl.diffmap(a, n_comps=n_dcs, random_state=config['seed'])
    sc.tl.dpt(a, n_dcs=n_dcs, n_branchings=0)

    kernel = cr.kernels.PseudotimeKernel(a, time_key='dpt_pseudotime')
    kernel.compute_transition_matrix(n_jobs=1, backend='threading', show_progress_bar=False)
    estimator = cr.estimators.GPCCA(kernel)
    estimator.compute_schur(n_components=int(min(20, a.n_obs - 2)))
    estimator.compute_macrostates(n_states=int(config['cellrank_n_states']),
                                  cluster_key=config['group_key'])
    fixed = [item.strip() for item in str(config.get('cellrank_terminal') or '').split(',')
             if item.strip()]
    if fixed:
        estimator.set_terminal_states(states=fixed, cluster_key=config['group_key'])
    else:
        estimator.predict_terminal_states()
    estimator.compute_fate_probabilities(use_petsc=False, solver='direct', n_jobs=1,
                                         backend='threading', show_progress_bar=False)
    fp = estimator.fate_probabilities
    probs = np.asarray(fp.X, dtype=float)
    names = [str(x) for x in fp.names]
    transition = kernel.transition_matrix
    if not sp.issparse(transition):
        transition = sp.csr_matrix(np.asarray(transition))
    sp.save_npz(folder / 'transition.npz', transition.tocsr())
    np.savez_compressed(folder / 'reference_fate.npz', probs=probs,
                        cells=np.asarray(a.obs_names, dtype=str),
                        terminal_labels=np.asarray([str(v) if not __import__('pandas').isna(v) else ''
                                                    for v in estimator.terminal_states], dtype=str),
                        terminal_names=np.asarray(names, dtype=str))
    (folder / 'terminal.json').write_text(json.dumps(names, ensure_ascii=False), encoding='utf-8')
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--subset', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--terminal', default='')
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--config-json', type=Path)
    parser.add_argument('--worker-output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.worker:
        config = json.loads(args.config_json.read_text(encoding='utf-8'))
        args.worker_output.mkdir(parents=True, exist_ok=True)
        return _worker(args.subset, config, args.worker_output)
    args.output.mkdir(parents=True, exist_ok=False)
    result = json.loads((args.candidate / 'cellflow' / 'result.json').read_text(encoding='utf-8'))
    fate = result.get('fate') or {}
    candidate_probs = np.asarray(fate.get('probabilities'), dtype=float)
    candidate_names = [str(x) for x in fate.get('terminal_states') or []]
    if args.terminal:
        wanted = {item.strip() for item in args.terminal.split(',') if item.strip()}
        keep = [i for i, name in enumerate(candidate_names) if name in wanted]
        candidate_names = [candidate_names[i] for i in keep]
        candidate_probs = candidate_probs[:, keep]
    config = {k: result.get('config', {}).get(k) for k in
              ('n_top_genes', 'n_neighbors', 'n_pcs', 'seed', 'use_umap')}
    if not config.get('n_top_genes'):
        config = {'n_top_genes': 500, 'n_neighbors': 15, 'n_pcs': 20, 'seed': 42, 'use_umap': True}
    import anndata as ad
    subset = ad.read_h5ad(args.subset)
    ductal = subset.obs_names[subset.obs['clusters'].astype(str) == 'Ductal']
    config.update({'method': 'cellrank', 'group_key': 'clusters', 'cellrank_n_states': 5,
                   'root_cell': str(ductal[0]), 'cellrank_terminal': args.terminal or ''})
    config_path = args.output / 'config.json'
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    command = [sys.executable, '-m', 'benchmarks.compare_cellrank_independent',
               '--subset', str(args.subset), '--candidate', str(args.candidate),
               '--output', str(args.output), '--worker',
               '--config-json', str(config_path), '--worker-output', str(args.output / 'reference')]
    env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               MKL_NUM_THREADS='1', NUMBA_NUM_THREADS='1')
    proc = subprocess.run(command, cwd=str(root), capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=env, timeout=1800)
    if proc.returncode != 0:
        report = {'ok': False, 'stage': 'reference',
                  'tail': ((proc.stdout or '') + (proc.stderr or ''))[-2500:]}
    else:
        payload = np.load(args.output / 'reference' / 'reference_fate.npz', allow_pickle=False)
        from .fate_compare import compare
        import scipy.sparse as sp
        with np.load(args.candidate / 'cellflow' / 'fate.npz', allow_pickle=False) as candidate:
            report = compare(candidate, payload, sp.load_npz(args.output / 'reference' / 'transition.npz'))
        report['reference_preprocessing'] = 'independent: normalize_total/log1p/HVG/PCA/neighbors/DPT'
    (args.output / 'compare_cellrank_independent.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
