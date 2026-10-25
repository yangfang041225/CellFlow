> 独立审查更正：本文保留 Phase II 开发阶段记录，不是本轮验收结论。当前缺陷、整改和使用边界见 [独立审查摘要](independent_review.md)。

# Phase II · P0 真实 RNA velocity 基准（阶段记录）

日期：2026-10-26（Asia/Shanghai）
基线：PR #1 `4fcb61f`；工作分支 `phase2/scientific-validation`
状态：**P0-1 / P0-2 / P0-3 完成；P0-4 尚未完成**

---

## 1. 数据集（P0-1）

| 项 | 值 |
|---|---|
| 数据集 | scVelo 官方胰腺内分泌发生（E15.5） |
| 来源 | `https://github.com/theislab/scvelo_notebooks/raw/master/data/Pancreas/endocrinogenesis_day15.h5ad` |
| 引用 | Bastidas-Ponce et al. 2019, Developmental Cell |
| 文件大小 | 52,477,898 字节 |
| SHA-256 | `9e3e459eca00ba06b496ec80def32941b5b2889918720e3e7aa6ffb811fbe7c6` |
| 形状 | 3,696 细胞 × 27,998 基因 |
| 层 | `spliced`, `unspliced`（float32，非负，无 NaN/Inf） |
| 分群 | Ductal 916、Ngn3 high EP 642、Pre-endocrine 592、Beta 591、Alpha 481、Ngn3 low EP 262、Epsilon 142、Delta 70 |

### 下载与完整性

- 使用 `benchmarks/fetch.py`：先写 `.part`，HTTP Range 续传，有限重试（3 次），
  校验字节数与 SHA-256，HDF5 可读后才原子改名为正式文件。
- 实际下载：curl 续传（本次网络约 101 KB/s），52 MB 完整；`anndata.read_h5ad` 通过。
- **失败处理**：若哈希/大小不符，抛 `IntegrityError` 且不重试、不发布半成品；
  已存在但哈希不符的文件会被拒绝而不是静默覆盖。
- 数据落在 `data/benchmarks/`（gitignore），不进入 GitHub。

### 本地缓存清单

`data/benchmarks/manifest.json` 记录来源、大小、SHA-256、形状、层、分群计数与校验时间。

---

## 2. 输入契约（P0-2）

`benchmarks/contracts.py` 对完整数据检查：

- `obs × var` 形状、唯一细胞/基因 ID、空标识符
- `X` / `spliced` / `unspliced` 的有限性、非负性、dtype、范围
- 若要求剪接层：必须同时存在 `spliced` 与 `unspliced`
- `obsm` 与细胞数对齐；`Ms/Mu` 与 `X` 形状一致

对完整胰腺数据的结果：

```json
{
  "ok": true,
  "issues": [],
  "warnings": [],
  "shape": [3696, 27998],
  "layers": ["spliced", "unspliced"]
}
```

说明：`ok=true` 只表示结构契约通过，不代表速度或谱系结论正确。

---

## 3. 确定性分层子集

- 分组列：`clusters`；每组最多 100 个细胞；种子 42
- 结果：770 个细胞（Delta 仅 70 个，全部保留）
- 只选细胞，不改任何表达值；`spliced/unspliced` 原样保留
- 元数据：`data/benchmarks/pancreas_subset.h5ad.subset.json`

---

## 4. 官方 scVelo 数值对照（P0-3）

参考实现：`benchmarks/compare_scvelo.py`，**直接调用 scVelo 0.3.4 API**，
不复用 CellFlow 适配器；参考与候选各自在独立子进程运行，避免共享 BLAS/numba 状态。

固定参数（运行前确定，未按结果调参）：

| 参数 | 值 |
|---|---|
| n_top_genes | 500 |
| n_neighbors | 15 |
| n_pcs | 20 |
| min_shared_counts | 10 |
| seed | 42 |
| use_umap | true |
| max_iter（dynamical） | 30 |
| 预处理 | filter_genes → normalize_per_cell(1e4, S/U) → log1p(X) → HVG(seurat) → PCA → neighbors → UMAP → moments |

结果：

| 模式 | 形状 | max abs diff | mean abs diff | Pearson | 容差 | 结论 |
|---|---|---:|---:|---:|---:|---|
| deterministic | 770 × 500 | **0.0** | 0.0 | 1.0 | 1e-6 | 通过 |
| stochastic | 770 × 500 | **0.0** | 0.0 | 1.0 | 1e-6 | 通过 |
| dynamical | 770 × 442 | **0.0** | 0.0 | ≈1.0 | 1e-5 | 通过（共同基因 442） |

说明：

- 三个模式在共同细胞/共同基因上完全一致，说明 CellFlow 的速度矩阵接入与同版本 scVelo 一致。
- dynamical 有 **58 个 HVG** 在 CellFlow 侧因“含非有限速度”被剔除；
  参考侧这些基因本身没有有效速度。对照只在共同 442 个基因上进行，报告中保留该差异。
- 这只证明**同一实现、同一参数**下的数值接入正确，不证明生物学方向正确，也不证明
  不同版本/真实数据上不会出现差异。

完整机器可读结果：`data/benchmarks/compare_scvelo_all/compare_scvelo.json`（gitignore）。

复跑命令：

```powershell
$env:PYTHONPATH = $PWD
.venv\Scripts\python.exe -m benchmarks.compare_scvelo `
  --subset data\benchmarks\pancreas_subset.h5ad `
  --output data\benchmarks\compare_scvelo_all `
  --modes deterministic,stochastic,dynamical
```

---

## 5. 尚未完成（P0-4 与后续）

- **CellRank 官方对照**：多次终末状态、transition matrix、macrostates、fate probabilities、
  吸收方程残差；固定终末 vs 自动终末要分开测试。
- 全细胞（3,696）而非 770 子集的对照；dynamical 的收敛/失败比例统计。
- veloVI 真实训练诊断、后验不确定性与多随机种子（P1）。
- 证据等级 L0–L4 与 A/B 报告升级（P1）。
- Windows 可靠性矩阵与外部试用清单（P2）。

---

## 6. 证据等级（当前）

- 数据完整性：**L1**（SHA-256、HDF5 可读、契约检查）
- scVelo 接入一致性：**L2**（独立参考实现对照，三个模式误差 0，固定参数）
- 生物学方向/谱系：**未达到 L3**（没有独立谱系追踪或实验验证）

本记录不允许被解读为“RNA velocity 已在真实数据上通过科学验收”。
