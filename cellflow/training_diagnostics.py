"""Diagnostics describe observations and never infer convergence from an epoch count."""
import numpy as np


def posterior_std(samples, n_cells, n_genes, n_samples):
    values = np.asarray(samples, dtype=float)
    if values.shape == (n_samples, n_cells, n_genes):
        axis = 0
    elif values.shape == (n_cells, n_genes, n_samples):
        axis = 2
    else:
        raise ValueError('Posterior samples unavailable or shape ambiguous: ' + str(values.shape))
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite posterior samples')
    return values.std(axis=axis, ddof=1)
