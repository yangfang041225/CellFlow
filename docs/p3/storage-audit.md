# P3-02 临时文件与磁盘落点审计

> 审计基线：**HEAD = `f688fc7`**（分支 `p3/batch1-data-acquisition`）
> 审计对象：`cellflow/**/*.py`（35 个文件）、`tools/**`（源码 10 个 .py / 11 个 .ps1）、仓库根 `app.py / launch.py / install.py / cellflow_app.pyw`
> 本报告所有行号均取自 **git 对象 HEAD**，不受工作树并发改动影响（见 §8）。
> 只读审计：未修改任何产品源码；唯一写入是本文件。

---

## 0. 结论摘要（TL;DR）

用户抱怨属实，且有**两条独立的原因链**：

1. **中间文件写系统盘**：全仓库仅 2 处 `tempfile.mkdtemp`，但正好覆盖"下载"和"解压"两个最大的阶段，默认落到 `%TEMP%`（本机在 C 盘）：
   - `cellflow/publicdata.py:551` — GEO 下载 + 解压 + 解析的**全部**原始文件
   - `cellflow/importers.py:398` — 用户导入 zip/tar.gz 的**全部**解压产物

2. **"数据根"根本不是可配置概念**：`data/`、`results/`、`logs/` 全部是 `ROOT = Path(__file__).resolve().parents[1]` 下的硬编码子目录（`cellflow/api.py:30-33`、`workspace.py:17-19`、`queue.py:35-36`），**数据永远落在程序安装目录所在盘**。用户"把最终数据放 D 盘"目前只能靠手动移动文件，程序不会跟随。

并且用户看到"数 GB"不只是临时文件——**每跑一个任务会把输入文件复制 2 份并永久保留**（`queue.py:381-388` + `runner.py:253-254`），且删除任务**不删文件**（`queue.py:882-891`）。这是长期占用的主因，比 tempfile 更值钱。

**本机磁盘现状（实测，见附录 A）**：C 盘可用 **7.04 GB** / 共 182.75 GB；D 盘可用 **55.1 GB**。数据根默认在 C 盘安装目录内 → 必然撑爆。

**治理主张**：默认值严格等于现状（不迁移、不改任何已登记数据的绝对路径）；只有用户显式确认切换数据根后，**新产生的文件**才落到新根。中间文件（下载/解压/转换）作为纯新增目录，可以直接改造，不影响任何老数据。

---

## 1. 审计基线与方法

| 项 | 值 |
|---|---|
| 分支 | `p3/batch1-data-acquisition` |
| HEAD | `f688fc7 docs: add 2026-10-26 public-data download hotfix report` |
| 数据根（当前实际） | `<repo>\data` ，`<repo>` = `C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix` |
| 系统临时目录（实测） | `TEMP = TMP = C:\Users\mtgmc\AppData\Local\Temp`（**C 盘**）；`TMPDIR` 未设置 |
| 系统临时目录可用空间 | **7.04 GB**（`shutil.disk_usage(gettempdir())`，与 C 盘一致） |
| 运行环境 | CPython 3.11.15，`.venv` |

方法：
1. 对 `cellflow/**/*.py` 与 `tools/**` 穷举关键字：`tempfile / mkdtemp / TemporaryDirectory / NamedTemporaryFile / gettempdir / TMPDIR / AppData / expanduser / shutil.rmtree / zipfile / tarfile / write_h5ad / savefig / npz / sqlite / cache / staging / download`。
2. 每个命中点读完整函数上下文，判定"真实落盘路径表达式 + 触发操作 + 生命周期"。
3. **行号全部用 `git show HEAD:<file>` 抽取**（命令见附录 B），避免工作树并发修改导致的漂移。
4. 体积量级优先用**实测文件大小**（附录 A），推不出的明确写"未知"。

> ⚠️ 审计期间工作树被并发修改（另一个成员正在实现 P3-02）。§2–§7 一律描述 **HEAD 现状**；对该在途改动的评估单独放在 §8。

---

## 2. 全量盘点

分类：
- **A 类 — 落在 `%TEMP%`（系统盘 C:）**：用户抱怨的直接来源
- **B 类 — 落在"数据根"（当前 = 安装目录 = C:）**：把 C 盘当数据盘
- **C 类 — 体积倍增器**：同一份数据被复制多份 / 长期不清理
- **D 类 — 无门禁、无清理**：失败/强杀后留下孤儿
- **E 类 — 工具链与缓存**：安装期、开发期

### A 类：落在 `%TEMP%`（系统盘）

| # | 文件:行号 (HEAD) | 当前落点表达式 | 触发操作 | 写入内容 | 估算体积 |
|---|---|---|---|---|---|
| A1 | `cellflow/publicdata.py:551` | `Path(tempfile.mkdtemp(prefix=f'cellflow_geo_{gse}_'))` | ① `POST /api/downloads`（`api.py:319`）② `POST /api/datasets/public`（`api.py:357`）③ Streamlit「公开数据」页（`studio/pages/data.py:42`）④ CLI `python -m cellflow.cli fetch`（`cli.py:36`） | SOFT 元数据 + **每个补充文件原件** + **每个压缩包解压产物** | **Σ(补充文件) + Σ(解压后)**；解压累计上限 4 GiB（`archive.py:11`），读取侧单文件上限 4 GiB（`data.py:20`，**只约束读取、不约束下载**）→ 峰值可达 **~8 GB/数据集**；实测 GSE72857：下载 16.4 MB → 产物 106 MB（6.8×，附录 A） |
| A2 | `cellflow/importers.py:398` | `Path(tempfile.mkdtemp(prefix='cellflow_import_'))` | 用户导入 `.zip / .tar.gz / .tgz`：`POST /api/datasets/upload`（`api.py:276` `load_data`）→ `data.load`（`data.py:90-94`）→ `importers.import_any` → `from_archive` | 压缩包**全部解压产物** | 与压缩包解压后大小同阶；上限 4 GiB（`archive.py:11`），且 `memory.py:130-131` 按 **压缩包 ×5** 估内存，磁盘同阶 |

**A1 的关键放大因子**（不只是"用 tempfile"，而是 tempfile 里**只增不减**）：
- `publicdata.py:581-583`：每个补充文件 `local = workdir / name` 下载进 workdir，**循环中从不删除**；
- `publicdata.py:589 + 594`：每个压缩包再解压到 `workdir / (name + '_x')`，**同样不删**；
- `publicdata.py:657-664`：只有整个 `geo_fetch` 成功/失败退出时才 `shutil.rmtree(workdir)`。
  → 一个含 10 个文件、合计 5 GB 的 Series，峰值 ≈ 5 GB(原件) + 5–15 GB(解压) = **10–20 GB**，全部在 C 盘 `%TEMP%`，直到函数返回。

**A2 的细节**：`importers.py:400-401` 解压后 `find_candidates`（412-426）会在解压树里递归找 `.h5ad/.loom/.h5/matrix.mtx/*.csv`，即解压产物是**完整**的，不会边解边删。

### B 类：落在"数据根"（当前 = 安装目录 = C 盘）

> 这里没有一个是"可配置"的：全部写死为 `ROOT / 'xxx'`，`ROOT` 由 `Path(__file__).resolve().parents[1]` 决定。

| # | 文件:行号 (HEAD) | 落点表达式 | 触发操作 | 说明 |
|---|---|---|---|---|
| B1 | `cellflow/api.py:31` | `ALLOWED_ROOTS = (ROOT/'results'/'inputs', ROOT/'data'/'public', ROOT/'examples')` | 任何读取本地数据集的请求 | **白名单写死**。数据根一旦换到 D 盘，`_safe()`（`api.py:64-71`）立刻 403 |
| B2 | `cellflow/api.py:249-251` | `target_dir = ROOT/'results'/'inputs'`；临时名 `.upload-<uuid><suffix>` | `POST /api/datasets/upload` | 流式落盘，单文件上限 1 GiB（`api.py:234`），落盘后再 `os.replace`（268）、解析校验（275-279） |
| B3 | `cellflow/api.py:319` | `downloads.submit(..., ROOT/'data'/'public')` | `POST /api/downloads` | 下载发布目录写死 |
| B4 | `cellflow/api.py:357` | `fetch(source, identifier, ROOT/'data'/'public')` | `POST /api/datasets/public` | 同步下载（无后台任务），直接写数据目录 |
| B5 | `cellflow/api.py:513` | `ROOT/'examples'/'synthetic.npz'` | `POST /api/datasets/demo` | 小（实测 examples 目录 0.1 MB） |
| B6 | `cellflow/downloads.py:85-86` | `_default_outdir() -> ROOT/'data'/'public'` | 下载任务的默认 outdir | 与 B3 重复的第二个真相源 |
| B7 | `cellflow/downloads.py:210` | `staging = Path(outdir)/'.downloads'/task_id` | 每个下载任务 | 在数据目录**内部**建隐藏暂存区；实测只残留 `state.json`（附录 C） |
| B8 | `cellflow/workspace.py:17-19` | `STORE=ROOT/'results'`；`PUBLIC=ROOT/'data'/'public'` | 数据集登记、项目表 | **两套路径写死**，且 `list_datasets` 只扫 `PUBLIC`（290-299） |
| B9 | `cellflow/workspace.py:300` | `EXAMPLES/'synthetic.npz'` | 数据集列表 | 小 |
| B10 | `cellflow/queue.py:36` | `STORE = ROOT/'results'` | 队列库、输入快照、任务输出 | **数据库里存的是绝对路径**（见 C1） |
| B11 | `cellflow/queue.py:791-799` | `store/<job_id>.json`、`store/<job_id>.worker.log` | 每次任务执行 | 小 |
| B12 | `cellflow/runner.py:249-254` | `folder = <output>`；`local = folder/('input'+suffix)` | 每次任务执行 | **完整复制输入**（见 C2） |
| B13 | `cellflow/runner.py:275` | `folder/'run.log'` | 每次任务执行 | 小，无轮转 |
| B14 | `cellflow/runner.py:290-295` | `folder/'source'/...` 复制全部 `cellflow/*.py`+`app.py` | 每次任务执行 | 每个任务一份源码副本（几十 KB × N 任务） |
| B15 | `cellflow/adapters.py:624,913,1239,1243,1261,1416` | `np.savez_compressed(folder/'velocity.npz' / 'velocity_uncertainty.npz' / 'latent_time.npz')` | 速度类方法运行 | **按 nnz 缩放**；CRC 30k×2 万非零 ≈ 2–3 GB 未压缩量级（估算，未实测） |
| B16 | `cellflow/adapters.py:713,719,753,1052` | `a.write_h5ad(folder/'analysis.h5ad')` | auto / 速度类方法 | **等于再存一份完整 AnnData**（含 spliced+unspliced） |
| B17 | `cellflow/adapters.py:1336` | `model.save(str(folder/'velovi_model'))` | veloVI 运行（隔离环境 `.venv-velovi`） | torch 权重 + 优化器状态，**未知**（依赖 n_latent/n_hvg/max_epochs） |
| B18 | `cellflow/adapters.py:1477` | `model.save(str(folder/'regvelo_model'), save_anndata=True)` | RegVelo 运行 | `save_anndata=True` 会把 **adata 一起写进模型目录** → 再一份数据量级 |
| B19 | `cellflow/reports.py:673` / `:293` | `folder/'figures'` / `fig.savefig(out_path)` | 每次任务 | SVG 为主 + `stream.png`（`reports.py:698`），MB 级 |
| B20 | `cellflow/reports.py:1867-1868` | `folder/'A_生物学分析报告.md'` / `A_report.html` | 每次任务 | 小 |
| B21 | `cellflow/pdf.py:65-70` | `pdf_path.parent.mkdir(...)`，PDF 与 HTML 同目录 | `GET /api/runs/{id}/report/{kind}/pdf`（`api.py:661-695`） | 小 |
| B22 | `cellflow/api.py:33,47-49` | `LOGS = ROOT/'logs'`，`logs/api-errors.log` 追加 | 未捕获异常 | 无轮转、无上限 |
| B23 | `cellflow/desktop_qt.py:27` | `(logs/'qt-native.log').open('a')` | 每次启动 | 无轮转 |
| B24 | `cellflow/desktop_qt.py:79-80` | `profile.setPersistentStoragePath(logs/'qt-profile')`、`setCachePath(.../'cache')` | 每次启动 | Chromium profile；已禁 HTTP 缓存（`:82`） |
| B25 | `cellflow/desktop_qt.py:124` | `logs/'desktop-ready.json'` | 窗口就绪 | 小 |
| B26 | `cellflow/registry.py:305,347-348` | `root/'results'/'isolated-health.json'` | 查询隔离环境状态 | 小；**是缓存，不是数据** |
| B27 | `cellflow/studio/pages/data.py:19-20` | `PUBLIC = ROOT/'data'/'public'`；`INCOMING = ROOT/'results'/'inputs'` | Streamlit 界面导入 | 与 B2/B8 **第三套写死路径** |
| B28 | `cellflow/studio/pages/data.py:28-32` | `raw = upload.getbuffer()` → `target.write_bytes(raw)` | 界面导入 | **整文件进内存**再落盘（无 1 GiB 限制，与 API 不一致） |

### C 类：体积倍增器（这是"数 GB"的真正主因）

| # | 证据 | 事实 | 量级 |
|---|---|---|---|
| C1 | `cellflow/queue.py:381-392` | 提交任务时把输入**完整复制**到 `store/'inputs'/<id><suffix>`，先写 `.part` 再 `os.replace` | +1× 输入 |
| C2 | `cellflow/runner.py:253-254` | 执行任务时把输入**再复制一次**到 `<output>/input<suffix>` | +1× 输入 |
| C3 | `cellflow/adapters.py:713,753,1052` | 再写 `analysis.h5ad`（含 spliced/unspliced 层） | +1–2× |
| C4 | `cellflow/queue.py:882-891` | `delete()` 只删数据库行，注释明写"结果文件保留" | 永不回收 |
| C5 | 无任何代码路径删除 `results/inputs/*` 快照 | 输入快照只增不减 | 永不回收 |
| C6 | 无保留策略/容量上限/GC | 全仓库无 `disk_usage` 门禁（唯一一处 `api.py:940` 仅用于展示） | — |

**推导（以 4 GiB 输入为例，均可由 C1–C3 逐项相加得到）**：
`4(快照) + 4(input 副本) + 4~8(analysis.h5ad) + velocity*.npz ≈ 14–20 GB / 次运行`，且 C4/C5 保证它们**永远留在 C 盘**。
实测参照：GSE72857 只 106 MB 的 h5ad，一次运行也会产生 ~300 MB 的 results 目录（比例同阶）。

### D 类：无空间门禁、无孤儿清理

| # | 证据 | 问题 |
|---|---|---|
| D1 | 全仓库无下载前/解压前空间检查（`grep disk_usage` 仅 `api.py:940` 展示用） | C 盘 7 GB 剩余时仍会开始下 10 GB，中途失败留下半成品 |
| D2 | `publicdata.py:111-163` `download_file` 的断点续传以**正式文件名**为进度源（`have = target.stat().st_size`，125 行），无 `.part` 分离 | 半成品与成品同名；`target.exists()` 会被后续逻辑当成"已下载" |
| D3 | `publicdata.py:663-664`、`importers.py:408-409`、`downloads.py:264-270` 都是 `finally: rmtree` | **进程被强杀/断电时 finally 不执行** → `%TEMP%\cellflow_geo_*` / `cellflow_import_*` 永久残留 |
| D4 | `downloads.py:74-76` 只把未完成任务标 `interrupted`，**不清理**其 staging 目录；`_load` 无扫盘 | 重启后 `data/public/.downloads/<id>/` 可能残留整个 h5ad |
| D5 | `api.py:251-273` 上传临时文件 `.upload-<uuid>` 只在异常分支 unlink | 进程被杀 → `results/inputs/.upload-*` 残留，且 `workspace._dataset_candidate` 只跳过 `.json/.log/.sqlite/.txt`（`workspace.py:313`），**不会**跳过 `.upload-*` 以外的垃圾；`.h5ad` 后缀的 .upload 文件会被当成数据集 |
| D6 | `api.py:890-902` 导出 ZIP 用 `io.BytesIO()` **全内存**构建（`full=1` 可含 200 MB 模型预算，`api.py:848`） | 不落盘，但等于 1 份数据驻留内存；大任务导出会 OOM 而非"写坏盘" |

### E 类：工具链 / 安装期 / 环境

| # | 文件:行号 (HEAD) | 事实 | 体积 |
|---|---|---|---|
| E1 | `install.py:51-53` | `UV_CACHE_DIR=ROOT/'.cache'/'uv'`、`UV_PYTHON_INSTALL_DIR=ROOT/'.runtimes'` | uv 缓存可数 GB（含 torch CPU wheel ~200 MB）；**已成功从 %TEMP% 挪到 repo**，方向正确 |
| E2 | `install.py:64,69,89` | `installation.json`、`environment-*.lock.txt` 落 repo 根 | 小 |
| E3 | 仓库根存在 `pip-build-tracker-*`、`pip-ephem-wheel-cache-*`、`pip-install-*` 目录 | pip 的构建隔离/临时下载目录**曾落在 repo 根**（说明某次运行把 TEMP 指向了 cwd）；源码编译的包会在这里展开数 GB | 已存在，未清理 |
| E4 | `tools/fetch_wheels.py:259-277` | 下载 `outdir/<filename>.part` → `replace` 正式名；默认 `-o wheels`（`fetch_wheels.py:297`，**相对 cwd**） | torch 栈 2–3 GB；`.part` 模式**是全仓库最规范的一处**，可作为其它点的模板 |
| E5 | `tools/_hotfix/01-build-frontend.ps1:14-15`、`05-e2e.ps1:4-6`、`06-qt-e2e.ps1:4-6`、`08-full-suite.ps1:5-6` | 开发/验收脚本显式把 `TMP/TEMP/TMPDIR` 指向 `tools/_hotfix/tmp`（注释说明机器级 TEMP 里有坏文件） | 实测 `tools/_hotfix/tmp` = 2.9 MB；**仅开发用，不属于产品运行时** |
| E6 | `tools/_hotfix/07-regression-proof.ps1:6,11` | 把 `cellflow/downloads.py` 备份到 `$env:TEMP\_downloads_fixed.py` | 开发脚本，小 |
| E7 | `cellflow/desktop.py:53-58,76-81` | `Scripts/pythonw.exe.repair` 临时文件（安装目录内，同盘 rename） | 小，已 finally 删除 |
| E8 | 根目录 `tmpw4kzovnf`（6.3 KB，未跟踪） | `tempfile.gettempdir()` 在本会话沙箱里**回退到了 cwd**，于是 mkdtemp 落在 repo 根。这是**审计环境**现象（Python 无法在 `%TEMP%` 建目录时会 fallback 到 `os.getcwd()`，见附录 B-3），产品正常环境下不会发生；但它同时证明：**一旦 TEMP 不可写，临时文件就会落到程序目录** | 见 §9 未验证 |

---

## 3. 真实体积证据（实测，非估算）

| 对象 | 大小 | 来源 |
|---|---|---|
| C 盘可用 / 总 | **7.04 GB** / 182.75 GB | `shutil.disk_usage(gettempdir())` |
| D 盘可用 | **55.1 GB** | `Get-PSDrive` |
| `data/public/geo_GSE72857__ed86d783.h5ad` | **111,205,145 B (106.05 MiB)** | 文件实测 |
| 同一数据的第二份副本 `data/_geo_probe/geo_GSE72857.h5ad` | **111,205,145 B** | 文件实测（**重复占用**，见 §8 注） |
| GSE72857 唯一补充文件 `GSE72857_umitab.txt.gz` | **16,413,252 B (15.65 MiB)** | `tools/_hotfix/http/downloads-get-10.json` 里 `done/total` |
| 下载→产物膨胀比 | **6.8×** | 111,205,145 / 16,413,252 |
| `data/` 合计 | 212.2 MB / 6 files | 递归实测 |
| `results/` 合计 | 0.0 MB / 3 files（仅 `queue.sqlite`、`projects.json`、`public_ignored.json`） | 递归实测 |
| `logs/` 合计 | 0.0 MB / 2 files | 递归实测 |
| `tools/_hotfix/tmp` | 2.9 MB / 175 files | 递归实测 |
| 同一 GSE 的下载任务记录 | `state.json` 里 **4 条全部 `done`**，同一 `path` | `data/public/.downloads/state.json` |

**结论**：`gettempdir()` 所在卷 = C 盘，剩余 7.04 GB；而单个 GEO Series 的中间文件峰值上限（4 GiB 原件 + 4 GiB 解压）就已超过剩余空间。用户"数 GB 写 C 盘"是可复现的必然结果，不是偶发。

---

## 4. 数据根目录治理设计

### 4.1 目标约束（按优先级）

1. **默认行为 = 现状**：默认数据根仍是 `<repo>/data`，已登记数据集的绝对路径**一个都不变**。
2. 中间文件（下载/解压/转换/导入）**永不写 `%TEMP%`**。
3. 只有用户显式确认后才切换落点，且**不自动迁移**、**不自动删除**任何已有数据。
4. 清理只针对本程序在 staging 下创建的任务目录，绝不递归删除用户目录。

### 4.2 目录布局

```
<DATA_ROOT>/                          # 默认 = <repo>/data （= 改造前位置）
├─ datasets/
│  ├─ downloaded/                     # 公开数据下载后的正式文件（内容版本化命名 <stem>__<sha8>.<ext>）
│  ├─ imported/                       # 用户上传/导入的原始文件快照
│  └─ manifests/                      # 每个数据集的 manifest.json（sha256 / 来源 / URL / 大小 / 时间）
├─ staging/                           # 全部是「可随时删除」的中间产物
│  ├─ downloads/<task_id>/            # 下载中：*.part
│  ├─ extraction/<task_id>/           # 压缩包解压
│  ├─ conversion/<task_id>/           # 格式转换（h5ad 构建、合并）
│  └─ import/<task_id>/               # 用户导入的临时解压
├─ results/<job_id>/                   # 分析结果（现状位置，见 §7 破坏性说明）
├─ reports/                            # 导出用的报告包/PDF（可选）
├─ cache/                              # 可重建：isolated-health.json、uv/wheels、Qt profile
└─ logs/                               # 运行日志（需轮转）
```

**兼容旧布局**：改造前数据在 `<root>/public`（即 `data/public`）。`datasets_dir()` 必须先检查 "<root>/public 是否存在"，存在则继续返回它 —— 这样升级后**不会**改任何已登记数据集的绝对路径。

### 4.3 需要新增的路径函数（签名 + 语义）

> 下列函数与并发在途的 `cellflow/paths.py`（§8）基本对齐；标注 **[新增]** 的是它**尚未提供**的。

| 函数 | 语义 | 现状 |
|---|---|---|
| `data_root() -> Path` | 当前生效数据根（可能不存在） | 已有 |
| `default_data_root() -> Path` | `repo_root()/ 'data'` | 已有 |
| `layout(root=None) -> dict` | 标准目录字典（键=用途） | 已有 |
| `ensure_layout(root=None)` | 幂等建目录，失败不中断启动 | 已有 |
| `datasets_dir(root=None) -> Path` | **新建数据集默认落点**；有旧 `public/` 时返回它 | 已有 |
| `downloaded_dir() / imported_dir() / manifests_dir()` | 细分目录 | 仅有 layout 键，无独立函数 |
| `staging_dir(kind, task_id='') -> Path` | kind ∈ {downloads, extraction, conversion, import} | 已有 |
| `make_workdir(prefix, kind) -> Path` | 替代 `tempfile.mkdtemp`（唯一允许的一次性目录工厂） | 已有 |
| `results_dir() / job_dir(job_id)` | 结果目录 | **无**（B10/B12 未接线） |
| `reports_dir() / cache_dir() / logs_dir()` | 其余标准目录 | **无**（只有 layout 键） |
| `partial_path(target) -> Path` | `<name>.part` | 已有 |
| `commit_file(temp, final) -> Path` | 同分区原子 rename；目标已存在则报错（**不得静默覆盖**） | 已有 |
| `atomic_write_text(path, text)` | JSON/配置类小文件统一走 tmp+replace | **无**（现散落在 downloads/workspace/queue 各处） |
| `space_report(path) / required_bytes(need, margin) / ensure_free_space(path, need, label)` | 空间门禁 | 已有 |
| `scan_staging(root=None) / cleanup_staging(max_age, dry_run)` | 孤儿识别与清理 | 已有 |
| `cleanup_orphans(max_age, dry_run)` | **[新增]** 额外覆盖：`*.part`、`.upload-*`、`results/inputs/*.part`、已 `interrupted` 任务的 staging | **无** |
| `validate_root(path) / set_data_root(path, create=False)` | 切换前的存在性/写权限/空间校验 | 已有 |
| `migrate_data_root(old, new, dry_run)` | **[新增]** 显式迁移（copy+校验+重登记）。**默认不提供自动调用** | **无** |

### 4.4 配置从哪里读（优先级从高到低）

1. **环境变量** `CELLFLOW_DATA_ROOT`（企业/脚本部署用；优先级最高，覆盖设置文件）
2. **设置文件** `%LOCALAPPDATA%\CellFlow\settings.json` → `{"data_root": "D:\\CytelleData"}`
   （`%LOCALAPPDATA%` 缺失时回退 `<repo>/.cellflow-settings.json`；可用 `CELLFLOW_SETTINGS_FILE` 覆盖，便于测试）
3. **默认值** `<repo>/data`（= 改造前，零迁移）

**首次运行策略（推荐）**：
- **不要**弹窗强制选择路径（会打断老用户，且无法保证有 D 盘）。
- 在「设置 → 数据与存储」页展示：当前数据根、所在卷剩余空间、staging 占用、可清理量。
- 当检测到 **数据根所在卷剩余 < 阈值（建议 10 GB）** 时，在数据页顶部给一条可关闭的提示，引导用户选择新目录。依据：本机 C 盘仅 7.04 GB（附录 A）。
- 切换必须带 `confirm=true`（已在 API 层体现，见 §8）。

### 4.5 不破坏既有安装的迁移规则

| 场景 | 行为 |
|---|---|
| 老用户升级，不动设置 | 数据根 = `<repo>/data`，`datasets_dir()` 返回旧 `data/public` → **零变化** |
| 用户把根切到 `D:\CytelleData` | 后续**新**下载/导入去 D；旧数据集仍在 C 且**继续可用**（登记表存绝对路径，见 `workspace.py:172-204`） |
| 读取白名单 | 必须 = 旧目录 ∪ 新根（否则切换后旧数据集 403，见 B1） |
| 用户想"搬家" | 独立命令 `migrate_data_root`：复制 → 逐文件 sha256 校验 → 重写 `datasets.json`/`queue.sqlite` 的绝对路径 → **用户确认后**才删除旧文件。默认不执行 |
| 回滚 | 删除/修改设置文件里的 `data_root` 即回默认；旧数据从未被删 |

---

## 5. 补丁点清单

> 列 = 编号 / HEAD 位置 / 现状 / 改成 / 兼容性注意。**"改" 一律指"新文件的落点"，不搬迁已存在文件。**

| 编号 | HEAD 位置 | 现状 | 改成 | 兼容性注意 |
|---|---|---|---|---|
| P1 | `publicdata.py:551` | `tempfile.mkdtemp('cellflow_geo_<gse>_')` | `paths.make_workdir(f'cellflow_geo_{gse}_', kind='downloads')` | ① 单测 `test_publicdata.py` 依赖 `TemporaryDirectory`，需允许注入 root；② workdir 必须与最终 outdir **同卷**（否则 §5.1 的 publish 会退化成长时间跨盘复制）|
| P2 | `publicdata.py:578-613` | 循环内只下载不清理 | 每处理完一个 `name` 立即删 `workdir/name` 与 `workdir/(name+'_x')` | 失败跳过时也要删；否则"只增不减"（A1 放大因子）照旧 |
| P3 | `publicdata.py:566-568` | SOFT 写 workdir | 随 P1 自动迁移 | SOFT 是元数据，建议放 `staging/downloads/<task>/meta/` 并在成功后**不**保留 |
| P4 | `publicdata.py:659-660` | `out = outdir/geo_<gse>.h5ad`；`merged.write_h5ad(out)` | 先写 `outdir / f'geo_{gse}.h5ad.part'` 再 `os.replace` | 崩溃后不留"看起来完整"的半个 h5ad；`downloads._publish` 会按内容改名，不受影响 |
| P5 | `publicdata.py:635` | `Path(out).with_name(...)`，而 `out` 在 **659 行才赋值** → `NameError` 被 `except Exception: pass`（642）吞掉 | 改为 `outdir / f'geo_{gse}.h5ad.meta.json'` | **既有 bug**：多物种被丢弃的元数据从未写盘（`downloads.py:244-249` 因此永远读不到 `species_filtered`） |
| P6 | `importers.py:398` | `tempfile.mkdtemp('cellflow_import_')` | `paths.make_workdir('cellflow_import_', kind='import')` | 解压上限 4 GiB（`archive.py:11`）→ 需在解压前做空间门禁 |
| P7 | `downloads.py:85-86` | `ROOT/'data'/'public'` | `paths.datasets_dir()` | 与 B3 是**两个真相源**，必须一起改，否则 `get()` 的 `_load(_default_outdir())` 会漏任务 |
| P8 | `downloads.py:210` | `staging = outdir/'.downloads'/task_id` | **保持现状**（不要改成 `data_root/staging`） | `_publish` 用 `shutil.move`（191 行）：跨卷会退化为"复制+删除"，耗时翻倍且要求 2× 空间。staging 必须与 outdir 同卷 |
| P9 | `downloads.py:210-271` | 无空间门禁；`interrupted` 只标记不清理 | ① `submit` 前 `ensure_free_space`；② `_load` 时对 `state==interrupted` 的 task 清 staging；③ 完成后 `workspace.register_dataset(final)` | **③ 是隐藏缺口**：`/api/downloads` 完成的文件目前只靠 `workspace.list_datasets` 扫目录发现，而扫描目录写死 `data/public`（`workspace.py:290`）；换根后下载成功但列表里看不到 |
| P10 | `api.py:31,68` | 静态 `ALLOWED_ROOTS` | `allowed = 旧三目录 ∪ {storage.data_root(), storage.datasets_dir()}` | 必须**保留旧目录**，否则换根后老数据集全部 403 |
| P11 | `api.py:249` | `ROOT/'results'/'inputs'` | `paths.imported_dir()`（或保持并与 `queue.store` 同卷） | 上传后立刻 `register_dataset(target)`（280）存的是**绝对路径** → 属破坏性变更（§7）|
| P12 | `api.py:319,357` | `ROOT/'data'/'public'` | `storage.datasets_dir()` | 同 P7 |
| P13 | `api.py:33,47-49` / `desktop_qt.py:27` / `desktop.py` 日志 | `ROOT/'logs'` | 可选：`paths.logs_dir()` + `RotatingFileHandler` | 日志不被数据库引用，可安全改；但仍建议**保留旧 `logs/`**（用户会去老地方找日志） |
| P14 | `workspace.py:18-19,290-299` | `STORE/PUBLIC` 写死；只扫一个目录 | `PUBLIC = paths.datasets_dir()`；扫描时**合并** 旧 `data/public` 与新根 | `unregister_dataset`（`workspace.py:257`）也用 `PUBLIC.resolve()` 判定"公共文件"→ 必须同步，否则取消登记会写错 `public_ignored.json` |
| P15 | `queue.py:36` | `STORE = ROOT/'results'` | **默认不动**（见 §7）。若要支持换根：增加 `CELLFLOW_STORE`，并一次性重写 `jobs.output/jobs.input` | 数据库存绝对路径（`queue.py:396,400`）→ 换根会使所有历史任务"输出不存在" |
| P16 | `queue.py:381-392` | `shutil.copyfile` 复制输入到 `results/inputs` | 同卷优先 `os.link()`（NTFS 硬链接，**0 额外空间**），跨卷或不支持时回退 copyfile；失败再回退 | 硬链接要求同卷；注意 `os.replace` 不能用于硬链接目标（用 link 后 unlink staging）。**这一条是大头收益** |
| P17 | `runner.py:253-254` | `shutil.copyfile(source, folder/'input'+suffix)` | 同卷 `os.link`；或仅记录绝对路径+sha256（`provenance` 已有 digest，259 行）不复制 | 报告第 7 节"复现方式"（`reports.py:1829-1835`）引用 `input` 文件名，若改为不复制需同步改文案 |
| P18 | `adapters.py:1477` | `model.save(..., save_anndata=True)` | 评估改为 `save_anndata=False`，adata 已由 `analysis.h5ad` 保存 | 会改变模型目录内容 → 复现脚本/校验需同步；属**行为变更**，建议单独评审 |
| P19 | `adapters.py:1336` | veloVI 权重落 `folder/velovi_model` | 保持；在 `provenance` 里记录模型目录体积 | 无路径变更 |
| P20 | `reports.py:293,673,1867` | 全部相对 `folder` | **无需改** | 自动跟随 `folder`（即 `queue.output`）|
| P21 | `pdf.py:70` | PDF 与 HTML 同目录 | **无需改** | `api.py:758-774` 用路径拼装，移动会 404 |
| P22 | `registry.py:305` | `results/isolated-health.json` | `paths.cache_dir()/isolated-health.json` | 纯缓存：旧文件留着无害，新文件重新生成（6h TTL，`registry.py:299`）|
| P23 | `desktop_qt.py:79-80` | `logs/qt-profile` | `paths.cache_dir()/qt-profile` | 纯缓存；Chromium 会自行重建。**改后首次启动会有一次冷启动** |
| P24 | `studio/pages/data.py:19-20,28-32` | 第三套写死路径 + 整文件进内存 | 复用 `paths`；改为分块读 (`upload.read(4MB)`) 且有 1 GiB 上限 | 与 API 行为一致（`api.py:234`）|
| P25 | `install.py:51-53` | `ROOT/.cache/uv`、`ROOT/.runtimes` | 可选：`UV_CACHE_DIR=<data_root>/cache/uv` | 安装期才用；换根后旧缓存不迁移（可重建）。注意 **uv/pip 仍会用 %TEMP% 做构建隔离**（证据：repo 根的 `pip-*` 目录），如需彻底治理要同时设 `TMPDIR`|
| P26 | `.gitignore` | 已忽略 `data/`、`results/`、`logs/`、`.cache/`、`tmp*/`、`pip-*/` | 若数据根改为 `<repo>/datasets` 或新增 `staging/`：追加 `datasets/`、`staging/`、`wheels/`、`*.part` | 不改会把这些**大数据**误提交进 git（当前 .gitignore 755 B，见附录 A）|
| P27 | `api.py:890-902` | `io.BytesIO` 全内存打包 | 流式写到 `<data_root>/staging/export/<job>.zip` 再 `FileResponse`，响应后删 | 不落盘改为落盘：需保证 `finally` 删除；否则反过来新增孤儿。属**可选优化** |

### 5.1 一个必须写进设计的约束：staging 与 publish 必须同卷

`downloads.py:191` 用 `shutil.move` 把文件从 `<outdir>/.downloads/<task>/` 移到 `<outdir>/`。若把 staging 统一改成 `<data_root>/staging/...` 而 `outdir` 仍是旧 `C:\...\data\public`，`shutil.move` 会退化为**跨盘复制**（读+写整份文件，耗时翻倍且临时占 2× 空间）。
→ 结论：**下载的 staging 必须由 outdir 推导**（现状即正确），只有"解压/转换"这类不 publish 到 outdir 的中间目录才放 `<data_root>/staging`。

---

## 6. 空间门禁与临时文件生命周期

### 6.1 门禁（每个阶段前）

| 阶段 | 需要量的计算 | 检查点 | 依据 |
|---|---|---|---|
| 下载前 | `Σ(manifest 里每个文件的 size_bytes)`；拿不到大小则退化为 `未知 → 仅要求 MIN_FREE_BYTES` | 目标卷（outdir 所在卷） | P3-01 已能只读拿到目录页大小（`publicdata.py` 在途新增 `_size_to_bytes`）|
| 解压前 | `archive_size × 5`（与 `memory.py:130-131` 同一经验系数），并受 `archive.py:11` 的 4 GiB 上限约束 | 解压目标卷 | 系数已存在于内存估算，口径一致 |
| 转换/合并前 | `Σ(输入) × 2`（原件+产物） | staging 卷 | GSE72857 实测 6.8× 膨胀（`h5ad` 压缩率高，故按 2× 保守下界）|
| 提交任务前 | `input_size × 3`（快照+input 副本+analysis.h5ad）；**若采用 §5 P16/P17 的硬链接则为 `input_size × 1`** | `results` 所在卷 | C1/C2/C3 |
| 运行前 | 现有 `memory.require()` 只管内存（`memory.py:156-163`），**需并列增加磁盘检查** | 结果卷 | — |

门禁失败必须是**中文可读**错误（沿用 `paths.InsufficientSpace` 风格），并在任务/下载记录里落为 `failed`，不能"永远排队"（这是 `downloads.py:201-209` 已修复过的同类缺陷）。

### 6.2 `.part` 与原子提交

规范（以 `tools/fetch_wheels.py:271-277` 为范本）：

1. 下载**一律**写 `<目标名>.part`，绝不直接写正式名；
2. 续传读取 `.part` 的大小（不是正式文件），发 `Range: bytes=<n>-`；
3. 服务端忽略 Range（返回 200）时，必须 `wb` 重写而不是追加；
4. 收完后校验：`content-length` 对得上（或 `sha256` 与 manifest 一致）；
5. **同目录** `os.replace(part, final)`（跨卷会失败，这也是"同卷"约束的另一个理由）；
6. 已存在正式文件时：`commit_file` **报错而非覆盖**（`paths.commit_file` 已如此），由下载层用内容版本化命名 `<stem>__<sha8>.<ext>`（`downloads.py:174-192` 已实现）。

> **兼容陷阱（必须处理）**：HEAD 的 `download_file` 用**正式文件名**做续传（`publicdata.py:125`），所以旧版本可能已经留下"同名但截断"的文件。切换到 `.part` 方案后，如果新增"目标已存在即跳过"的短路判断，会把这些历史半成品**当成已下载完成**静默接受。
> → 必须做一次性校验：对 `data/public/` 下既有 h5ad 做可读性/大小校验（或直接按内容哈希重命名为 `__<sha8>` 版本），确认后才允许"已存在即跳过"。

### 6.3 失败/取消清理

| 场景 | 现状（HEAD） | 建议 |
|---|---|---|
| 正常失败/取消 | ✅ `finally: rmtree`（`publicdata.py:663-664`、`importers.py:408-409`、`downloads.py:264-270`）| 保持；额外删 `.part` |
| 进程被强杀 / 断电 | ❌ 无任何清理 | **启动时 sweep**（见 6.4）|
| 取消 | ✅ `downloads.cancel` → `should_cancel` 每 MB 检查（`publicdata.py:146-147`）| 保持；取消后必须删 `.part`（否则下次续传的是"上一次的不完整数据"，虽可续但语义不清）|
| 任务失败 | ✅ `_fail` 写终态（`downloads.py:195-198`）| 保持 |

### 6.4 孤儿识别与清理

**识别对象**（按优先级）：
1. `<data_root>/staging/<kind>/<task>/` — 目录 mtime 超过 N 小时且**不在** active 任务表中
2. `<data_root>/datasets/**/\*.part` — 超过 N 小时未修改
3. `<repo>/results/inputs/.upload-*` — 超过 N 小时
4. `downloads.state.json` 里 `state == interrupted` 的任务 → 对应 staging 目录
5. **绝不**碰：`datasets/` 下的正式文件、`results/<job>/`、`manifests/`

**执行规则**：
- 默认 `dry_run=True`，先返回"将删除 N 项 / 可释放 X GB"给界面（`paths.cleanup_staging` 已支持）；
- 真删前必须 `path.resolve().relative_to(base.resolve())` 校验（`paths.cleanup_staging:337-339` 已实现，是整份代码里唯一的"安全删除"范式，**其它清理点必须复制这个模式**）；
- 不跟随符号链接；不删除 staging 之外任何路径；
- 清理动作写日志（`logs/storage-cleanup.log`）。

### 6.5 异常退出恢复

已有基础：
- 下载任务：`downloads.py:74-76` 启动时把 `queued/running/cancelling` 标为 `interrupted` 并提示"不覆盖已有文件"；
- 队列：`queue.recover_orphans`（`queue.py:571-596`）回收失联 `running` 任务并杀无主子进程。

缺口：
- `interrupted` 任务的 **staging 目录不会被清**（只改状态）；
- `interrupted` 任务的 **部分下载文件**留在 staging，永不续传也永不删除；
- `publicdata` 的 `workdir` 在强杀后是**完全不可知**的（名字带随机后缀，且无登记文件）→ 建议在 `make_workdir` 同时写一个 `<workdir>/.owner.json`（含 pid/时间/任务 id），让启动时的 sweep 能判断"是否还有活进程"。

---

## 7. 破坏性 vs 纯新增（必须分开评审）

### 7.1 会改变已有数据绝对路径 —— 破坏性，需要迁移方案 + 用户确认

| 变更 | 谁引用了这些绝对路径 | 若不迁移的后果 |
|---|---|---|
| `queue.STORE` / `jobs.output`（`queue.py:36,396`） | `queue.sqlite` 的 `output`/`input` 列；`api.py:617,645` 读结果目录；`reports.py:` 里 `reproduce_cwd`/`reproduce_command`（`runner.py:261,268-271`） | 所有历史任务"结果不存在"；报告里的复现命令全部失效 |
| 上传落点 `results/inputs`（`api.py:249`） | `workspace.datasets.json` 存的是 `Path(path).resolve()`（`workspace.py:174,194`） | 已登记数据集失联；`_safe()` 403 |
| `workspace.PUBLIC` 扫描目录（`workspace.py:19,290`） | 数据集列表、"公开数据"来源标记 | 换根后新下载**不显示**；老数据若目录不再扫描则"消失"（文件还在） |
| `REGISTRY` 路径解析 `_safe`/`ALLOWED_ROOTS`（`api.py:31`） | 所有按路径读取的 API | 换根即 403 |
| `downloads._default_outdir`（`downloads.py:85`） | `downloads.get()`/`list_tasks()` 的 `_load` | 任务状态分裂成两份，列表丢任务 |

**统一迁移方案**（必须显式触发）：
1. 校验新根（存在/可写/空间）→ 2. 复制（保留相对结构）→ 3. 逐文件 sha256 与 `manifests` 比对 → 4. 事务性重写 `datasets.json`、`projects.json`、`queue.sqlite` 的绝对路径列 → 5. 写 `migration.json` 审计记录 → 6. **用户确认后**才删除旧副本。
任何一步失败 → 回滚（新根视为未完成，旧根不动）。

### 7.2 纯新增 —— 不动老数据，可独立合入

| 变更 | 理由 |
|---|---|
| 新增 `cellflow/paths.py` | 新模块，无引用方 |
| `publicdata.py:551` → `make_workdir(kind='downloads')` | 临时目录名/位置变化，老数据无引用 |
| `importers.py:398` → `make_workdir(kind='import')` | 同上 |
| 新增 `<data_root>/staging/*` | 全新目录 |
| 新增 `*.part` 命名 | 仅影响"下载中"文件；**但需 §6.2 的一次性校验**，否则对历史半成品是行为变更 |
| 新增空间门禁 | 只是多一次拒绝；不改路径 |
| 新增 `cache/`（isolated-health、qt-profile） | 纯缓存，可重建；老文件留着无害 |
| `registry.py:305` → cache | 同上（6h TTL 自动重建） |
| `api.py` 新增 `/api/storage*` 路由 | 新接口 |
| 孤儿清理（dry_run 默认开） | 只删本程序创建的 staging 子目录 |

### 7.3 行为变更但非路径（需单独评审，不要混在存储补丁里）

- `adapters.py:1477` `save_anndata=True` → `False`（改变模型目录内容）
- `queue.py:381-392` / `runner.py:253-254` 由"复制"改"硬链接/不复制"（改变目录内容与复现语义）
- `api.py:890` 导出 ZIP 从内存改落盘（新增一个临时文件生命周期）
- `publicdata.py:635` 修 `Path(out)` NameError（会**新增**一个 `.meta.json` 产物 → `downloads.py:244-249` 会开始读取它）

---

## 8. 对并发在途改动的评估（截至 2026-10-26 07:10）

审计进行中工作树出现未提交改动（`git status` 见附录 C）：

```
 M cellflow/api.py        (+148)
 M cellflow/downloads.py  (+8)
 M cellflow/importers.py  (+5)
 M cellflow/publicdata.py (+552)
?? cellflow/paths.py      (354 行)
?? tests/test_paths.py  ?? tests/test_geo_manifest.py  ?? tests/test_geo_selected_download.py
?? tools/p3_probe/
```

### 8.1 已经覆盖到的（与 §4/§5 设计一致，可直接沿用）

| 设计点 | 在途实现 | 评价 |
|---|---|---|
| 数据根解析（env > settings > 默认） | `paths.data_root()`（`paths.py:84-92`） | ✅ 优先级与 §4.4 一致 |
| 默认值 = 现状 | `default_data_root() = repo/'data'` + `datasets_dir()` 旧 `public/` 优先（`paths.py:80-81,183-195`） | ✅ 满足"零迁移"硬约束 |
| staging 分类 | `STAGING_KINDS = ('downloads','extraction','conversion','import')`（`paths.py:34`） | ✅ |
| `make_workdir` 替代 mkdtemp | `paths.py:209-226` | ✅ 且做了文件名净化与重名重试 |
| `.part` / 原子提交 | `partial_path`/`commit_file`（`paths.py:269-286`） | ✅ `commit_file` 拒绝覆盖，正确 |
| 空间门禁 | `ensure_free_space`（`paths.py:246-261`） | ✅ 有 MIN_FREE 512 MB + 15% 余量 |
| 安全清理 | `cleanup_staging`（`paths.py:323-354`） | ✅ 已用 `relative_to` 校验，是正确范式 |
| A1 改造 | `publicdata.py` 改为 `paths.make_workdir(kind='conversion')` | ⚠️ kind 建议 `downloads`（见 8.2-1）|
| A2 改造 | `importers.py:399` → `make_workdir(kind='import')` | ✅ |
| 下载 `.part` | `publicdata.py:128-165` 改用 `part` + 空间门禁 | ⚠️ 见 8.2-2 |
| B1/B3/B4 | `api.py` 增加 `_allowed_roots()` 动态并入 `storage.data_root()/datasets_dir()` | ✅ 且**保留了旧三目录**（正确）|
| B6 | `downloads._default_outdir` → `paths.datasets_dir()` | ✅ |
| 存储管理 API | `GET /api/storage`、`POST /api/storage/data-root`（需 `confirm`）、`POST /api/storage/cleanup`（默认 dry_run） | ✅ 与 §4.4/§6.4 一致 |

### 8.2 仍在途实现里**尚未解决**的缺口（建议 Lead 收口时逐条确认）

1. **kind 语义**：`publicdata.py` 用 `kind='conversion'`（`publicdata.py:891` 在途）。GEO 的 workdir 里主要放的是**下载原件**，建议 `kind='downloads'`；解压产物可再分 `extraction`。仅影响"清理时按类别统计"，不影响正确性。
2. **"已存在即跳过"回归风险**：在途 `download_file` 新增
   `if target.is_file() and target.stat().st_size > 0: return target`。
   HEAD 时代的半成品本来就与正式文件同名（`publicdata.py:125`），于是历史截断文件会被**静默当成完整文件**。必须配合 §6.2 的一次性校验，否则这是**数据正确性**回退。
3. **`results/` 未接线**：`paths.layout()` 提供了 `results/reports/cache/logs` 键，但 `queue.py:36 STORE`、`workspace.py:18 STORE`、`api.py:33 LOGS`、`registry.py:305` 仍是 `ROOT/...`。→ 用户切到 D 盘后，**分析结果、队列库、日志仍然全部写 C 盘**。这是 P3-02 最核心的用户可见收益，目前**未落地**（§7.1 已列为破坏性，需要用迁移方案，不能顺手改）。
4. **上传落点未接线**：`api.py:249 results/inputs` 与 `studio/pages/data.py:20 INCOMING` 仍写死。换根后上传仍落 C 盘。
5. **数据集发现未合并**：`workspace.py:290-299` 只扫 `PUBLIC`（`ROOT/data/public`）。换根后 `/api/downloads` 成功产出的文件**不会出现在数据集列表**——因为 `downloads._run` 从未调用 `workspace.register_dataset`（`downloads.py:210-271` 全函数无此调用）。
6. **无启动期孤儿 sweep**：`cleanup_staging` 只在手动 `POST /api/storage/cleanup` 时执行；`_load` 里 `interrupted` 任务仍不清理 staging（`downloads.py:74-76`）。
7. **`scan_staging` 会全树遍历**：`paths.py:297` 用 `base.rglob('*')` 然后逐个 `stat`，staging 里有 10 GB 时会明显卡界面；建议只遍历两层（`staging/<kind>/<task>`）。
8. **`.gitignore` 未跟进**：若数据根可能落在 `<repo>/datasets` 或新增 `staging/`，需追加（当前只有 `data/`、`results/`、`logs/`、`.cache/`、`tmp*/`）。
9. **重名风险**：`paths.datasets_dir()` 在"新根里恰好存在 `public/` 目录"时会误判为旧布局（`paths.py:190-192`）。建议改为"存在 `public/` **且** 存在登记表引用"的双条件，或在设置文件里记录 `legacy_layout: true`。

---

## 9. 未验证项（明确说明，不猜）

1. **大体积压缩包的真实解压峰值**：本报告用 `archive.py:11` 的 4 GiB 上限 + `memory.py:131` 的 ×5 经验系数推导，未用真实 4 GiB 压缩包实测。
2. **velocity.npz / analysis.h5ad / velovi_model / regvelo_model 的真实体积**：本仓库没有可复现的真实速度任务产物（`results/` 为空，仅 3 个元数据文件）。量级为按 `nnz × (8+4+4)` 的推导，**未实测**。
3. **Qt WebEngine 是否有落在 `%LOCALAPPDATA%` 的额外缓存**：`desktop_qt.py:79-82` 显式设置了 persistent storage / cache path 并禁用 HTTP 缓存，但 Chromium 的 GPU/shader 缓存路径未逐一验证。**未验证**。
4. **`%TEMP%` 里 "cellflow_geo_*" 历史残留**：本会话沙箱下 Python 无法在 `%TEMP%` 建目录（`gettempdir()` 回退到 cwd，见附录 B-3），因此**无法通过真实运行观察** `%TEMP%` 残留规模。代码层面已确认"强杀不清理"（D3）。
5. **uv/pip 在 `%TEMP%` 的构建临时目录体积**：仅从 repo 根存在的 `pip-build-tracker-*`、`pip-ephem-wheel-cache-*`、`pip-install-*` 目录推断曾发生，**未测具体体积**。
6. **`data/_geo_probe/geo_GSE72857.h5ad`（111 MB）的来源**：与本产品代码路径无关的独立探针产物（并发审计进程），仅作为"同一数据出现两份副本"的实证，未追溯其创建命令。
7. **切换数据根后 Windows 权限/ACL**（例如 D 盘为可移动盘或网络盘）：`paths.validate_root` 只测"能写一个探针文件"，未覆盖长期可用性（掉盘、休眠唤醒）。**未验证**。

---

## 附录 A：磁盘与体积实测

```text
$ Get-PSDrive C,D
Name FreeGB UsedGB
C       7.1  175.7
D      55.1    4.9

python -c "shutil.disk_usage(tempfile.gettempdir())"
temp free GB = 7.04  total GB = 182.75
TEMP = C:\Users\mtgmc\AppData\Local\Temp   (TMP 同值；TMPDIR 未设置)

data/                    212.2 MB  6 files
data/public              106.1 MB  2 files
data/public/geo_GSE72857__ed86d783.h5ad   111,205,145 B
data/_geo_probe/geo_GSE72857.h5ad         111,205,145 B   (重复副本)
results/                   0.0 MB  3 files  (queue.sqlite / projects.json / public_ignored.json)
logs/                      0.0 MB  2 files
examples/                  0.1 MB  3 files
tools/_hotfix/tmp          2.9 MB  175 files
.cache/ .runtimes/         MISSING
```

GEO 真实下载记录（`tools/_hotfix/http/downloads-get-10.json` / `-11.json`）：

```json
{"state":"running","done":16413252,"total":16413252,"message":"下载 GSE72857_umitab.txt.gz"}
{"state":"done","done":111205145,"total":111205145,
 "path":"C:\\...\\CellFlow-hotfix\\data\\public\\geo_GSE72857__ed86d783.h5ad"}
```

`data/public/.downloads/state.json` 中同一 GSE 有 **4 条全部 done** 的任务（同一 `path`），说明当前没有"同一数据集"级别的去重/复用。

## 附录 B：可复现命令

**B-1 抽取 HEAD 行号（不受工作树改动影响）**
```powershell
git show HEAD:cellflow/publicdata.py | Select-String -Pattern 'mkdtemp'
# 551:    workdir = Path(tempfile.mkdtemp(prefix=f'cellflow_geo_{gse}_'))
```

**B-2 确认 tempfile 落点与容量**
```powershell
.venv\Scripts\python.exe -c "import tempfile,shutil;print(tempfile.gettempdir());u=shutil.disk_usage(tempfile.gettempdir());print(u.free/1024**3)"
```

**B-3 本会话沙箱的一个已知偏差（写进报告是为了避免误读）**
```text
tempfile._candidate_tempdir_list() =
 ['C:\\Users\\mtgmc\\AppData\\Local\\Temp', ... , 'C:\\WINDOWS\\Temp', 'c:\\temp', 'c:\\tmp',
  '\\temp', '\\tmp', 'C:\\Users\\mtgmc\\...\\CellFlow-hotfix']
tempfile.gettempdir() = C:\Users\mtgmc\...\CellFlow-hotfix   ← 回退到 cwd
```
即：在本审计的环境里，Python 进程无法在 `%TEMP%` 建文件（`file sandbox` 影响），于是 `gettempdir()` 回退到最后候选 `os.getcwd()`（仓库根），这也是仓库根出现未跟踪目录 `tmpw4kzovnf` 的原因。
**这不是产品行为**：产品在正常桌面会话下 `%TEMP%` 可写，落点就是附录 A 的 `C:\Users\mtgmc\AppData\Local\Temp`（同为 C 盘，结论不变）。

**B-4 仓库体量**
```powershell
Get-ChildItem data,results,logs -Recurse -File | Measure-Object -Property Length -Sum
```

## 附录 C：并发改动快照（审计时刻）

```text
$ git log --oneline -1
f688fc7 docs: add 2026-10-26 public-data download hotfix report

$ git status --porcelain
 M .github/workflows/ci.yml
 M cellflow/api.py
 M cellflow/downloads.py
 M cellflow/importers.py
 M cellflow/publicdata.py
?? cellflow/paths.py
?? tests/test_geo_manifest.py
?? tests/test_paths.py
?? tmpw4kzovnf
?? tools/p3_probe/

$ git diff --stat
 .github/workflows/ci.yml |   2 +
 cellflow/api.py          | 148 ++++++++++++-
 cellflow/downloads.py    |   8 +-
 cellflow/importers.py    |   5 +-
 cellflow/publicdata.py   | 552 +++++++++++++++++++++++++++++++++++++++++++++--
```

`cellflow/paths.py` 在 HEAD 中**不存在**（`git show HEAD:cellflow/paths.py` 失败），是本轮 P3-02 的新模块（354 行）。
