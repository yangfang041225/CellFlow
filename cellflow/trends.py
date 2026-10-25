import numpy as np
from scipy import sparse
from scipy.stats import spearmanr

def descriptive_trends(data, result, config):
    """Exploratory binned expression association. No p-values or driver claims."""
    if result.get('time') is None or not config.get('raw_x_confirmed'):
        return []
    t=np.asarray([np.nan if v is None else v for v in result['time']],dtype=float)
    valid=np.isfinite(t)
    if valid.sum()<20 or np.unique(t[valid]).size<4:return []
    x=sparse.csr_matrix(data.X,dtype=float)
    x=sparse.diags(1e4/np.maximum(np.asarray(x.sum(1)).ravel(),1))@x
    x.data=np.log1p(x.data);x=x.tocsr()
    candidates=[str(r['gene']) for r in result.get('gene_table',[]) if r.get('gene') in data.genes][:15]
    if not candidates:
        mean=np.asarray(x.mean(0)).ravel()
        variance=np.asarray(x.multiply(x).mean(0)).ravel()-mean**2
        candidates=list(data.genes[np.argsort(variance)[-15:]])
    idx=np.flatnonzero(valid);ordered=idx[np.argsort(t[idx])]
    bins=np.array_split(ordered, min(12,len(ordered)//5))
    out=[]
    for gene in candidates:
        j=int(np.flatnonzero(data.genes==gene)[0]);expr=x[:,j].toarray().ravel()
        if np.std(expr[valid])==0:continue
        rho=float(spearmanr(t[valid],expr[valid]).statistic)
        if not np.isfinite(rho):continue
        out.append(dict(gene=str(gene),exploratory_spearman=rho,
            bins=[dict(time=float(t[b].mean()),mean_expression=float(expr[b].mean()),n=int(len(b))) for b in bins],
            expression_unit='log1p(CP10k)',evidence='探索性同数据关联；非独立验证；不作因果解释'))
    return sorted(out,key=lambda r:abs(r['exploratory_spearman']),reverse=True)[:5]
