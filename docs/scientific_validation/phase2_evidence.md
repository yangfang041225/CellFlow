# Phase II · 证据等级与报告语义（P1-4 / P1-5 / P1-6 部分）

日期：2026-10-26
分支：`phase2/scientific-validation`

---

## 1. 证据等级 L0–L4

新增 `cellflow/evidence.py`：

| 等级 | 含义 | 运行时是否自动 |
|---|---|---|
| L0 工程输出 | 任务完成、结果目录/报告可生成 | 是（仅当任务 completed） |
| L1 数值验证 | 结构/数值检查全部通过 | 是（quality.ok 且存在检查） |
| L2 参考实现对照 | 与官方实现同配置比较 | **否**；必须由 benchmark 显式写入 `provenance.reference_comparison` |
| L3 生物学证据支持 | 已知方向/实验观察/独立参照 | **否**，永远不由程序自动判定 |
| L4 独立实验验证 | 谱系追踪/扰动等 | **否** |

每次任务：

- `result.json` 增加 `evidence_levels`
- `provenance.json` 增加 `evidence_levels`
- A 报告增加 `### 4.12 证据等级` 表
- B 技术报告增加 `## 7.1 证据等级（技术记录）`

程序明确写出“运行成功不会自动升级到 L2”，并列出 L2/L3/L4 的未达到原因。

测试（`tests/test_evidence_levels.py`，3 项）：

- 成功任务最高只到 **L1**
- 显式 `reference_comparison` 才允许 L2
- QC 运行的结果、provenance、A/B 报告都包含等级记录

---

## 2. 当前科学证据状态

| 能力 | 等级 | 来源 |
|---|---|---|
| 数据下载/哈希/契约 | L1 | `phase2_p0.md` |
| scVelo deterministic/stochastic/dynamical | **L2** | 独立参考实现、同参数、误差 0 |
| CellRank 自动终末/命运概率 | **L2（受限）** | 共享 analysis.h5ad、5 类终末、残差 3.3e-16 |
| veloVI 接口/后验维度 | L1 | 3 种子、2 epochs、非零有限 std |
| veloVI 收敛/种子稳健性 | **未验证** | 中位跨种子余弦约 0.32 |
| 生物学方向/谱系 | **未达到 L3** | 无独立谱系追踪 |

注意：L2 的 benchmark 结果目前**不会自动写回普通任务**；这是有意的隔离，
避免一次基准测试把历史任务全部“升级”。

---

## 3. 尚未完成（P1-7 与后续）

- 导入语义确认（显式方向/元数据列/预览后再导入；拒绝静默改值）
- A 报告逐条“观察/推断”证据字段的进一步细化
- B 报告训练诊断与回退原因的完整记录（veloVI 已有 protocol/training_diagnostics，
  但尚未统一为通用字段）
- Windows 可靠性矩阵、外部试用清单、正式《科学验证报告》

本文件不构成“已通过科学验收”的声明。
