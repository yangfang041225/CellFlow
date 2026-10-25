"""Real scVelo pancreas subset integration; never a lineage accuracy benchmark.

Downloads only into a new output directory. Seeds and sample size are fixed before
running; single-terminal or failed runs fail acceptance rather than being retuned.
"""
import argparse
import os
import shutil
import subprocess
import hashlib
import json
from pathlib import Path
import numpy as np
from cellflow.runner import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Output must be a new directory')
    args.output.mkdir(parents=True)
    import anndata as ad
    source = args.output / 'endocrinogenesis_day15.h5ad'
    url = 'https://github.com/theislab/scvelo_notebooks/raw/master/data/Pancreas/endocrinogenesis_day15.h5ad'
    temporary = source.with_suffix('.download')
    curl = shutil.which('curl.exe') or shutil.which('curl')
    if not curl:
        raise RuntimeError('curl is required for bounded, complete public-data download')
    subprocess.run([curl, '--fail', '--location', '--retry', '1', '--max-time', '180',
                    '--max-filesize', str(100 * 1024 * 1024), '--output', str(temporary), url],
                   check=True, timeout=400)
    a = ad.read_h5ad(temporary)  # includes HDF5 truncation checks before publication
    os.replace(temporary, source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if 'clusters' not in a.obs or not {'spliced', 'unspliced'} <= set(a.layers):
        raise ValueError('Curated dataset lacks expected annotations or count layers')
    # Stratified, deterministic subset; no expression alteration or artificial cells.
    rng = np.random.default_rng(42)
    indices = []
    for label in sorted(a.obs['clusters'].unique()):
        group = np.flatnonzero(a.obs['clusters'].to_numpy() == label)
        indices.extend(rng.choice(group, min(len(group), 75), replace=False))
    subset = a[np.sort(indices)].copy()
    subset.X = subset.layers['spliced'].copy()
    snapshot = args.output / 'subset.h5ad'
    subset.write_h5ad(snapshot)
    roots = subset.obs_names[subset.obs['clusters'].astype(str) == 'Ductal']
    if not len(roots):
        raise ValueError('No predeclared Ductal root candidate')
    evidence = dict(source='scvelo.datasets.pancreas; Bastidas-Ponce et al. 2019',
                    source_sha256=digest, cells=int(subset.n_obs),
                    genes=int(subset.n_vars), sampling='up to 75 cells per annotated cluster; seed 42',
                    biological_accuracy_validated=False, runs=[])
    for seed in (42, 43):
        config = dict(method='auto', seed=seed, raw_x_confirmed=True, raw_su_confirmed=True,
                      group_key='clusters', root_cell=str(roots[0]),
                      root_reason='Annotated Ductal cell; exploratory prior, not lineage tracing',
                      n_top_genes=1000, n_neighbors=30, n_pcs=30, min_shared_counts=20,
                      velocity_model='stochastic', cellrank_n_states=5, use_umap=True)
        folder = args.output / f'seed-{seed}'
        provenance = run(snapshot, config, folder)
        result = json.loads((folder / 'result.json').read_text(encoding='utf-8')) if (folder/'result.json').exists() else {}
        fate = result.get('fate') or {}
        n_terminal = len(fate.get('terminal_states') or [])
        evidence['runs'].append(dict(seed=seed, status=provenance['status'],
                                     n_terminal_categories=n_terminal,
                                     terminal_categories=fate.get('terminal_states'),
                                     accepted=provenance['status'] == 'completed' and n_terminal >= 2))
    evidence['source_unchanged'] = hashlib.sha256(source.read_bytes()).hexdigest() == digest
    evidence['ok'] = evidence['source_unchanged'] and all(r['accepted'] for r in evidence['runs'])
    (args.output / 'acceptance.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    if not evidence['ok']:
        raise RuntimeError(str(evidence))


if __name__ == '__main__':
    main()
