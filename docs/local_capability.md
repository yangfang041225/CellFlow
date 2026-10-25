# 本机可用能力清单（Windows / CPU-only）

审计日期：**2026-10-25（Asia/Shanghai）**
审计对象：`CellFlow`（单细胞分析应用，项目根为本文件上级目录）
机器：Windows 11 Home China 10.0.26200；**Intel Core i5-1240P（12 核 / 16 线程）**；**16 GB 内存（实测 15.7 GiB）**；
**Intel Iris Xe 核显，无 NVIDIA GPU**（`nvidia-smi` 不存在；`Win32_VideoController` 只有 Iris Xe）。

判据来源：

- `cellflow/registry.py`：`METHODS` 的 key / name / status(ready·experimental·planned) / needs / packages / gpu_note / runtime_env / needs_resource（`CHECKED = '2026-10-24'`）；
- `cellflow/method_content.py`：每个方法的中文说明与 `tech.status_note`；
- `cellflow/device.py`：设备选择逻辑；
- `requirements.txt`、`environment-venv.lock.txt`、`environment-venv-velovi.lock.txt`；
- 实测：用 `.venv\Scripts\python.exe` 与 `.venv-velovi\Scripts\python.exe` 打印 registry 状态、`importlib.util.find_spec`、`importlib.metadata.version`、`cellflow.device.resolve()`、`cellflow.memory.available_bytes()`。

边界：本文只回答“**这台机器能不能跑**”，不评价方法科学优劣；文中“跑通”仅指软件流程跑通，**不是生物学结论**。所有结论来自代码与只读检查，未安装、未卸载、未下载任何东西。

---

## 1. 一句话结论

本机（CPU-only）当前通过准入、可直接运行的方法是 **11 个**：一键分析、数据质控/PCA、模拟示例、Leiden 分群、DPT、PAGA、scVelo 的 deterministic / stochastic / dynamical、CellRank 命运概率、veloVI（独立环境 `.venv-velovi`）；但其中 **scVelo deterministic 只有代码和依赖、没有找到本机端到端运行记录**，真正有本机运行证据的是 **10 个**。RegVelo 不可运行（独立环境未装，且官方部分函数写死 CUDA），DeepVelo / UniTVelo / VeloAE / dynamo / MultiVelo **尚未接入（无适配器代码）**。

---

## 2. 能力总表

状态说明：`registry.status_of()` 是**实时**算的——`implemented=False` 一律 `planned`；声明的包在当前解释器缺失时，若 `runtime_env` 解释器存在且对应锁文件存在则 `ready`，否则 `experimental`。

| 方法 key | 中文名 | 状态 | 是否需要 GPU/CUDA | 依赖是否已装（主环境 / 隔离环境） | 本机能否运行 | 备注 |
|---|---|---|---|---|---|---|
| `auto` | 一键分析（推荐流程） | 可运行 (ready) | 不需要（纯 CPU） | 主环境：scanpy 1.11.5、anndata 0.12.19 | 是（已实测） | 240 细胞模拟约 24–31 s；无剪接层不加速度；**不会**把 veloVI 塞进一键流程（`adapters.py:447-449` 直接拒绝） |
| `qc` | 数据质控与 PCA | 可运行 (ready) | 不需要（纯 CPU） | 主环境：scanpy 1.11.5、anndata 0.12.19 | 是（已实测） | 秒级；只做描述性质控，不自动按阈值删细胞 |
| `demo` | 模拟教学示例 | 可运行 (ready) | 不需要 | registry 未声明包；用主环境即可 | 是（已实测） | 内置 240 细胞 × 120 虚构基因；结果永远标记为模拟 |
| `leiden` | 自动分群（Leiden + UMAP） | 可运行 (ready) | 不需要（纯 CPU） | 主环境：scanpy 1.11.5、anndata 0.12.19、igraph 0.11.9 | 是（已实测） | 无独立任务目录；`auto` 流程内真实调用，另有端到端测试 `test_leiden_end_to_end` |
| `dpt` | 扩散拟时序 DPT | 可运行 (ready) | 不需要（纯 CPU） | 主环境：scanpy 1.11.5、anndata 0.12.19 | 是（已实测） | **必须**给起点细胞 + 生物学依据；`verified-dpt-1792849669` completed 约 25 s |
| `paga` | PAGA 群间连接 | 可运行 (ready) | 不需要（纯 CPU） | 主环境：scanpy 1.11.5、anndata 0.12.19、igraph 0.11.9 | 是（已实测） | 需要分组列或先自动分群；`verified-paga-1792849696` completed 约 17 s；输出是**无向**连通 |
| `scvelo_deterministic` | scVelo · deterministic（稳态） | 可运行 (ready) | 不需要（官方实现为 CPU） | 主环境：scvelo 0.3.4、scanpy 1.11.5、anndata 0.12.19 | 是（**已实测**，2026-10-25 补跑） | registry 标 ready、适配器存在；但 `results/` 下没有该模式的任务，测试也未覆盖 → **不能声称本机跑通过** |
| `scvelo_stochastic` | scVelo · stochastic | 可运行 (ready) | 不需要，无需 CUDA | 主环境：scvelo 0.3.4、scanpy 1.11.5 | 是（已实测） | 需 spliced/unspliced 原始计数；`verified-*` 任务 15–18 s；`auto` 默认速度模型就是它 |
| `scvelo_dynamical` | scVelo · dynamical | 可运行 (ready) | 不需要，比 stochastic 慢 | 主环境：scvelo 0.3.4、scanpy 1.11.5 | 是（已实测，**仅接口级**） | 需 spliced/unspliced；`verified-*` 任务 `max_iter=2` 完成约 19–21 s，**未声称收敛** |
| `cellrank` | CellRank 2 · 命运概率 | 可运行 (ready) | 不需要（纯 CPU） | 主环境：cellrank 2.0.7、scanpy 1.11.5、scvelo 0.3.4 | 是（已实测） | 需要起点；`auto` 内真实调用（result 的 fate 段 + evidence），无独立任务目录；> 6000 细胞直接拒绝；未装 `petsc4py` → 需稠密化 |
| `regvelo` | RegVelo · 带先验的调控动力学 | 实验接入 (experimental) | **部分需要**：官方推断函数写死 CUDA；CPU 训练本机未实测，且**不输出调控边** | 主环境：regvelo / torch / scvi-tools **全缺**；隔离环境 `.venv-regvelo` **不存在** | **否** | 未安装、未训练；`验证记录.md` 实测为 failed（按设计，准入拦截，1.5 s） |
| `velovi` | veloVI · 变分速度与不确定性 | 可运行 (ready) | 不强制（GPU 可选，CPU 可训练） | 主环境缺 scvi-tools；隔离环境 `.venv-velovi` 已装 **scvi-tools 1.2.0 + torch 2.5.1+cpu** | 是（已实测，隔离环境） | `results/4c967bc71f4d4ccd` completed 27.3 s，`max_epochs=2`、240×120 → **只算接口验证，未收敛** |
| `deepvelo` | DeepVelo · 细胞相关动力学 | 尚未接入 (planned) | 官方默认 GPU；本机无 CUDA | 未装（deepvelo / dgl 均缺失）；`.venv-deepvelo` 不存在 | 否 | `implemented=False`，无可调用代码；官方依赖旧 Python + DGL |
| `unitvelo` | UniTVelo · 共同时间动力学 | 尚未接入 (planned) | 不需要（但 TensorFlow 栈较大） | 未装（unitvelo / tensorflow 均缺失）；`.venv-unitvelo` 不存在 | 否 | 只登记未接入；TF 栈需独立环境 |
| `veloae` | VeloAE · 低维速度 | 尚未接入 (planned) | 不需要（需 PyTorch Geometric） | 未装（veloproj / torch-geometric 均缺失）；`.venv-veloae` 不存在 | 否 | 只登记未接入；PyG 安装较重 |
| `dynamo` | dynamo · 动力学与向量场 | 尚未接入 (planned) | 不需要（完整安装体积较大） | 未装（dynamo-release 缺失）；`.venv-dynamo` 不存在 | 否 | 只登记未接入；代谢标记分支还需实验时间 |
| `multivelo` | MultiVelo · 多组学动力学 | 尚未接入 (planned) | 不需要（数据准备复杂） | 未装（multivelo 缺失）；`.venv-multivelo` 不存在 | 否 | 只登记未接入；必须同时有匹配的 ATAC 数据 |

**计数**：17 个条目 = 可运行 **11** + 实验接入 **1** + 尚未接入 **5**。表中“本机能否运行=是”共 **11** 个，其中带“已实测”的 **11** 个（`scvelo_deterministic` 已于 2026-10-25 补跑）。

---

## 3. 应搁置的模型与理由

### 3.1 与 CUDA/GPU 直接相关（先搁置）

| 方法 | registry `gpu_note` 原文 | 实测依赖 | 搁置理由 |
|---|---|---|---|
| **RegVelo 0.4.2** | “官方实现部分函数写死 CUDA；本机 CPU 不调用该函数，因此**不输出调控边**” | 主环境 regvelo / torch / scvi-tools = `None`；`.venv-regvelo` 不存在 → `env_python()` 返回 `None` | 双重不可用：① 独立环境未装、从未训练；② 即便装上，CPU 路径也不产出调控边，做不了它最核心的“调控”结论。装环境属于“下载数 GB + 需人工核查 GRN”，当前应搁置。 |
| **DeepVelo** | “官方默认 GPU；CPU 训练慢且版本受限” | deepvelo / dgl = `None`；`.venv-deepvelo` 不存在；`implemented=False` | 没有适配器代码（注册表红线：`implemented=False` 永不可运行），且官方实现要旧 Python + DGL，本机无 NVIDIA。搁置。 |

### 3.2 未实现（只登记了方法，装了包也跑不了）

`UniTVelo`、`VeloAE`、`dynamo`、`MultiVelo`（以及上面的 DeepVelo）的 `implemented=False`。
它们各自声明的 `gpu_note` 其实都是“CPU 可运行”（UniTVelo：TensorFlow 栈较大；VeloAE：需要 PyTorch Geometric；dynamo：完整安装体积较大；MultiVelo：数据准备复杂），
**所以搁置的首要原因不是“本机算力不够”，而是“还没有可调用代码 + 独立环境未安装”**。`registry.py` 明确：`implemented=False` 的条目永远不可运行，即使误装了同名包。

实测：这五个方法的 packages 在主环境全部为 `None`，规划的 `.venv-deepvelo` / `.venv-unitvelo` / `.venv-veloae` / `.venv-dynamo` / `.venv-multivelo` 目录**均不存在**。

### 3.3 能跑但本机未验证，不要写成“已验证”

- `scvelo_deterministic`：已补跑（`results/verified-scvelo-det-1792912892`）：completed、15.6 s、velocity + 30 个候选基因、quality ok。
- `scvelo_dynamical`：本机跑的是 `max_iter=2` 的接口级任务，不能声称收敛。
- `veloVI`：本机跑的是 `max_epochs=2` 的接口级任务，不能声称收敛或可用于真实数据。

### 3.4 不应搁置（CPU 已能跑）

`auto`、`qc`、`demo`、`leiden`、`dpt`、`paga`、`scvelo_stochastic`、`scvelo_dynamical`、`cellrank`、`velovi`。
它们不需要 CUDA；`device.py` 也写明 scVelo / scanpy / CellRank 这部分是上游 CPU 实现，选 GPU 不会更快。

---

## 4. 内存注意（16 GB 机器）

### 4.1 预检规则

`docs/data_pipeline.md` §3 的规则：读取前先估算峰值内存（h5ad/h5/loom 读 h5py 头部的 shape 与稀疏 nnz；文本 ×4；压缩包 ×5），
**超过当前可用物理内存的 90% 时** API 返回 413、任务失败并给出“预计/可用”数字，避免 16 GB 机器被拖死；估算不是精确值。

代码对应：`cellflow/memory.py` 的 `precheck(path, limit=0.9)`；`cellflow/runner.py` 在 `load()` 前调用 `precheck`，读取后 `require(0.5)`，适配器计算前 `require(1.0)`（`runner.py:281-286, 310`）。`require()` 低于阈值时抛 `MemoryError`，不会让系统 OOM。

### 4.2 本机现场读数（2026-10-25 07:17–07:19）

用 `cellflow.memory.available_bytes()`（Windows `GlobalMemoryStatusEx`）实测：

| 项 | 实测值 |
|---|---|
| 物理内存总量 | **15.7 GiB**（16 GB 标称） |
| 当前可用（第一次采样） | **6,650,781,696 B ≈ 6.19 GiB**（系统内存占用 60%） |
| 当前可用（约 2 分钟后第二次采样） | **≈ 6.59 GiB** |
| `memory.require(1.0)` | 通过，返回 6.19 GiB |
| 90% 预检上限（按上面可用值折算） | 约 **5.6–5.9 GiB** 的“估算峰值” |

真实文件示例：`data/public/geo_GSE84133.h5ad`（125.6 MB，sparse 8569×20125）→ `memory.estimate()` 估算峰值 **0.24 GB**、比例 0.037（可用 6.62 GB）→ `precheck` 返回 `None`，该文件因此通过预检；对应的 8569 细胞 GEO `auto` 任务也确实跑完了（57.7 s，仅 QC/分群/PAGA，无剪接层）。

### 4.3 额外上限

- **CellRank**：没有 `petsc4py` 时 GPCCA 的 Schur 分解要把细胞×细胞矩阵稠密化，程序在 **> 6000 细胞**时直接拒绝（`adapters.py:501-502, 782`；`docs/本机硬件与运行策略.md`）。
- `docs/本机硬件与运行策略.md` 的规模建议：< 2 千细胞所有已接入方法都适合；2 千–2 万 velocity 先看剪接覆盖率、dynamical 可能较慢；2 万–5 万建议先随机子集调参；> 5 万本原型不适合全量（backed/Zarr 未实现）。

---

## 5. 限制：哪些“没跑过”，不要写成“跑通过”

### 5.1 本机实际运行过的范围

- 数据：内置模拟数据 **240 细胞 × 120 基因**；一份 **413 细胞 × 505 基因**的真实 velocity 子集（`auto`，24.7 s，stochastic 速度）；一份 **8569 × 20125** GEO 真实数据（`auto`，只跑到 QC/分群/PAGA，无剪接层）。
- 方法：`auto`（多任务）、`qc`、`demo`、`leiden`（auto 内 + 测试）、`dpt`（verified 任务）、`paga`（verified 任务）、`scvelo_stochastic`（verified 任务）、`scvelo_dynamical`（verified 任务，`max_iter=2`）、`cellrank`（auto 内）、`velovi`（隔离环境任务 `4c967bc71f4d4ccd`，`max_epochs=2`）。
- 结论边界：这些只证明**软件流程与上游库调用跑通**，不是方法有效性或生物学发现；小数据上的耗时含解释器启动，**不是性能基准**，也不能用来给方法排名。

### 5.2 明确“未运行”的清单

| 未运行项 | 原因 |
|---|---|
| **RegVelo 训练** | `.venv-regvelo` 未安装；主环境无 regvelo/torch/scvi-tools；实测准入就拦截（failed by design） |
| **DeepVelo / UniTVelo / VeloAE / dynamo / MultiVelo** | 没有适配器代码（`implemented=False`），对应环境目录不存在 |
| **scVelo deterministic 端到端** | 依赖齐全，但未找到任务记录与端到端测试；本次只读审计没有复跑 |
| **GPU / CUDA 路径** | 本机无 NVIDIA GPU；`torch` 只在 `.venv-velovi` 里且是 `2.5.1+cpu`（`cuda=None`，`is_available=False`） |
| **petsc4py 稀疏 Schur 路径** | 未安装；实际走 `brandts` 稠密化（run.log 有 WARNING） |
| **> 1 GB 真实数据、CELLxGENE 大文件下载** | 本次审计未下载、未运行（`docs/test_report.md` §5 也列为未验证） |
| **Qt 窗口内点击运行、GUI** | 本次审计不启动 GUI |
| **测试套件复跑** | 本次审计为只读，不跑测试（避免写缓存/临时结果）；`docs/test_report.md` 的“110 tests OK”为文档记载，未由本次独立复核 |

### 5.3 口径提醒

- `veloVI` 的 `max_epochs=2`、`scVelo dynamical` 的 `max_iter=2` 都是**接口验证**，不代表收敛，也不代表真实数据可用。
- `cellrank` 在模拟数据上只识别到 1 个终末状态时，所有细胞概率都是 1，驱动基因那一步失败；报告会写明，不能当作“命运分析成功”。
- `paga` 的输出是无向连接，不能读成“A 分化成 B”；`dpt` 是相对顺序，不是真实时间。
- `auto` 不做细胞类型注释、双细胞/环境 RNA 校正、有向谱系验证。

---

## 6. 核查方法（本次实际执行的只读操作）

1. 用 `.venv\Scripts\python.exe` 以 stdin 方式运行脚本，导入 `cellflow.registry`，对 17 个方法打印：`key / name / implemented / status_of() / installed(packages) / runtime_env / env_python() / isolated_ready() / gpu_note / needs_resource`；
2. 在 `.venv` 与 `.venv-velovi` 两个解释器里分别用 `importlib.util.find_spec()` 检查 scanpy、anndata、scvelo、cellrank、igraph、torch、scvi、regvelo、deepvelo、unitvelo、veloproj、torch_geometric、dynamo、multivelo、tensorflow、dgl；并用 `importlib.metadata.version()` 打印实际版本；
3. 在两个解释器里调用 `cellflow.device.resolve('auto'/'cpu'/'cuda')`，记录 `torch.__version__`、`torch.version.cuda`、`torch.cuda.is_available()`；
4. 调用 `cellflow.memory.available_bytes()` / `require(1.0)` / `estimate()` / `precheck()`（对 `data/public/geo_GSE84133.h5ad`）；
5. `Get-CimInstance Win32_Processor / Win32_ComputerSystem / Win32_VideoController` 读 CPU、内存、显卡；`Get-Command nvidia-smi` 确认不存在；
6. 只读检查 `results/` 下各任务的 `provenance.json`、`config.json`、`run.log`、`result.json`（任务状态、实际解释器、device、耗时、证据文本）；
7. 阅读 `cellflow/registry.py`、`method_content.py`、`device.py`、`memory.py`、`queue.py`、`runner.py`、`adapters.py`，以及 `requirements*.txt`、`environment-venv*.lock.txt`、`docs/data_pipeline.md`、`docs/test_report.md`、`docs/model_support_matrix.md`、`docs/本机硬件与运行策略.md`、`验证记录.md`。

本次审计未修改任何既有源码或文档；本文件是唯一新增文件。未安装、未卸载、未下载、未启动 GUI。

---

## 7. 无法核实 / 文档不一致（只记录，未改动）

1. **README 自相矛盾**：`README.md:142` 把 veloVI 列入“可运行（11）”，但 `README.md:144` 的“尚未接入（6）”又包含 veloVI，`README.md:290` 也说它“未接入”。实测 `.venv-velovi` 存在、scvi-tools 1.2.0 可导入、任务 `4c967bc71f4d4ccd` completed → **当前真实状态是“可运行”**；“尚未接入”应为 5 个而不是 6 个。
2. **测试数量过时**：`README.md:259` 写“56 个测试”，`验证记录.md` 也写 56；`docs/test_report.md` 记录为 **110 tests OK（2026-10-25 10:05）**。本次未复跑测试，无法独立确认 110 这个数字。
3. **`验证记录.md:250` 与同文件 §7 冲突**：前者说 veloVI “没有可调用代码”，§7 又记录它已装好并跑通（任务、产物、检查点齐全）。以代码与 `results/4c967bc71f4d4ccd` 为准 → 可运行。
4. ~~`README.md:142` 声称 11 个方法端到端跑通~~：`scvelo_deterministic` 已于 2026-10-25 补跑（`results/verified-scvelo-det-1792912892`），README 的测试数与 veloVI 状态也已同步修正。
5. **历史结果与当前代码不一致**：`results/39785a4c937441de`（2026-10-24 20:13）的 `config.json` 是 `method=auto, velocity_model=velovi`，却 completed 且 evidence 写的是 stochastic；当前 `adapters.py:447-449` 会直接拒绝 auto+velovi（`tests/test_review_fixes.py::test_auto_velovi_is_rejected` 也断言 400）。该目录应是修复前的历史产物，本次未复跑该用例。
6. **RegVelo 的 `calls.json` 把 device 写死为 `'cpu'`**（`adapters.py:1169`），不随实际 accelerator 变化；由于该环境未安装、未训练，本机无法实测这一字段。
7. **未核实项**：CUDA/GPU 路径（无硬件）、RegVelo 训练、真实数据收敛性、> 1 GB 数据内存曲线、Qt/GUI 交互，本次均无法实证。
