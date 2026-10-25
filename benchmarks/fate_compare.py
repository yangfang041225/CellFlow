"""Independent boundary/equation verification using saved terminal assignments."""
import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import breadth_first_order
from .matrix_compare import validate_payload


def compare(candidate, reference, transition, tolerance=1e-6, residual_tolerance=1e-8):
    cp, cc, cn = validate_payload(candidate, 'probs', 'terminal_names')
    rp, rc, rn = validate_payload(reference, 'probs', 'terminal_names')
    if set(cc) != set(rc) or set(cn) != set(rn):
        raise ValueError('CellRank 细胞/终末类别集合不同')
    ci, ni = {c:i for i,c in enumerate(cc)}, {n:i for i,n in enumerate(cn)}
    cp = cp[np.ix_([ci[c] for c in rc], [ni[n] for n in rn])]
    labels = np.asarray(reference['terminal_labels']).astype(str)
    if labels.shape != rc.shape:
        raise ValueError('终末标签轴不匹配')
    boundary = np.column_stack([labels == n for n in rn]).astype(float)
    term = boundary.sum(1) == 1
    trans = ~term
    if not term.any() or not all((labels == n).any() for n in rn):
        raise ValueError('缺少实际终末边界')
    t = sparse.csr_matrix(transition, dtype=float).copy()
    t.eliminate_zeros()
    if t.shape != (len(rc), len(rc)) or not np.isfinite(t.data).all() or (t.data < 0).any():
        raise ValueError('转移矩阵无效')
    if not np.allclose(np.asarray(t.sum(1)).ravel(), 1, atol=residual_tolerance, rtol=0):
        raise ValueError('转移矩阵行和错误')
    graph = sparse.lil_matrix((len(rc)+1, len(rc)+1))
    graph[:len(rc), :len(rc)] = t.T
    graph[len(rc), np.flatnonzero(term)] = 1
    reached = breadth_first_order(graph.tocsr(), len(rc), directed=True, return_predecessors=False)
    if not np.isin(np.flatnonzero(trans), reached).all():
        raise ValueError('不可达瞬态闭类')
    residuals = {}
    for role, p in (('candidate', cp), ('reference', rp)):
        if not np.isfinite(p).all() or (p < -residual_tolerance).any() or (p > 1+residual_tolerance).any():
            raise ValueError('概率非有限或越界')
        if not np.allclose(p.sum(1), 1, atol=residual_tolerance, rtol=0):
            raise ValueError('概率行和错误')
        if not np.allclose(p[term], boundary[term], atol=residual_tolerance, rtol=0):
            raise ValueError('终末边界错误')
        # (I-Q)B - R; R aggregates every terminal cell into its declared category.
        q = t[trans][:, trans]
        r = t[trans][:, term] @ boundary[term]
        error = p[trans] - q @ p[trans] - r
        residuals[role] = float(np.max(np.abs(error))) if error.size else 0.0
    error = float(np.max(np.abs(cp-rp)))
    return dict(ok=bool(error <= tolerance and max(residuals.values()) <= residual_tolerance),
                shape=list(cp.shape), cells=len(rc), common_terminal_states=list(rn),
                candidate_terminal_states=list(cn), reference_terminal_states=list(rn),
                max_abs_probability_diff=error, absorption_residual=max(residuals.values()),
                equation_residuals=residuals, terminal_cells=int(term.sum()),
                boundary_source='actual estimator terminal_states; never inferred from probability')
