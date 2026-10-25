# CellFlow 个人学习指南

> 面向：只给自己学习使用的人。
> 目标不是“对外发布”，而是：**看懂真实数据上跑了什么、哪些结论可信、哪些只是模型推断**。
> 日期：2026-10-26

---

## 1. 先记住三句话

1. **运行成功 ≠ 科学正确**。任务 completed、图能打开，只说明工程链路可用（L0）。
2. **数值约束通过 ≠ 生物学正确**。矩阵有限、概率和为 1、残差小，只说明计算没坏（L1）。
3. **只有和官方实现同配置对照（L2）才谈得上“实现一致”**；生物学方向仍需要 L3/L4 证据。

CellFlow 的 A 报告 `4.12`、B 报告 `7.1` 会写出本次最高证据等级；不要跳过。

---

## 2. 推荐学习路线（由浅入深）

| 步骤 | 做什么 | 看什么 |
|---|---|---|
| 1 | 先跑内置模拟数据（QC/auto） | 报告 `0.1 先读这里`、`4.10 解释分层` |
| 2 | 跑胰腺子集（真实数据） | `docs/scientific_validation/phase2_p0.md` |
| 3 | 依次跑 scVelo deterministic / stochastic / dynamical | 报告 §4.6 指标、A 报告 §4.10 |
| 4 | 跑 CellRank（自动终末 / 固定终末） | 终末状态数量、是否单终末、§4.12 等级 |
| 5 | 跑 veloVI（短训练） | 明确标注“未收敛”，只学接口与不确定性 |
| 6 | 自己换数据 | 先走“表格预览确认”，确认方向/元数据列 |

真实数据复跑命令（本机已有 `data/benchmarks/` 缓存时）：

```powershell
$env:PYTHONPATH = $PWD
# 1) 构建 770 细胞分层子集（已有可跳过）
.venv\Scripts\python.exe -c "from benchmarks import subset; subset.build_subset('data/benchmarks/endocrinogenesis_day15.h5ad','data/benchmarks/pancreas_subset.h5ad',per_group=100,seed=42)"
# 2) 三种 scVelo 模式与官方实现对照
.venv\Scripts\python.exe -m benchmarks.compare_scvelo --subset data\benchmarks\pancreas_subset.h5ad --output data\benchmarks\compare_scvelo_all --modes deterministic,stochastic,dynamical
# 3) CellRank 自动/固定终末对照
.venv\Scripts\python.exe -m benchmarks.compare_cellrank --subset data\benchmarks\pancreas_subset.h5ad --output data\benchmarks\compare_cellrank
.venv\Scripts\python.exe -m benchmarks.compare_cellrank --subset data\benchmarks\pancreas_subset.h5ad --output data\benchmarks\compare_cellrank_fixed --terminal Beta,Alpha
# 4) 独立预处理对照
.venv\Scripts\python.exe -m benchmarks.compare_cellrank_independent --subset data\benchmarks\pancreas_subset.h5ad --candidate data\benchmarks\compare_cellrank --output data\benchmarks\compare_cellrank_independent
# 5) veloVI 训练诊断（短训练，不声称收敛）
.venv\Scripts\python.exe -m benchmarks.velovi_diagnostics --subset data\benchmarks\pancreas_subset.h5ad --output data\benchmarks\velovi_diag --seeds 42,123,2026 --max-epochs 2
```

---

## 3. 怎么读 A 报告

按这个顺序看：

1. `0. 一句话结论`：本次到底做了什么。
2. `0.1 先读这里（个人学习版）`：**实际执行 / 没有执行 / 最高证据等级 / 不要误读什么**。
3. `4.10 解释分层`：
   - **观察**：数据本身可见的（细胞数、群大小、表达）。
   - **派生统计**：从模型输出算出来的（速度长度中位数、命运概率）。
   - **计算推断**：依赖模型假设（方向、拟时序、命运）。
   - **假说**：需要实验验证的生物学解释。
4. `4.12 证据等级`：L0–L4，本次最高到哪一级。
5. `5. 参数与复现`：想重跑时看。

常见误读：

- 箭头方向 = 真实分化方向？**不是**；只是模型推断。
- 命运概率接近 1 = 高置信度？**不是**；可能只有一个终末状态。
- PAGA 连接 = 有向谱系？**不是**；只是群间连接强度。
- veloVI 跑满 epoch = 收敛？**不是**；看 `budget_exhausted` 与损失曲线。

---

## 4. 怎么读 B 报告

B 是技术记录，学习时重点看：

- 输入指纹、层/尺度声明、是否做了归一化/log/moments；
- 实际调用序列（calls.json）；
- 参数快照、随机种子；
- 失败阶段、错误、退出码；
- 证据等级与不可复现边界。

---

## 5. 真实与可信的边界（当前）

| 能力 | 证据 |
|---|---|
| 胰腺数据下载/校验 | SHA-256 + HDF5 可读 |
| scVelo 三模式 | 与官方实现同参数逐元素一致（误差 0） |
| CellRank 自动/固定终末 | 官方对照概率差 0、残差 ~1e-16；独立预处理同样成立 |
| veloVI | 2/20/60 epochs 均未出现平台，`budget_exhausted`；**未收敛、未证明种子稳健** |
| 生物学方向/谱系 | **没有**独立谱系追踪或实验验证 |

结论：可以拿来**学方法、学读图、学看不确定性**；不能把结果当成**已证实的生物学发现**。

---

## 6. 个人数据使用建议

1. 先备份原始数据；CellFlow 只读源文件，但会写 `results/`。
2. 表格导入先预览，确认细胞/基因方向与元数据列；缺失值会被拒绝。
3. 有 spliced/unspliced 才做 velocity；没有就只做 QC/分群/DPT/PAGA。
4. 单终末状态时，不要解读命运区分。
5. 换参数后重新看 `4.12`，不要把上一次的 L2 套到新数据上。

---

## 7. 想进一步学习时

- 官方 scVelo 文档：投影、dynamical 前置条件、velocity graph 的含义。
- CellRank 文档：kernel、GPCCA、终末状态、吸收概率。
- 重点理解“模型假设”而不是只记参数名。
- 若要做真实课题：需要独立时间点/谱系追踪/扰动实验，单靠 RNA velocity 不足以证明方向。
