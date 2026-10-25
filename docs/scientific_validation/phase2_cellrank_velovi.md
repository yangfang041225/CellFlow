# Phase II · P0-4 CellRank 与 P1 veloVI 诊断记录

日期：2026-10-26
基线：PR #1 `4fcb61f`；分支 `phase2/scientific-validation`
状态：**P0-4 完成（自动终末）；P1 仅完成短训练诊断与多种子，不声称收敛**

---

## 1. CellRank 官方对照（P0-4）

方法：先在 CellFlow 中运行 `cellrank` 方法，保存其预处理后的 `analysis.h5ad`
（含 `dpt_pseudotime`、`clusters`）。参考进程**直接**在同一个 `analysis.h5ad` 上运行官方
CellRank：`PseudotimeKernel` → `GPCCA` → `predict_terminal_states` →
`compute_fate_probabilities(solver='direct')`。这样比较的是“同一输入状态下的 CellRank 计算”，
避免预处理差异掩盖问题。

固定参数：

- 子集：770 细胞（见 `phase2_p0.md`）
- `cellrank_n_states=5`
- `group_key=clusters`
- DPT 起点：子集内第一个 Ductal 细胞（探索性先验，不是谱系追踪）

结果：

| 项 | 结果 |
|---|---|
| 终末类别（候选） | Ductal_1, Beta, Delta, Ductal_2, Alpha |
| 终末类别（官方参考） | 完全相同 |
| 类别排除 | 无 |
| 命运概率形状 | 770 × 5 |
| 最大绝对概率差 | **0.0** |
| 吸收方程残差 | **3.33e-16** |
| 多终末 | 是（5 类） |

机器可读结果：`data/benchmarks/compare_cellrank/compare_cellrank.json`（gitignore）。

### 边界（必须保留）

- 只验证了**自动识别终末状态**；项目书要求的“固定终末状态 vs 自动终末状态分别验证”
  尚未完成——CellFlow 当前没有固定终末状态配置入口。
- 参考与候选共享同一份预处理 `analysis.h5ad`，因此这次对照**只证明 CellRank
  计算与吸收求解一致**，不证明预处理/DPT 本身独立正确。
- 5 个终末类别来自 GPCCA 在细胞图上的识别，**不是**生物学谱系验证。
- 单终末状态退化情形由既有测试覆盖，但本数据集未触发。

复跑：

```powershell
.venv\Scripts\python.exe -m benchmarks.compare_cellrank `
  --subset data\benchmarks\pancreas_subset.h5ad `
  --output data\benchmarks\compare_cellrank
```

---

## 2. veloVI 短训练与多种子（P1 部分）

脚本：`benchmarks/velovi_diagnostics.py`。每个种子在 `.venv-velovi`（scvi-tools 1.2.0 /
torch 2.5.1+cpu）中独立子进程运行，`max_epochs=2`，其余参数固定。

| 种子 | epochs | elbo_train_final | 后验不确定性文件 | std 形状 | std 均值 | std 中位 | std 最大 | 有限 |
|---:|---:|---:|---|---:|---:|---:|---:|---|
| 42 | 2 | 263,638.03 | 有 | 770×500 | 0.779 | 0.372 | 9.689 | 是 |
| 123 | 2 | 289,422.91 | 有 | 770×500 | 0.784 | 0.376 | 9.464 | 是 |
| 2026 | 2 | 252,353.19 | 有 | 770×500 | 0.784 | 0.379 | 9.670 | 是 |

跨种子速度方向一致性（基因维余弦，500 个共同基因）：

| 种子对 | 中位余弦 | 余弦 > 0 比例 |
|---|---:|---:|
| 42 vs 123 | 0.318 | 99.2% |
| 42 vs 2026 | 0.322 | 98.8% |
| 123 vs 2026 | 0.328 | 99.0% |

解释：

- 2 个 epoch 只用于**接口/诊断回归**，不能称为训练收敛；ELBO 数值之间不可直接比较。
- 后验标准差文件存在且有限，形状与速度矩阵一致；这验证了“不是伪造的零不确定性”。
- 但 **中位余弦仅约 0.32**：短训练下不同种子的速度方向/幅度差异明显。
  因此当前不能宣称 veloVI 结果在种子间稳定，也不能把单次结果提升到 L2/L3 证据等级。
- 未完成：validation ELBO、early stopping、收敛判据、更多 epoch 的资源评估、
  真实数据收敛性。项目书要求的部分保持 **unvalidated**。

复跑：

```powershell
.venv\Scripts\python.exe -m benchmarks.velovi_diagnostics `
  --subset data\benchmarks\pancreas_subset.h5ad `
  --output data\benchmarks\velovi_diag `
  --seeds 42,123,2026 --max-epochs 2
```

---

## 3. 证据等级与结论

| 能力 | 当前等级 | 理由 |
|---|---|---|
| 数据下载/完整性/契约 | L1 | SHA-256、HDF5 可读、契约检查 |
| scVelo 三模式接入 | **L2** | 独立参考实现、同参数、误差 0 |
| CellRank 自动终末/命运概率 | **L2（受限）** | 共享 analysis.h5ad；固定终末未测 |
| veloVI 接口/后验维度 | L1 | 短训练、3 种子、非零有限不确定性 |
| veloVI 收敛/稳健性 | **未验证** | 2 epochs、种子中位余弦 0.32 |
| 生物学方向/谱系 | **未达到 L3** | 无独立谱系追踪或实验验证 |

**不能据此宣称 CellFlow 已通过真实 RNA velocity/CellRank 科学验收。**
下一步：固定终末状态对照、更多 epoch 的 veloVI 收敛诊断、A/B 报告证据等级、
导入语义确认、Windows 可靠性矩阵。
