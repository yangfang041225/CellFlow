from pathlib import Path
import json
import numpy as np

def compare(left, right):
    """Agreement only; NEVER calls agreement accuracy or declares a winner."""
    left,right=Path(left),Path(right)
    read=lambda p,n: json.loads((p/n).read_text(encoding='utf-8'))
    a,b=read(left,'result.json'),read(right,'result.json')
    pa,pb=read(left,'provenance.json'),read(right,'provenance.json')
    ca,cb=read(left,'config.json'),read(right,'config.json')
    blockers=[]
    if a.get('synthetic') or b.get('synthetic'): blockers.append('模拟结果不进入真实模型比较')
    if pa['input_sha256']!=pb['input_sha256']: blockers.append('输入文件 SHA-256 不同')
    if pa.get('code_sha256')!=pb.get('code_sha256'): blockers.append('分析代码版本不同')
    ea,eb=read(left,'environment.json'),read(right,'environment.json')
    for package in ('numpy','scipy','scanpy','scvelo','anndata'):
        if ea.get(package)!=eb.get(package):blockers.append('关键依赖版本不同：'+package)
    for key in ('cells_hash','genes_hash','preprocessing_hash'):
        if not a.get(key) or a.get(key)!=b.get(key): blockers.append(key+' 缺失或不一致')
    for key in ('seed','n_top_genes','n_neighbors','n_pcs','min_shared_counts'):
        if ca[key]!=cb[key]: blockers.append('预处理/随机配置不同：'+key)
    for key in ('max_iter','max_epochs'):
        if ca[key]!=cb[key]:blockers.append('计算预算配置不同：'+key)
    if not a.get('velocity') or not b.get('velocity'): blockers.append('两者没有共同基因空间速度；拟时序、PAGA 和速度不可直接比较')
    if blockers:
        return dict(comparable=False,reasons=blockers,explanation='只展示各自诊断，不能排名。')
    with np.load(left/a['velocity'],allow_pickle=False) as x, np.load(right/b['velocity'],allow_pickle=False) as y:
        if not np.array_equal(x['cells'],y['cells']) or not np.array_equal(x['genes'],y['genes']):
            return dict(comparable=False,reasons=['速度矩阵 ID 顺序不一致'])
        v,w=x['velocity'].astype(float),y['velocity'].astype(float)
    norms=np.linalg.norm(v,axis=1)*np.linalg.norm(w,axis=1)
    valid=np.isfinite(v).all(1)&np.isfinite(w).all(1)&(norms>1e-12)
    if not valid.any(): return dict(comparable=False,reasons=['没有有效的配对速度向量'])
    cosine=np.einsum('ij,ij->i',v[valid],w[valid])/norms[valid]
    return dict(comparable=True,paired_cells=int(valid.sum()),excluded_cells=int((~valid).sum()),
        median_gene_space_cosine=float(np.median(cosine)),
        explanation='这是方向一致性，不是准确率；未比较不同模型似然、损失或潜在时间。需独立时间/谱系证据评价正确性。')
