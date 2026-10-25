"""veloVI 训练诊断与多随机种子稳定性（真实短训练，不声称收敛）。

每个种子在隔离环境 `.venv-velovi` 中用独立子进程运行 CellFlow CLI；
输出：训练历史、ELBO、epochs、后验不确定性文件、跨种子速度方向一致性。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp


def _python(root: Path) -> Path:
    candidates = [root / '.venv-velovi' / 'Scripts' / 'python.exe',
                  root / '.venv-velovi' / 'bin' / 'python']
    for item in candidates:
        if item.is_file():
            return item
    raise FileNotFoundError('未找到 .venv-velovi 解释器；本诊断需要隔离环境')


def run_seed(root: Path, subset: Path, output: Path, config: dict, max_epochs: int,
             timeout=3600) -> dict:
    import subprocess
    folder = output / f'seed-{config["seed"]}'
    cfg = dict(config, method='velovi', max_epochs=int(max_epochs))
    config_path = output / f'config-{config["seed"]}.json'
    config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    command = [str(_python(root)), '-m', 'cellflow.cli', 'run', '--input', str(subset),
               '--config', str(config_path), '--output', str(folder)]
    proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout, cwd=str(root))
    record = {'seed': config['seed'], 'returncode': proc.returncode,
              'stderr_tail': (proc.stderr or '')[-1500:]}
    result_path = folder / 'result.json'
    protocol_path = folder / 'velovi_protocol.json'
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding='utf-8'))
        record['status'] = 'completed'
        record['metrics'] = result.get('metrics') or {}
        record['protocol'] = json.loads(protocol_path.read_text(encoding='utf-8')) \
            if protocol_path.is_file() else {}
        uncertainty = folder / 'velocity_uncertainty.npz'
        if uncertainty.is_file():
            payload = np.load(uncertainty, allow_pickle=False)
            std = np.asarray(payload['std'], dtype=float)
            record['uncertainty'] = {
                'shape': list(std.shape),
                'finite': bool(np.isfinite(std).all()),
                'mean': float(np.nanmean(std)),
                'median': float(np.nanmedian(std)),
                'max': float(np.nanmax(std)),
            }
        else:
            record['uncertainty'] = None
    else:
        record['status'] = 'failed'
    return record


def _load_velocity(folder: Path):
    path = folder / 'velocity.npz'
    if not path.is_file():
        return None
    payload = np.load(path, allow_pickle=False)
    return {'velocity': np.asarray(payload['velocity'], dtype=float),
            'cells': payload['cells'].astype(str), 'genes': payload['genes'].astype(str)}


def _direction_agreement(left: dict, right: dict):
    cells = left['cells']
    if not np.array_equal(cells, right['cells']):
        index = {cell: i for i, cell in enumerate(right['cells'])}
        if set(cells) != set(right['cells']):
            return None
        right = dict(right, velocity=right['velocity'][[index[c] for c in cells]])
    genes = [g for g in left['genes'] if g in set(right['genes'])]
    if not genes:
        return None
    left_index = {g: i for i, g in enumerate(left['genes'])}
    right_index = {g: i for i, g in enumerate(right['genes'])}
    a = left['velocity'][:, [left_index[g] for g in genes]]
    b = right['velocity'][:, [right_index[g] for g in genes]]
    norms = np.linalg.norm(a, axis=0) * np.linalg.norm(b, axis=0)
    valid = np.isfinite(a).all(0) & np.isfinite(b).all(0) & (norms > 1e-12)
    if not valid.any():
        return None
    cosine = np.einsum('ij,ij->j', a[:, valid], b[:, valid]) / norms[valid]
    return {'genes': int(valid.sum()), 'median_gene_cosine': float(np.median(cosine)),
            'fraction_positive': float((cosine > 0).mean())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--subset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', default='42,123,2026')
    parser.add_argument('--max-epochs', type=int, default=2)
    parser.add_argument('--n-top-genes', type=int, default=500)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('输出目录必须不存在，避免覆盖旧证据')
    args.output.mkdir(parents=True)
    root = Path(__file__).resolve().parents[1]
    config = {'n_top_genes': args.n_top_genes, 'n_neighbors': 15, 'n_pcs': 20,
              'min_shared_counts': 10, 'use_umap': True, 'raw_x_confirmed': True,
              'raw_su_confirmed': True, 'group_key': 'clusters'}
    runs = []
    for seed in [int(item) for item in args.seeds.split(',') if item.strip()]:
        print(f'[veloVI] seed {seed} max_epochs={args.max_epochs}', flush=True)
        runs.append(run_seed(root, args.subset, args.output, dict(config, seed=seed),
                             args.max_epochs))
        item = runs[-1]
        print(json.dumps({'seed': item['seed'], 'status': item['status'],
                          'epochs': item.get('metrics', {}).get('epochs_ran'),
                          'elbo': item.get('metrics', {}).get('elbo_train_final'),
                          'uncertainty': bool(item.get('uncertainty'))}, ensure_ascii=False))
    velocities = {}
    for item in runs:
        loaded = _load_velocity(args.output / f"seed-{item['seed']}")
        if loaded is not None:
            velocities[item['seed']] = loaded
    comparisons = []
    seeds = sorted(velocities)
    for i, left in enumerate(seeds):
        for right in seeds[i + 1:]:
            agreement = _direction_agreement(velocities[left], velocities[right])
            if agreement:
                comparisons.append({'a': left, 'b': right, **agreement})
            else:
                comparisons.append({'a': left, 'b': right, 'error': '无法对齐/无有效基因'})
    report = {
        'subset': str(args.subset), 'max_epochs': args.max_epochs,
        'config': config, 'runs': runs,
        'direction_agreement': comparisons,
        'ok': all(item['status'] == 'completed' for item in runs),
        'note': ('短训练只验证接口、诊断与稳定性，不能证明收敛或生物学准确；'
                 '训练轮数不等于收敛，unvalidated 能力不得进入 L2/L3 声明。'),
    }
    (args.output / 'velovi_diagnostics.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': report['ok'],
                      'seeds': [item['seed'] for item in runs]}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
