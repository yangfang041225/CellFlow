# P3-00 基线审计：已知故障清单

- 生成时间：2026-10-26T08:00:56+08:00
- 依据：docs/hotfix-2026-10-26-public-data-download.md 第 10 节、tools/_hotfix/ENV_REPORT.md、本次实测（见 test_baseline.json）。
- 口径：**已修**＝有提交且被测试覆盖；**未修**＝确认存在未改；**未验证**＝没有证据，不下结论。

## 1. 本次 hotfix 已修（f688fc7 树上）

| 编号 | 现象 | 状态 | 位置 | 证据 |
|---|---|---|---|---|
| F1 | 前端不区分失败层级：TypeError: Failed to fetch 被原样拼进界面 | 已修 | web/src/services/net.ts（requestJson / classifyFailure） | web/tests/net.test.mjs 覆盖失败分类 3 项 |
| F2 | 轮询一次异常就丢任务：setInterval + 任意异常即 forgetDownload() | 已修 | web/src/services/download-monitor.ts（轮询状态机） | net.test.mjs：单次失联不丢 / 连续失联只停轮询 / 404 才清除 |
| F3 | 后端幽灵任务：_run() 准备工作在 try 之外，失败后永远停在 queued | 已修 | cellflow/downloads.py（准备阶段移入异常处理 + _fail()） | tests/test_download_hotfix.py（旧代码上 'queued' != 'failed'） |
| F4 | 非幂等 POST 在传输层失败后被重发，可能建两个下载任务 | 已修 | web/src/views/Data.tsx（改用幂等 GET /api/downloads 对账） | net.test.mjs 对账用例 |

## 2. 仍未修（本次确认）

| 编号 | 问题 | 位置 | 复现 / 证据 |
|---|---|---|---|
| NF1 | CELLFLOW_PORT 不存在，端口在 3 处硬编码 8600 | cellflow_app.pyw、cellflow/desktop_qt.py（LocalPage）、cellflow/api.py::__main__ | git grep -n 8600 可复现；hotfix 报告第 10 节第 4 条 |
| NF2 | .github/workflows/ci.yml 的 npm test 步骤未提交 | .github/workflows/ci.yml（工作区未提交改动） | gh 令牌只有 gist/read:org/repo，推送 workflow 文件被 GitHub 拒绝 → 前端回归在 CI 里实际未启用 |
| NF3 | 复制 venv 的 Scripts/*.exe 与发行版不一致（PE subsystem=3） | .venv/Scripts/pythonw.exe 等 | tests/test_desktop.py 2 项失败：3 != 2、重定向器字节比对不一致；install.py::repair_gui_python() 存在就是为了修这个 |
| NF4 | veloVI 隔离环境在本仓库缺失 | .venv-velovi（不存在） | 相关测试 skipped；见 dependency_inventory.json.isolated_envs.velovi |
| NF5 | web/dist 未入库（gitignore），部署/安装前必须构建 | web/dist、.gitignore | git check-ignore -v web/dist 命中 web/dist/；install.py --check 依赖 web/dist/index.html |
| NF6 | 远端 phase3/cytelle-rebrand-and-validation 指向 audit 提交 | 远端分支 | 见 branch_matrix.md 第 1、3 节（merge-base --is-ancestor 与 ls-remote 证据） |

## 3. hotfix 报告「未验证项」逐条落账

| 项目 | 状态 | 说明 |
|---|---|---|
| 用户那次 Failed to fetch 的确切触发方式 | 未验证 | 现场日志被旧构建覆盖；本次只修了“本机服务不可达/连接中断”这一类路径 |
| GSE181411 在线验收 | 未验证 | hotfix 只端到端验了 GSE72857 |
| CELLxGENE 数据源 | 未验证 | 本次审计未跑该源 |
| Qt 验收是否走 cellflow_app.pyw 完整入口 | 未验证 | 当时用自建脚本 + uvicorn；建议真实安装的 .venv 下再点一次 |
| “服务中途挂掉再恢复”双进程端到端 | 未验证 | 前端只有单元测试覆盖 |
| 复制 venv 缺 httpx2 导致 9 个 API 测试未执行 | 已实测 | 见 test_baseline.json：安装前后的对照数字 |
| web/dist 未入库 | 确认 | 部署前必须 npm run build |

## 4. 本次审计新发现的工程约束/故障

| 编号 | 问题 | 位置 | 复现 / 证据 |
|---|---|---|---|
| NF7 | Python 子进程只能写工作区内部，写 %TEMP% 报 PermissionError | 本机执行环境（DSH 文件沙箱） | 复现：.venv\Scripts\python.exe -c "open(r'%TEMP%\\x.json','w')" → PermissionError: [Errno 13]；写仓库内目录正常。副作用：tempfile.gettempdir() 回落到 cwd，任何把中间产物写进 %TEMP% 的脚本在本机都会失败 |
| NF8 | 并行任务共享同一工作副本，HEAD 与 git status 随时在变 | 仓库工作区 | 审计开始时 HEAD=f688fc7，采集期间被并行任务推进到 0cfed72；未提交改动也从 1 项涨到 8+ 项。因此本审计把后端测试改到 HEAD 导出副本里跑（不受工作区改动影响） |
| NF9 | pip install httpx2 首次长时间无输出（两次并发 pip 同时挂起） | 本机 venv | 改用 --no-cache-dir --timeout 10 --retries 0 -v 后 30 秒内成功；CI 里是 pip install "pypdf==6.21.2" "httpx2" |
| NF10 | requirements.txt 声明的包并非全部已安装 | venv | 见 dependency_inventory.json 的 python.requirement_check.missing；属环境差异，不是产品缺陷，但会让本地验收与 CI 结论不一致 |
| NF11 | 测试套件隐式依赖 data/、results/、logs/ 三个目录已存在（全新 clone 里没有） | tests/test_pipeline.py 的 2 个 auto 用例 / cellflow 运行时目录 | 实测：HEAD 的干净导出（无这三个 gitignore 目录）跑 tests.test_pipeline → 2 个 auto 用例失败（'fate' not found / KeyError: 'fate'）；补建空的 data/results/logs 后同一代码 11 个用例通过（1 skipped）。具体被吞掉的异常未定位（未验证）——但这意味着 fresh clone 直接跑 unittest 会红 |

## 5. 与测试基线的对应

- before_httpx2_blocked：collected=283，passed=269，failed=2，errors=9，skipped=3
- after_httpx2：collected=325，passed=321，failed=2，errors=0，skipped=2
- 前端 tsc --noEmit：exit=0（通过）
- 前端 npm test：exit=0（通过）

逐条失败/跳过的分类见 test_baseline.json 的 failure_analysis。
