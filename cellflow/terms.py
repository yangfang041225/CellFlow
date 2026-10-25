"""极小的中文术语参考表。

原则：
- 只收录含义明确、无争议的通用术语/物种/组织；
- 精确匹配（大小写不敏感），不做模糊猜测；
- 返回结果一律标注“参考翻译”，界面不得把它当成已核实的生物学命名。

未收录的内容保持原文，绝不自动编造中文名。
"""
from __future__ import annotations

CURATED = {
    # 物种
    'human': '人',
    'homo sapiens': '人',
    'mouse': '小鼠',
    'mus musculus': '小鼠',
    'rat': '大鼠',
    'rattus norvegicus': '大鼠',
    # 组织 / 样本
    'blood': '血液',
    'pbmc': '外周血单个核细胞',
    'bone marrow': '骨髓',
    'brain': '脑',
    'cortex': '皮层',
    'liver': '肝',
    'lung': '肺',
    'kidney': '肾',
    'heart': '心脏',
    'skin': '皮肤',
    'tumor': '肿瘤',
    'tumour': '肿瘤',
    'organoid': '类器官',
    # 细胞类型
    't cell': 'T 细胞',
    'b cell': 'B 细胞',
    'nk cell': 'NK 细胞',
    'monocyte': '单核细胞',
    'macrophage': '巨噬细胞',
    'dendritic cell': '树突状细胞',
    'fibroblast': '成纤维细胞',
    'neuron': '神经元',
    'astrocyte': '星形胶质细胞',
    'endothelial cell': '内皮细胞',
    'epithelial cell': '上皮细胞',
    'hepatocyte': '肝细胞',
    # 方法与分析术语
    'umap': '统一流形近似与投影（UMAP）',
    'pca': '主成分分析（PCA）',
    'leiden': 'Leiden 社区发现',
    'paga': 'PAGA 群间连接',
    'dpt': '扩散拟时序（DPT）',
    'pseudotime': '拟时序',
    'velocity': 'RNA 速度',
    'spliced': '已剪接计数',
    'unspliced': '未剪接计数',
    'latent time': '潜在时间',
    'terminal state': '终末状态',
    'fate probability': '命运概率',
    'gene expression': '基因表达',
    'quality control': '质量控制',
}


def lookup(text):
    """精确匹配，返回参考中文；未收录返回 None。"""
    key = str(text or '').strip().lower()
    if not key:
        return None
    return CURATED.get(key)


def glossary():
    return [{'term': term, 'zh': zh, 'source': 'curated'} for term, zh in sorted(CURATED.items())]
