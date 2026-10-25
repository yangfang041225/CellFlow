"""Bounded absorbing-chain solver; columns represent terminal categories."""
import numpy as np
import pandas as pd
from scipy import sparse
from scipy.sparse.csgraph import breadth_first_order


def solve(kernel, terms, limit=1200, tolerance=1e-8):
    labels = pd.Series(terms)
    categories = [x for x in labels.cat.categories if (labels == x).any()]
    names = [str(x) for x in categories]
    if len(set(names)) != len(names):
        raise ValueError('Terminal category names collide after conversion to text')
    terminal = labels.notna().to_numpy()
    trans = np.flatnonzero(~terminal)
    if not names or len(trans) > limit:
        raise ValueError('No terminal states or dense absorption size limit exceeded')
    matrix = sparse.csr_matrix(kernel.transition_matrix, dtype=float)
    if matrix.shape != (len(labels), len(labels)):
        raise ValueError('Transition/terminal dimensions differ')
    if not np.isfinite(matrix.data).all() or (matrix.data < 0).any():
        raise ValueError('Transition matrix must be finite and nonnegative')
    if not np.allclose(np.asarray(matrix.sum(axis=1)).ravel(), 1, atol=tolerance, rtol=0):
        raise ValueError('Transition matrix rows must sum to one')
    # Reverse traversal from a virtual terminal node detects closed transient classes.
    graph = sparse.lil_matrix((len(labels) + 1, len(labels) + 1))
    graph[:len(labels), :len(labels)] = matrix.T
    graph[len(labels), np.flatnonzero(terminal)] = 1
    reachable = breadth_first_order(graph.tocsr(), len(labels), directed=True,
                                    return_predecessors=False)
    if not np.isin(trans, reachable).all():
        raise ValueError('Transient cells cannot reach any terminal category')
    boundary = np.column_stack([(labels == category).to_numpy(dtype=float) for category in categories])
    q = matrix[trans][:, trans].toarray()
    r = np.asarray(matrix[trans] @ boundary)
    operator = np.eye(len(trans)) - q
    solution = np.linalg.solve(operator, r)
    residual = float(np.max(np.abs(operator @ solution - r))) if solution.size else 0.0
    if residual > tolerance:
        raise ValueError('Absorption equation residual exceeds tolerance')
    probs = boundary.copy()
    probs[trans] = solution
    if (not np.isfinite(probs).all() or (probs < -tolerance).any()
            or (probs > 1 + tolerance).any()
            or not np.allclose(probs.sum(axis=1), 1, atol=tolerance, rtol=0)):
        raise ValueError('Absorption probabilities exceed numerical tolerance')
    negatives = int((probs < 0).sum())
    # Only rounding noise within the explicit tolerance is corrected; no renormalization.
    kernel._cellflow_absorption_diagnostics = dict(
        solver='category_aggregated_dense', tolerance=tolerance,
        equation_residual_max=residual, row_sum_error_max=float(np.max(np.abs(probs.sum(axis=1)-1))),
        minimum_probability=float(probs.min()), corrected_negative_entries=negatives,
        unreachable_cells=0, n_terminal_categories=len(names), n_transient_cells=len(trans))
    probs = np.clip(probs, 0, 1)
    return names, probs, {str(category): int((labels == category).sum()) for category in categories}, negatives
