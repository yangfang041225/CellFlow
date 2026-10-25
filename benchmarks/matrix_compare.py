"""Strict identity alignment and transparent coverage for numerical comparisons."""
import numpy as np


def validate_payload(payload, value_key='velocity', feature_key='genes'):
    matrix = np.asarray(payload[value_key], dtype=float)
    cells, features = np.asarray(payload['cells']).astype(str), np.asarray(payload[feature_key]).astype(str)
    if cells.ndim != 1 or features.ndim != 1 or matrix.shape != (len(cells), len(features)):
        raise ValueError('矩阵与 ID 轴维度不一致')
    for ids in (cells, features):
        if not len(ids) or len(set(ids)) != len(ids) or any(not x.strip() for x in ids):
            raise ValueError('空轴、空 ID 或重复 ID')
    return matrix, cells, features


def compare_velocity(reference, candidate):
    left, lc, lg = validate_payload(reference)
    right, rc, rg = validate_payload(candidate)
    if set(lc) != set(rc):
        raise ValueError('细胞集合不一致')
    ci = {c:i for i,c in enumerate(rc)}
    right = right[[ci[c] for c in lc]]
    li, ri = {g:i for i,g in enumerate(lg)}, {g:i for i,g in enumerate(rg)}
    common = [g for g in lg if g in ri]
    reasons = []
    for gene in dict.fromkeys([*lg, *rg]):
        why = []
        a = left[:, li[gene]] if gene in li else None
        b = right[:, ri[gene]] if gene in ri else None
        for side, v in (('reference', a), ('candidate', b)):
            if v is None:
                why.append(side + '_missing')
            elif not np.isfinite(v).all():
                why.append(side + '_nonfinite')
            elif (v == 0).all():
                why.append(side + '_all_zero')
        if why:
            reasons.append({'gene': str(gene), 'reasons': why})
    if not common:
        raise ValueError('没有共同基因')
    a = left[:, [li[g] for g in common]]
    b = right[:, [ri[g] for g in common]]
    finite = np.isfinite(a) & np.isfinite(b)
    masks_match = ((np.isfinite(a) == np.isfinite(b)) & (np.isnan(a) == np.isnan(b)) &
                   (np.isposinf(a) == np.isposinf(b)) & (np.isneginf(a) == np.isneginf(b)))
    errors = np.abs(a[finite] - b[finite])
    x, y = a[finite], b[finite]
    correlation = None
    if len(x) >= 2 and np.ptp(x) > 0 and np.ptp(y) > 0:
        correlation = float(np.corrcoef(x, y)[0, 1])
    missing_ref, missing_cand = [str(g) for g in rg if g not in li], [str(g) for g in lg if g not in ri]
    return dict(shape=list(a.shape), reference_shape=list(left.shape), candidate_shape=list(right.shape),
                max_abs_diff=float(errors.max()) if errors.size else None,
                mean_abs_diff=float(errors.mean()) if errors.size else None, pearson=correlation,
                pearson_status='defined' if correlation is not None else 'undefined_constant_or_insufficient',
                finite_mismatch=int((~masks_match).sum()),
                jointly_nonfinite_elements=int((~np.isfinite(a) & ~np.isfinite(b)).sum()),
                compared_finite_elements=int(finite.sum()), total_aligned_elements=int(a.size),
                fully_finite_genes=int(finite.all(0).sum()),
                finite_gene_coverage=float(finite.all(0).sum() / len(lg)),
                common_genes=len(common), excluded_genes=len(missing_cand),
                reference_missing=missing_ref, candidate_missing=missing_cand,
                coverage_complete=not missing_ref and not missing_cand,
                gene_classification=reasons)
