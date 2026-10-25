# CellFlow Phase II 科学验证报告

日期：2026-10-26（Asia/Shanghai）
基线：PR #1 `4fcb61f`；工作分支 `phase2/scientific-validation`（未合并）
版本：v0.4.0 候选（**未发布**）

> 用途说明：本项目当前只用于个人学习，不对外提供；外部试用清单仅作为边界记录。
> 本报告只陈述本轮实际执行过的验证。所有未完成项在 §7/§8 明确标为未验证，
> 不能因为 CI 或单元测试通过而视为科学验收完成。

---

## 1. 范围与结论摘要

本阶段目标：真实数据上的 RNA velocity / 命运推断基准、veloVI 训练诊断、
证据等级、导入语义、Windows 可靠性；不扩充新模型、不做大重构。

**结论：**

- scVelo 三种模式在固定参数下与官方实现逐元素一致（L2）。
- CellRank 自动终末与固定终末均与官方实现一致（L2，受限：共享预处理文件）。
- 真实数据契约、下载完整性、导入确认、证据等级、磁盘门禁已落地。
- veloVI 只完成接口/后验/短训练诊断；**未收敛、未证明种子稳健**（unvalidated）。
- Windows 可靠性只覆盖可自动化场景；干净安装/强制关窗/DPI/真实磁盘写满未验证。
- **不允许把当前状态称为“已完成全部科学验收”**；外部试用仅限受控条件（§9）。

---

## 2. 基准数据

| 项 | 值 |
|---|---|
| 数据 | scVelo 官方胰腺内分泌发生 E15.5 |
| 来源 | `theislab/scvelo_notebooks` |
| 大小 / SHA-256 | 52,477,898 B / `9e3e459e…fbe7c6` |
| 形状 | 3,696 × 27,998 |
| 层 | spliced / unspliced（float32、非负、无 NaN/Inf） |
| 下载 | `.part` 续传、3 次有界重试、大小+哈希校验、HDF5 可读后原子发布 |
| 本地缓存 | `data/benchmarks/`（gitignore，未进 GitHub） |

数据契约检查：0 issues / 0 warnings。子集：按 `clusters` 分层、每组 ≤100、seed 42 → 770 细胞。

---

## 3. scVelo 数值对照（L2）

参考实现直接调用 scVelo 0.3.4；参考与候选在独立子进程运行。固定参数：
500 HVG、15 邻居、20 PC、min_shared_counts=10、seed 42、dynamical max_iter=30。

| 模式 | 共同形状 | max abs diff | mean abs diff | Pearson | 容差 | 结果 |
|---|---|---:|---:|---:|---:|---|
| deterministic | 770×442 | 0.0 | 0.0 | 1.0 | 1e-6 | 通过 |
| stochastic | 770×442 | 0.0 | 0.0 | 1.0 | 1e-6 | 通过 |
| dynamical | 770×442 | 0.0 | 0.0 | ≈1.0 | 1e-5 | 通过 |

说明：dynamical 有 58 个 HVG 在 CellFlow 侧因全非有限/全零速度被剔除；
参考侧这些基因也无有效速度。对照只在共同 442 基因上成立，差异已记录。

---

## 4. CellRank 对照（L2，受限）

### 4.1 自动终末

在 CellFlow 保存的同一份 `analysis.h5ad` 上运行官方 CellRank
（PseudotimeKernel → GPCCA → predict_terminal_states → direct fate probabilities）：

- 终末类别：Ductal_1 / Beta / Delta / Ductal_2 / Alpha（双方一致）
- 命运概率形状：770 × 5
- 最大绝对概率差：**0.0**
- 吸收方程残差：**3.33e-16**

**独立预处理对照**：参考进程从原始子集重新执行 normalize_total/log1p/HVG/PCA/
neighbors/DPT，不再共享 `analysis.h5ad`；自动终末仍为 5 类一致、概率差 0、
残差 3.33e-16。共享中间文件的限制已解除。

### 4.2 固定终末

CellFlow 新增 `cellrank_terminal` 配置；固定 `Beta,Alpha` 后：

- 双方终末类别：Beta / Alpha
- 最大绝对概率差：**0.0**
- 吸收方程残差：**4.44e-16**

### 4.3 边界

- 独立实现使用相同步骤/参数，不是不同软件版本或不同参数的泛化测试。
- 终末类别来自 GPCCA 的细胞图识别；5 类不等于 5 条已验证谱系。
- 没有真实谱系追踪或独立实验参照，因此仍为 L2，不是 L3。

---

## 5. veloVI（未验证收敛）

`benchmarks/velovi_diagnostics.py`，隔离环境 scvi-tools 1.2.0 / torch 2.5.1+cpu，
`max_epochs=2`，种子 42/123/2026：

| 种子 | epochs | elbo_train_final | 后验 std | std 中位 | 跨种子中位余弦 |
|---:|---:|---:|---|---:|---|
| 42 | 2 | 263,638.03 | 770×500 有限非零 | 0.372 | vs 123: 0.318 |
| 123 | 2 | 289,422.91 | 770×500 有限非零 | 0.376 | vs 2026: 0.328 |
| 2026 | 2 | 252,353.19 | 770×500 有限非零 | 0.379 | vs 42: 0.322 |

结论：

- 输入协议（Ms/Mu、normalized、is_count_data=False）已按 1.2.0 对齐，后验文件不是伪造零不确定性。
- 2 epochs 只验证接口；**没有 validation ELBO、early stopping、收敛判据**。
- 跨种子中位余弦约 0.32，说明短训练下方向/幅度不稳定；不能宣称种子稳健。
- 追加 20 / 60 epochs（seed 42）：train 274,809 → 153,519 → 56,458，
  validation 383,171 → 223,909 → 90,590；60 epochs 最后 5 轮仍在下降，
  `budget_exhausted=true`，**没有出现平台**，因此仍不升级证据等级。
- veloVI 当前证据等级：接口/数值约束 **L1**；收敛/稳健性 **未验证**。

---

## 6. 证据等级与报告

新增 `cellflow/evidence.py`：

| 等级 | 运行时自动？ |
|---|---|
| L0 工程输出 | 是（completed） |
| L1 数值验证 | 是（quality.ok） |
| L2 参考实现对照 | **否**，必须显式 `reference_comparison` 记录 |
| L3 生物学证据 | 否，永不自动 |
| L4 独立实验 | 否，永不自动 |

每次任务写入 `result.json` 与 `provenance.json`；A 报告 4.12、B 报告 7.1 显示
本次最高等级与未达到原因。测试：`tests/test_evidence_levels.py`。

---

## 7. 导入语义（P1-7）

- 新增 `table-preview` / `table-import`：上传 CSV/TSV 先只读预览，显示
  方向推断置信度、元数据候选、缺失与表头重复计数。
- 用户必须确认方向与元数据列后才会转换导入；缺失/非数值表达直接拒绝。
- 转换结果写入新的 `.h5ad` 并登记，源文件不改；文件与选项共同决定目标哈希。
- UI：数据页上传表格时弹出确认对话框（方向、元数据列、缺失统计）。
- 测试：`tests/test_table_import.py`（4 项）覆盖方向、元数据、缺失拒绝、过期预览。

---

## 8. Windows 可靠性与产物

自动化通过：连续任务、损坏输入、终态唯一、取消进程树、并发上限、恢复/心跳、
准备失败、内存预检、磁盘 <1 GB 门禁、Qt API 启动/停止/端口释放。

未验证：干净 Windows 机器安装、普通用户权限全流程、强制杀进程后的孤儿清理、
真实磁盘写满、真实 OOM、多屏 125/150/200% DPI、卸载/数据保留。

详见 `phase2_windows.md`。

---

## 9. G1–G5 验收对照

| 目标 | 状态 | 依据 |
|---|---|---|
| **G1 数据安全** | 部分通过 | 下载哈希/契约、缺失拒绝、预览确认、源文件不改；未做真实用户误操作矩阵 |
| **G2 数值正确性** | 部分通过 | scVelo 三模式误差 0；CellRank 自动/固定终末误差 0、残差 ~1e-16；veloVI 未收敛 |
| **G3 科学边界** | 通过（限本轮） | L0–L4 证据等级、单终末告警、未执行步骤不宣称、未验证能力标 experimental |
| **G4 工程可靠性** | 部分通过 | 自动化场景 + 强制杀启动器/重启用例通过；真实 OOM/磁盘写满/DPI 未验证 |
| **G5 外部可复现** | 部分通过 | 文档、命令、哈希、CI；干净机器/外部人员实测未完成 |

---

## 10. 外部试用准入清单（受控）

允许：

- 在本机、由开发者陪同的**受控试用**；
- 使用真实胰腺数据复跑 scVelo/CellRank 对照（L2）；
- 使用自己的数据做 L0/L1 诊断，并明确标注“未做外部验证”。

禁止/必须标注：

- 不得把 veloVI 结果当作收敛或稳健结论；
- 不得把 CellRank 命运概率当作已验证谱系；
- 不得把单终末状态或接近 1 的概率解释为准确率；
- 不得声称“已通过全部科学验收”；
- 不得在未完成干净安装/DPI 验证前承诺普通用户可独立安装。

外部试用前必须完成：

1. 固定终末与自动终末的**独立预处理**对照（当前共享 analysis.h5ad）。
2. veloVI 至少一次足够 epoch 的收敛诊断与 validation ELBO。
3. 干净 Windows 标准用户安装/卸载/再次启动。
4. 真实磁盘写满/强制杀进程的残留检查。
5. 多屏 DPI 矩阵。
6. A/B 报告与证据等级的最终人工审阅。

---

## 11. 产物索引

- 数据/脚本：`benchmarks/fetch.py`、`contracts.py`、`subset.py`、`compare_scvelo.py`、
  `compare_cellrank.py`、`velovi_diagnostics.py`、`windows_reliability.py`
- 测试：`tests/test_benchmarks_contracts.py`、`test_evidence_levels.py`、
  `test_table_import.py`、`test_disk_guard.py`、`test_registry_health.py`
- 记录：`docs/scientific_validation/phase2_p0.md`、`phase2_cellrank_velovi.md`、
  `phase2_evidence.md`、`phase2_windows.md`
- 机器可读证据：`data/benchmarks/*.json`（gitignore）
- 分支：`phase2/scientific-validation`；基线 PR #1 `4fcb61f`

复跑（摘要）：

```powershell
$env:PYTHONPATH = $PWD
.venv\Scripts\python.exe -m unittest discover -s tests          # 317 tests
.venv\Scripts\python.exe -m benchmarks.compare_scvelo --subset data\benchmarks\pancreas_subset.h5ad --output data\benchmarks\compare_scvelo_all --modes deterministic,stochastic,dynamical
.venv\Scripts\python.exe -m benchmarks.compare_cellrank --subset data\benchmarks\pancreas_subset.h5ad --output data\benchmarks\compare_cellrank
.venv\Scripts\python.exe -m benchmarks.compare_cellrank --subset data\benchmarks\pancreas_subset.h5ad --output data\benchmarks\compare_cellrank_fixed --terminal Beta,Alpha
.venv\Scripts\python.exe -m benchmarks.velovi_diagnostics --subset data\benchmarks\pancreas_subset.h5ad --output data\benchmarks\velovi_diag --seeds 42,123,2026 --max-epochs 2
.venv\Scripts\python.exe -m benchmarks.windows_reliability
```

---

## 12. 最终声明

> CellFlow 在 Phase II 已完成真实数据上的 scVelo 与 CellRank **参考实现数值对照**（L2），
> 完成了数据完整性、导入确认、证据等级与部分 Windows 可靠性；
> **尚未完成 veloVI 收敛验证、独立预处理对照、干净机器安装与多屏 DPI**。
> 因此当前状态是“可受控试用 + 可审计的 L0/L1/L2 证据”，不是“完整科学验收通过”。
