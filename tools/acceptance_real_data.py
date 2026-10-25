"""Read-only real-data QC acceptance; never reports biological validation.

The supplied H5AD is hashed before/after. Outputs must be a new directory.
No download, input rewriting, velocity-layer invention, or biological accuracy claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
from cellflow.runner import run


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Output must be a new directory')
    before = sha256(args.input)
    provenance = run(args.input, {'method': 'qc', 'seed': 42, 'n_top_genes': 500,
                                 'n_pcs': 20, 'n_neighbors': 15, 'use_umap': False}, args.output)
    after = sha256(args.input)
    evidence = dict(source=args.source, input_sha256=before, input_unchanged=before == after,
                    status=provenance['status'], scope='real-data QC and report integration only',
                    biological_validation=False, velocity_validation=False,
                    multi_terminal_validation=False)
    (args.output / 'acceptance.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    if before != after or provenance['status'] != 'completed':
        raise RuntimeError(str(evidence))


if __name__ == '__main__':
    main()
