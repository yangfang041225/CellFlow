# CellFlow 公开数据下载故障修复报告（Hotfix）

- 报告日期：2026-10-26
- 分支：`hotfix/public-data-download-connectivity`
- 提交：`1ab49e0c2739caf6cd73efc03fb575a69f588130`（基于 `main` @ `ecf0bc5`）
- 草稿 PR：https://github.com/yangfang041225/CellFlow/pull/4
- 范围：仅「数据 → 公开数据 → NCBI GEO → 下载」链路。未触碰品牌（Cytelle）、未启动 Phase III、未改科学模型与架构。

---

## 1. 摘要

用户在「数据 → 公开数据 → NCBI GEO → 输入 GSE 编号 → 下载」时，对 `GSE72857` 与 `GSE181411` 均失败，
界面报 `TypeError: Failed to fetch`。

本次工作的结论是：**真实 GEO/数据链路本身是好的**（见第 7 节，用真实数据跑通并逐字节校验）；
用户看到的是**本机 API 不可达/连接被中断**这一网络层故障，而 CellFlow 在用三个缺陷把「一次连接故障」
放大成了「一个都下载不了、而且看不出原因」：

1. 前端把浏览器原始异常 `TypeError: Failed to fetch` 原样拼进界面文案，无法判断是哪一层失败；
2. 下载状态轮询用 `setInterval` 且「任何一次异常就丢弃任务」，服务抖动一次就让仍在下载的任务从界面消失；
3. 后端 `downloads._run()` 把准备阶段放在异常处理之外，准备失败会留下永远停在 `queued` 的幽灵任务。

修复后：真实 GEO 下载经 HTTP 与真实 Qt 窗口两条路径验收通过；新增回归测试在**未修复代码上失败、修复后通过**。

---

## 2. 现象与工作边界

- 用户操作路径：数据 → 公开数据 → NCBI GEO → 输入 GSE → 下载。
- 已试编号：`GSE72857`、`GSE181411`。
- 用户原话：「一个都下载不了！」；错误字符串：`TypeError: Failed to fetch`。
- 缺少的原始证据：用户本机完整的 HTTP 状态、Network 记录、后端日志（现场已被后续构建覆盖）。

因此本次按「可复现—分层取证—再修复」执行，**不对缺少证据的环节做断言**。

---

## 3. 方法论

采用系统性调试（systematic debugging）的四个阶段：
先读错误与源码 → 稳定复现 → 在**每个组件边界**加证据 → 只改被证明的缺陷。

分层边界与取得证据的方式：

| 层 | 证据来源 | 结果 |
|---|---|---|
| React 前端 | 源码审查 + 11 项单元测试（node:test） | 缺陷确认（见 5.2） |
| 本机 HTTP API | 真实 `uvicorn` + `curl` 全链路 | 200 / 轮询 / done（见 7.1） |
| 后台下载线程 | 注入准备失败 + 并发用例 | 缺陷确认（见 5.3） |
| GEO / NCBI | 真实下载并校验 SHA-256 | 正常（见 7） |
| Qt WebEngine | 真实窗口驱动「数据→公开数据→下载」 | 正常，0 条 JS 错误（见 7.3） |

---

## 4. 环境核实（与交接材料的差异，必须先说清）

| 项目 | 核实结果 |
|---|---|
| 仓库 HEAD | `main` = `ecf0bc5`；参考提交 `a43ae4861adb8757d7473faccde642c2dbfd0276` 位于 `audit/phase2-review-20261026`，不在 `main` 上 |
| 用户本机工作副本 | `CellFlow-audit` **没有 `.git`**；与 `a43ae48` 比对 200 个受控文件中有 **162 个不同**，且缺 `benchmarks/`、`cellflow/evidence.py`、`cellflow/process_job.py` 等 |
| 运行日志 | `CellFlow-audit/logs/app.log` 显示 API 以 `--port 54386` 启动；而仓库三处硬编码 `8600`，且**不存在 `CELLFLOW_PORT` 环境变量**——说明当时跑的是一个已被覆盖的旧构建 |
| 本机 Python 环境 | 无任何解释器同时具备 fastapi + PySide6；`CellFlow-audit/.venv` 已不存在 |
| 结论 | **不能假定本机运行的是 PR #3 的最终提交**；本次修复以 GitHub `main` 为准，本机旧副本未做任何修改 |

---

## 5. 根因分析

### 5.1 为什么错误字符串是 `Failed to fetch`

`fetch()` 在以下情形**不会返回 Response**，而是直接抛 `TypeError`：连接被拒绝、连接被重置、请求被中止、
DNS/网络层失败、响应非法。它不是 GEO 的报错，也不是数据格式错误。

关键结构事实：**页面与 API 由同一个 FastAPI 进程提供**
（`api.py` 末尾 `app.mount('/', StaticFiles(directory=web/dist, html=True))`）。
于是当该进程不可用或连接被中断时：**已经渲染好的 Qt 窗口仍然存在**，界面看起来正常，
但之后每个 `/api/*` 请求都会以 `TypeError: Failed to fetch` 失败。

### 5.2 前端两个缺陷（把故障放大）

**缺陷 F1：错误未分类，直接把浏览器英文回显给用户**

修复前（`web/src/services/api.ts`）：

```ts
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  ...
}
```

任何 `fetch` 抛出的 `TypeError` 都不经处理直达调用方，
再被 `Data.tsx` 拼成 `无法开始下载：${exc}` 显示——这正是用户看到的字符串来源。

**缺陷 F2：轮询「一次失败就忘记任务」**

修复前（`web/src/views/Data.tsx`）：

```ts
} catch (exc) {
  if (downloadIdRef.current !== id) return;
  stopPolling();
  forgetDownload();      // 清掉持久化记录
  setDownload(null);     // 清掉界面状态
  notify('bad', `下载状态读取失败：${exc}`);
}
```

后果：本机服务抖动一次（或重启中的一瞬间），**一个仍在后台正常下载的任务就从界面消失**，
用户看到的是失败，实际文件还在写。这与「一个都下载不了」的体验直接对应。

### 5.3 后端缺陷：幽灵任务

修复前（`cellflow/downloads.py` `_run()`）：导入 `publicdata`、读取任务、`staging.mkdir()` 都在
`try/except` 之外。这些步骤一旦失败（目录不可写、磁盘满、依赖缺失），线程直接结束，
**任务永远停在 `queued`**：界面无限显示「排队中」，既不报错也不会结束。

该缺陷已用测试固定：在「临时目录不可创建」的场景下，旧代码停在 `queued`（测试断言 `'queued' != 'failed'` 失败），
新代码正确落到 `failed`。

### 5.4 明确未证实

用户当时那一次 `Failed to fetch` 的**具体触发方式**无法证实（现场日志已被旧构建覆盖）。
本次修复的是「本机服务不可达/连接中断」这一类**确定存在且被误报**的路径，并在界面层面把
「不可用」与「失败」区分开、保住任务记录。**没有**宣称找到用户那次的确切触发。

---

## 6. 修改清单

共 10 个文件，+920 / −99。

### 6.1 新增 `web/src/services/net.ts`（请求层分类）

- 新增 `requestJson()`：统一把失败分为 `transport`（本机服务不可达/连接中断）、`http`（保留状态码与后端
  `detail`）、`parse`（响应不是合法 JSON）。
- `AbortError` **原样抛出**，取消语义完全不变。
- 新增 `classifyFailure()` 返回 `missing / transient / fatal`，供轮询策略使用。
- 面向用户的提示改为可执行中文：「无法连接本机服务（127.0.0.1）。请确认 CellFlow 窗口仍开着；
  若刚重启过程序，请按 Ctrl+R 刷新本页。」

### 6.2 新增 `web/src/services/download-monitor.ts`（轮询状态机）

- 用 `setTimeout` 链替代 `setInterval`，避免请求叠加。
- 语义：单次失联 → 保留任务与进度并继续重试（最多 5 次，约 4 秒）；
  连续失联 → **只停止轮询、不丢弃记录**；任务 404 → 才 `gone`（允许清除）；业务错误 → 立即 `fatal` 停止。
- 与 React 解耦、失败分类由调用方注入，因此可被 `node:test` 直接测试。

### 6.3 `web/src/services/api.ts`

- `request` 改为 `requestJson`（对外 `api.*` 形状不变）。
- 新增幂等 `GET /api/downloads` 列表接口与 `DownloadTask` 类型，用于失败后对账。

### 6.4 `web/src/views/Data.tsx`

- 用状态机替换 `setInterval`；失联时界面保留进度并显示说明 + 「重新读取状态」按钮。
- **POST 传输层失败后不重发**（非幂等，重发可能建两个任务、下两遍），而是用 `GET /api/downloads`
  按 `source + identifier + 120 秒内创建` 对账：命中则接管该任务继续显示进度，未命中才提示失败。
- 恢复上次下载时，只有「任务确实不存在（404）」才清除记录，暂时失联保留。

### 6.5 `cellflow/downloads.py`

- `_run()` 的准备工作（导入依赖、读取任务、创建临时目录、安装钩子）全部移入异常处理；
  新增 `_fail()` 统一记录失败。
- `finally` 中恢复钩子、清理临时目录、强制持久化状态，保证异常路径也不会留下残留。

### 6.6 测试与工程配置

- 新增 `tests/test_download_hotfix.py`（幽灵任务、并发进度隔离、对账字段契约）。
- 新增 `web/tests/net.test.mjs` + `web/tsconfig.test.json` + `npm test` 脚本（11 项，**零新增依赖**）。
- `.gitignore` 忽略 `web/.tmp-test/` 与本地验收脚本目录。

### 6.7 未包含但需要你补一行

`.github/workflows/ci.yml` 的前端任务需要加 `- run: npm test`。
本次**未提交**该文件：当前 `gh` 令牌只有 `gist / read:org / repo`，GitHub 拒绝 OAuth App 创建或更新
workflow 文件（`refusing to allow an OAuth App to create or update workflow ... without workflow scope`）。
改动已留在工作区；执行 `gh auth refresh -s workflow` 后可以补推。

---

## 7. 验证

### 7.1 真实 GEO 端到端（HTTP 路径，新后端代码）

| 项目 | 结果 |
|---|---|
| GSE | `GSE72857` |
| 来源 | `https://ftp.ncbi.nlm.nih.gov/geo/series/GSE72nnn/GSE72857/suppl/` |
| 流程 | `POST /api/downloads` → 200 `{"ok":true,"id":"989f03b9ebfe"}` → 轮询 44 s → `state=done` |
| 产物 | `geo_GSE72857__ed86d783.h5ad`，**111,205,145 字节** |
| SHA-256 | `ed86d783b628b585251f26b6d8536845fcd9ec5174eb75840f8f8ce2dffed55c`（两次独立运行一致） |
| 数据规模 | **27,297 细胞 × 10,368 基因**，X 类型 counts |
| 数据集登记 | `id=377e18f35e48`，kind `AnnData`，106.1 MB，出现在 `/api/meta` 与界面列表 |
| 异常 | 无（`error=""`） |

### 7.2 真实 Qt WebEngine 验收

- 真实窗口加载 `http://127.0.0.1:8600/`：`load_finished=true`，标题「CellFlow · 单细胞动力学分析」，
  **控制台 0 条 JS 错误**。
- 驱动真实控件完成：点击「数据」→ 来源选「公开数据」→ 数据源「NCBI GEO」→ 输入 `GSE72857` → 点击「下载」。
- 服务端随即出现新任务并由 Qt 这次点击触发，最终 `done`（与 7.1 同一文件、同一 SHA-256）。
- 界面证据：进度条 +「共 1 个样本，开始合并 · 15.7/15.7 MB」+「取消下载」；右侧检查结果显示
  27,297 / 10,368；底部状态栏「本地服务已连接」。

### 7.3 自动化测试

前端：

| 命令 | 结果 |
|---|---|
| `npx tsc --noEmit` | exit 0（零错误） |
| `npm test`（`node --test`） | **tests 11 / pass 11 / fail 0 / skipped 0** |
| `vite build` | 成功，新代码已进入 bundle |

后端新增回归：**先证明能复现**

| 代码状态 | `tests.test_download_hotfix.PreparationFailure` |
|---|---|
| 旧代码（`git checkout` 还原后） | ❌ `AssertionError: 'queued' != 'failed'` |
| 修复后 | ✅ 通过（4 项中 3 通过、1 项因本机缺 `httpx2` 跳过） |

后端全量：`python -m unittest discover -s tests` → `Ran 241 tests, failures=2, errors=9, skipped=4`。
**这 11 项全部是环境原因，与本次修改无关**：

- 9 个错误：`RuntimeError: The starlette.testclient module requires the httpx2 package`
  （本机复制 venv 未装 `httpx2`；CI 有显式 `pip install httpx2`）。
- 2 个失败：`test_desktop.WindowsGuiLauncher` 的 `3 != 2`（PE subsystem）与重定向器字节比对——
  源于「复制 venv 的 `Scripts/*.exe` 与发行版不一致」，是复制环境的固有现象。
- 4 项跳过：veloVI 隔离环境缺失等。

其它工程检查：`tools/check_docs_links.py` → `DOC_LINKS_OK`；`compileall` → 0 错误。

> 说明：本机收集数（241）与历史报告（341）不可直接比较——本机 venv 为复制体、缺 veloVI 隔离环境；
> 本报告不把 `skipped` 当通过。

---

## 8. 影响与兼容性

| 维度 | 结论 |
|---|---|
| 用户科研数据 | 未删除、未移动；验收数据写在项目 `data/public`（已 gitignore） |
| 已有任务与报告 | 未改动队列、报告、结果格式 |
| Windows 桌面启动 | 未改 `cellflow_app.pyw` / `desktop_qt.py` 的启动与单实例逻辑 |
| 进程取消 | `AbortError` 原样抛出，取消/中断语义不变；下载取消仍走原路径 |
| 数据项目隔离 | `downloads.submit` 仍按 `active_project()` 记录，列表仍按项目过滤 |
| 其他科学模型 | 零改动（scanpy / scVelo / CellRank / veloVI / 报告） |
| 非幂等请求 | 失败后**不重发 POST**，改用幂等 GET 对账 |

---

## 9. 回滚

单提交，回滚成本极低：

```powershell
git revert 1ab49e0c2739caf6cd73efc03fb575a69f588130
```

或直接丢弃该分支（未合并进 `main`）。新增文件均为独立模块，删除后不影响既有页面。

---

## 10. 未验证项与已知边界

1. 用户当时那次 `Failed to fetch` 的**确切触发未证实**（现场日志被旧构建覆盖）。
2. `GSE181411` 未单独做在线验收；`CELLxGENE` 数据源未验证。
3. Qt 验收使用自建验收脚本 + `uvicorn`，**未走 `cellflow_app.pyw` 完整入口**：
   复制 venv 的 `pythonw.exe` 是 console 子系统（PE subsystem=3），正是 `install.py` 中
   `repair_gui_python()` 要修的情况。建议在真实安装的 `.venv` 下再点一次确认。
4. **`CELLFLOW_PORT` 不存在**：端口在 `cellflow_app.pyw`、`desktop_qt.LocalPage`、`api.py::__main__`
   三处硬编码 `8600`。属独立决策项，本次未改。
5. 「服务中途挂掉再恢复」的**双进程端到端注入**未做（前端已在单元测试与代码层覆盖）。
6. 本机复制 venv 缺 `httpx2`，9 个 API 测试本地未执行（CI 会跑）。
7. `web/dist` 未入库（gitignore），部署前必须构建。

---

## 11. 后续建议

1. **更新本机工作副本**（当前 `CellFlow-audit` 已陈旧且无 git 历史）：
   重新 clone → `git checkout hotfix/public-data-download-connectivity` → `python install.py` →
   `cd web; npm ci; npm run build`。
2. 补 CI 一行 `- run: npm test`（需 `gh auth refresh -s workflow`）。
3. 就「端口是否可配置（CELLFLOW_PORT）」做一次明确决策，并同步三处硬编码与 CORS 允许列表。
4. 若要进一步自证「服务失联可恢复」，可加一个本地代理层注入断连的集成测试（本次未做）。

---

## 附录 A：复现本次验证的命令

```powershell
# 0) 环境（本机为复制 venv 后补装依赖，源 venv 未改动）
cd CellFlow-hotfix
.venv\Scripts\python.exe -m uvicorn cellflow.api:app --host 127.0.0.1 --port 8600 --log-level warning

# 1) 真实 GEO 全链路
curl.exe -s -X POST http://127.0.0.1:8600/api/downloads -H "Content-Type: application/json" -d '{"source":"geo","identifier":"GSE72857"}'
curl.exe -s http://127.0.0.1:8600/api/downloads/<id>

# 2) 前端测试
cd web; npx tsc --noEmit; npm test; npm run build

# 3) 后端测试
cd ..; .venv\Scripts\python.exe -m unittest tests.test_download_hotfix -v
```

## 附录 B：提交内容

```
 .gitignore                           |   6 +-
 cellflow/downloads.py                | 114 +++++++++-------
 tests/test_download_hotfix.py        | 147 ++++++++++++++++++++
 web/package.json                     |   3 +-
 web/src/services/api.ts              |  34 ++---
 web/src/services/download-monitor.ts | 143 ++++++++++++++++++++
 web/src/services/net.ts              | 151 +++++++++++++++++++++
 web/src/views/Data.tsx               | 155 ++++++++++++++++-----
 web/tests/net.test.mjs               | 253 +++++++++++++++++++++++++++++++++++
 web/tsconfig.test.json               |  13 ++
 10 files changed, 920 insertions(+), 99 deletions(-)
```
