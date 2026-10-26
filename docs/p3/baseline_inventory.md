# P3-00 基线审计：工程现状清单

- 生成时间：2026-10-26T08:00:56+08:00
- 生成脚本：tools/p3_baseline.py（可重复执行，见第 6 节）
- 仓库：C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix
- 当前分支：p3/batch1-data-acquisition；HEAD：0cfed72（feat(p3-01/02): GEO 单文件选择下载 + 数据根目录与临时文件治理）
- HEAD 提交时间：2026-10-26T07:28:15+08:00

> **快照口径**：本文件是采集时刻的快照。该工作副本与其它并行任务共享，采集期间 HEAD 会被并行任务推进（本次就发生过：审计开始时 HEAD=f688fc7，采集时已是 0cfed72），未提交改动也会变化。测试基线因此改为在 HEAD 的导出副本里跑，见 test_baseline.json 的 backend.notes。

## 1. 三个本地工作副本

| 副本 | 路径 | 有 .git | 受控源文件数 | vs HEAD：相同 / 内容不同 / 缺失 |
|---|---|---|---|---|
| CellFlow-hotfix | C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix | 是 | 176 | 165 / 3 / 0 |
| CellFlow | C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow | **否** | 63 | 29 / 27 / 112 |
| CellFlow-audit | C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-audit | **否** | 150 | 94 / 47 / 27 |

结论：只有 CellFlow-hotfix 是 git 工作副本；CellFlow 与 CellFlow-audit 是**没有 .git 的普通目录拷贝**，谈不上分支与 HEAD，只能按文件内容与提交比对。

### 1.1 源文件与仓库提交的差异规模

方法：对提交执行 git ls-tree -r 取 blob SHA；对副本内受控源文件按同一算法（sha1("blob <size>\0" + content)）计算；排除 .venv*、node_modules、data/、results/、logs/、web/dist、__pycache__、pip-*、tmp* 等非源码目录。

基准提交：
- main = ecf0bc58ec3a31ea59d1c6a379aad5caff0ffda1（155 个 blob）
- hotfix = f688fc70a6296575da390942d456c688c837f474（161 个 blob）
- audit = a43ae4861adb8757d7473faccde642c2dbfd0276（199 个 blob）
- HEAD = 0cfed72028331e80b862639c5fc07d6c5dee9831（168 个 blob）

| 副本 | 对比提交 | 提交内文件 | 相同 | 内容不同 | 副本缺失 | 副本多出 | 相同占比 |
|---|---|---|---|---|---|---|---|
| CellFlow-hotfix | HEAD | 168 | 165 | 3 | 0 | 8 | 98.2% |
| CellFlow-hotfix | main | 155 | 143 | 12 | 0 | 21 | 92.3% |
| CellFlow-hotfix | hotfix | 161 | 150 | 11 | 0 | 15 | 93.2% |
| CellFlow-hotfix | audit | 199 | 122 | 33 | 44 | 21 | 61.3% |
| CellFlow | HEAD | 168 | 29 | 27 | 112 | 7 | 17.3% |
| CellFlow | main | 155 | 29 | 27 | 99 | 7 | 18.7% |
| CellFlow | hotfix | 161 | 29 | 27 | 105 | 7 | 18.0% |
| CellFlow | audit | 199 | 29 | 27 | 143 | 7 | 14.6% |
| CellFlow-audit | HEAD | 168 | 94 | 47 | 27 | 9 | 56.0% |
| CellFlow-audit | main | 155 | 98 | 43 | 14 | 9 | 63.2% |
| CellFlow-audit | hotfix | 161 | 96 | 45 | 20 | 9 | 59.6% |
| CellFlow-audit | audit | 199 | 90 | 51 | 58 | 9 | 45.2% |

- CellFlow 相对 audit（a43ae48）：相同 29、内容不同 27、缺失 143、多出 7；多出样例：cellflow/ui_analysis.py、cellflow/ui_manual.py、cellflow/ui_models.py、cellflow/ui_results.py、cellflow/ui_theme.py
- CellFlow-audit 相对 audit（a43ae48）：相同 90、内容不同 51、缺失 58、多出 9；多出样例：DELIVERABLES.md、_baseline_check.py、_harness.py、_velovi_smoke.py、baseline_desktop_contracts.txt

## 2. 解释器与依赖环境

| 工具 | 可用 | 版本 | 路径 |
|---|---|---|---|
| python | 是 | Python 3.12.8 | F:\mamba\python.EXE |
| node | 是 | v26.4.0 | F:\mamba\node.EXE |
| npm | 是 | 11.17.0 | F:\mamba\npm.CMD |
| git | 是 | git version 2.54.0.windows.1 | F:\mamba\Library\bin\git.EXE |
| gh | 是 | gh version 2.95.0 | F:\mamba\Library\bin\gh.EXE |
| uv | 是 | uv 0.11.25 (x86_64-pc-windows-msvc) | F:\mamba\Library\bin\uv.EXE |

- 项目 venv：C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\.venv（存在=True，1.4 GB，26794 个文件，含 pip=False）
- venv 解释器：Python 3.11.15（sys.prefix = C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\.venv）
- pyvenv.cfg：home = C:\Users\mtgmc\Documents\Codex\2026-10-24\referenced-chatgpt-conversation-this-is-an\outputs\CellFlow\.runtimes\cpython-3.11-windows-x86_64-none；implementation = CPython；uv = 0.11.25；version_info = 3.11；include-system-site-packages = false
- 仓库外设置目录：C:\Users\mtgmc\AppData\Local\CellFlow（存在=True，0 个文件）。HEAD 版本的数据根在仓库内：cellflow/workspace.py 中 ROOT = Path(__file__).resolve().parents[1]；%LOCALAPPDATA%/CellFlow 目前为空。
- veloVI 隔离环境：本仓库没有 .venv-velovi（详见 dependency_inventory.json 的 isolated_envs.velovi）。

## 3. 数据目录位置与体积

| 副本 | 总大小 | 总文件数 | data | results | logs | web/dist | .venv | .venv-velovi | web/node_modules |
|---|---|---|---|---|---|---|---|---|---|
| CellFlow-hotfix | 1.9 GB | 78774 | 365.7 MB | 11.6 MB | 544.0 B | 3.8 MB | 1.4 GB | - | 131.4 MB |
| CellFlow | 3.0 GB | 58028 | - | 230.8 MB | - | - | 770.1 MB | 2.0 GB | - |
| CellFlow-audit | 235.5 MB | 8942 | 0.0 B | 28.4 KB | 448.0 B | 3.7 MB | - | - | - |

本仓库 data/ 与 results/ 中 ≥1 MB 的文件：
- C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\data\public\geo_GSE72857__ed86d783.h5ad — 106.1 MB
- C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\data\_geo_probe\geo_GSE72857.h5ad — 106.1 MB
- C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\data\public\geo_GSE72857_GSE72857_umitab__6e001a8f.h5ad — 106.1 MB
- C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\data\_probe\GSM8453300_alloxan.loom.gz — 30.4 MB
- C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\data\_probe\GSM7093919_kdm6bwt.loom.gz — 9.7 MB
- C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\data\_probe\GSE274591_velocyto_h2endoc.h5ad — 7.2 MB
- C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\results\c724e9fccd0e434c\figures\embedding.html — 4.6 MB
- C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix\results\ea6aeb53a6c043ca\figures\embedding.html — 4.6 MB

## 4. 采集时刻的工作区状态

- git status --porcelain 条目数：9（干净=否）
  - M .github/workflows/ci.yml
  - ?? docs/p3/baseline_inventory.md
  - ?? docs/p3/batch1-report.md
  - ?? docs/p3/branch_matrix.md
  - ?? docs/p3/data_safety_inventory.json
  - ?? docs/p3/dependency_inventory.json
  - ?? docs/p3/known_failures.md
  - ?? docs/p3/test_baseline.json
  - ?? tools/p3_baseline.py
- git stash list：无
- remote：origin	https://github.com/yangfang041225/CellFlow.git (fetch)；origin	https://github.com/yangfang041225/CellFlow.git (push)

未提交改动里的两类东西要分清：

1. **PR #4 遗留**：.github/workflows/ci.yml 增加 run: npm test —— 因 gh 令牌缺 workflow scope 无法推送（见 branch_matrix.md / known_failures.md）。
2. **并行任务的在途改动**（cellflow/api.py、cellflow/downloads.py、web/src/views/Data.tsx、新增 cellflow/paths.py、tests/test_geo_*.py 等），不属于本审计，也不应被本审计的结论覆盖。

## 5. 交付物本身

- 本目录 docs/p3/ 未被 .gitignore 覆盖（git check-ignore 结果见 data_safety_inventory.json），属于要提交的文档。
- 6 份文件全部由 tools/p3_baseline.py 生成，重复执行会覆盖。

## 6. 复现命令

    cd <repo>
    .venv\Scripts\python.exe tools\p3_baseline.py

可选：--skip-tests 只采静态证据；--compare-httpx2 额外采「未装/已装 httpx2」两组后端测试；--run-mode worktree 在当前工作区跑测试（会被并行改动污染）。
