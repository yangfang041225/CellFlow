# CellFlow 工程交付与审查报告

> 用途：交给 GPT 审阅、再交给 Codex 挑错。
> 日期：2026-10-25　｜　版本：0.3.0　｜　仓库：<https://github.com/yangfang041225/CellFlow>
> 报告生成者：本轮开发代理（DeepSeek Harness）。本报告只陈述实际执行过、能复现或能定位证据的内容。

---

## 0. 一页摘要

CellFlow 是一个**本机运行、面向非程序员研究者的单细胞动力学分析工作台**：

- 前端 React + TypeScript + Blueprint + Dockview；后端 FastAPI；桌面壳 PySide6 + Qt WebEngine
- 计算核心 Scanpy / scVelo / CellRank；veloVI 在隔离环境；无 GPU 依赖
- 真实任务队列（SQLite 子进程）、报告（A/B、HTML/PDF/ZIP）、质量校验、项目隔离
- 当前本机可运行 11 个方法；RegVelo 实验性；5 个深度模型未接入

本轮工作分两大段：

1. **从零到可用**：把原先的 Streamlit 原型迁到 React + Qt 桌面，补齐队列、报告、数据导入、内存保护、项目管理、错误处理、测试。
2. **按第三方审计项目书整改**：逐项落实 F01–F40，修掉 P0/P1 缺陷，并把无法验证的能力明确标为实验性/边界。

当前证据：

- 本地自动化测试 **282 项全部通过**（约 211 s）
- GitHub Actions 前后端**均成功**（提交 `965ba87`）
- Qt 窗口实测：启动、窗口内提交任务、关闭释放 8600
- 审计 40 项中：**32 项已修**、**7 项部分**、**1 项实验性**
- 仓库 25 个提交；约 10.4k 行 Python、4.3k 行测试、13 个前端源文件、20 份文档

**这不是“V1.0 已可交给任何科研人员独立使用”的声明**。真实生物基准、多屏 DPI 矩阵、浏览器 E2E、RegVelo 训练仍未完成，详见 §8。

---

## 1. 用户需求与硬约束（原始意图）

按时间顺序，用户提出的核心要求：

1. 给**不会编程的科研用户**做单细胞分析软件，中文界面。
2. **不要浏览器**，要真正的桌面软件；**不要黑色控制台窗口**。
3. 前端必须基于成熟开源项目改造，**禁止从零造轮子**。
4. 数据不能上传云端；本机 16 GB、无 NVIDIA，**不做 GPU 依赖**。
5. **必须做好内存管理**，不能把机器拖死。
6. **不要动私人文件和系统文件**。
7. 按一份 40 项的《全面审查与修改项目书》整改，完成后再交付。
8. 代码要放到 GitHub 规范管理（公开、个人账号、MIT）。
9. 允许使用子智能体并行，但要能收敛、别反复失败。

已遵守的边界：

- 只修改项目目录内文件：`cellflow/`、`web/src/`、`tests/`、`docs/`、入口脚本、`results/`、`data/public/`、`logs/`
- 未删除/移动用户的私人文件或系统文件
- 未安装 GPU 依赖，未上传任何数据
- 下载/测试产生的临时数据已清理（`data/public` 仅保留用户已有的 `geo_GSE84133.h5ad`）

---

## 2. 最终架构

```
启动快捷方式 / pythonw cellflow_app.pyw
  └─ cellflow/desktop.py     单实例锁、启动 API、健康检查
      ├─ uvicorn (127.0.0.1:8600)  cellflow/api.py
      └─ cellflow/desktop_qt.py  Qt 原生窗口（QWebEngineView）
            └─ React 前端（web/dist，由 Vite 构建）

数据与任务
  cellflow/importers.py     h5ad / loom / 10x / CSV / zip / tar
  cellflow/archive.py       安全解压（F01）
  cellflow/data.py          读取、格式体检、预处理声明（F19）
  cellflow/memory.py        峰值估算与加载前预检（F24）
  cellflow/workspace.py     项目、数据集登记、公共数据忽略列表
  cellflow/queue.py         SQLite 队列：去重、并发、心跳、取消进程树
  cellflow/runner.py        单个任务生命周期（准备→计算→质量→报告）
  cellflow/adapters.py      Scanpy / scVelo / CellRank / veloVI / RegVelo 适配
  cellflow/reports.py       FigureSpec、A/B 报告、HTML 安全、ZIP 清单
  cellflow/pdf.py           Qt 文档 → PDF（页眉/页码/字号）
```

关键设计选择：

- **服务只绑定 127.0.0.1**，无远程暴露
- 每个任务独立子进程 CLI（`python -m cellflow.cli run`），队列只做调度与状态
- 结果目录 `results/<job_id>/` 自带 config、environment、provenance、日志、图、报告
- 报告与图共用 `FigureSpec`，避免“预览/报告/导出三套”语义漂移
- 隔离环境（veloVI）用独立解释器，运行时探测模块可用性

---

## 3. 交付物

### 入口

- `cellflow_app.pyw`：桌面入口（无控制台）
- `install.py`：安装与健康检查（`--check`）
- `web/`：React 前端源码；`web/dist` 构建产物不进仓库

### 主要文档

| 文件 | 内容 |
|---|---|
| `README.md` | 安装、启动、构建、历史入口边界 |
| `docs/audit_response.md` | **F01–F40 逐项状态与证据** |
| `docs/final_status.md` | 交付状态与剩余边界 |
| `docs/acceptance.md` | 验收对照 |
| `docs/test_report.md` | 测试记录、CI 历史、审计新增测试 |
| `验证记录.md` | 按轮变更流水（含失败与修复） |
| `docs/architecture.md` / `data_pipeline.md` | 架构与流水线 |
| `docs/model_support_matrix.md` | 方法 × 本机可用性 |
| `docs/local_capability.md` | 本机能力（GPU 模型明确搁置） |
| `docs/install_manifest.md` | 环境 manifest 与复现/回滚 |
| `docs/术语表.md` | 45 个术语的参考翻译（精确匹配） |
| `THIRD_PARTY_NOTICES` | 第三方许可 |

### 统计

- 25 个提交（从 `1ca203b` 初始提交到 `965ba87`）
- `cellflow/`：35 个 Python 文件，约 10,353 行
- `tests/`：32 个测试文件，约 4,291 行
- `web/src`：13 个 TS/TSX 文件
- `docs/`：20 份 Markdown

---

## 4. 工作分解（做了什么）

### 4.1 桌面化与前端

- 从 Streamlit 原型迁到 **FastAPI + React + Dockview + Blueprint**
- 用 **PySide6 + Qt WebEngine** 做原生窗口，去掉浏览器地址栏
- 解决 `pythonw.exe` GUI 子系统问题，消除黑色控制台
- 单实例锁；关闭窗口 → 停止 API → 释放 8600
- 窗口按屏幕可用区域收敛；980×620 窄屏单栏、工具栏收纳
- 前端：项目切换、分页、搜索、任务取消/重命名/删除、数据集详情、对比、报告预览、环境诊断、状态栏

### 4.2 任务系统

- SQLite 队列；每次任务独立子进程；阶段写入 `provenance.json`
- **去重键含 project_id**（修复跨项目复用）
- **进程树取消**：记录计算 PID + 创建时间，`taskkill /T`，确认退出才写 cancelled
- **全局并发上限**（默认 1）、**心跳/租约**、启动恢复、失败终态
- 准备阶段（配置/拷贝/环境/哈希）纳入保护块，失败不留永久 running

### 4.3 数据与安全

- 安全解压：拒绝父目录/盘符/UNC/绝对路径/链接/特殊文件，限制文件数/字节/层级
- 上传流式落盘 + 边写边限流，不整文件读入内存
- AnnData 信任 obs/var 轴，不再按形状猜转置
- CSV 缺失/重复值计数写入 notes 与 extras；元数据空值保持空，不变成 “None” 类
- 缺失分组列直接拒绝，不制造 “None” 群
- 数据集保留原始文件名、来源、复合扩展名（`.csv.gz`/`.tar.gz`）
- 数据集移除按项目作用域；公共文件移除后不会因目录扫描复活
- 大文件哈希明确区分 `sha256` 与 `sha256-partial-headtail-size`
- 预览统计修正：sparse 用 `nnz/(n×g)` 与整体均值，dense 用 `issparse` 判断
- 内存预检计入 HDF5 的 `/layers`、`/raw/X`、10x `/matrix`

### 4.4 科研计算

- 11 个方法真实可运行：auto、QC/PCA、demo、Leiden、DPT、PAGA、scVelo deterministic/stochastic/dynamical、CellRank、veloVI（隔离环境）
- **veloVI 协议修正**：按 scvi-tools 1.2.0，先校验 S/U 为原始计数 → normalize/log1p/moments → 用 `Ms/Mu` 训练；写 `velovi_protocol.json`
- **预处理声明**：声明/观察/有效结论/冲突/步骤；auto 与独立 scVelo 共用同一函数
- **质量门禁**：`quality.ok=False` 直接 failed，不写 completed
- **CellRank 数值回退**：GPCCA 求解器出现负概率时，用稠密吸收方程 `(I−Q)x=R`，裁剪极小负值并归一化
- **比较契约**：只有输入/基因/预处理/参数/实际嵌入坐标全部一致才允许同坐标比较；指标不排名
- 报告四层解释（观察/派生统计/计算推断/假说）由实际执行步骤驱动；未执行速度不再宣称 scVelo

### 4.5 图形与报告

- 点与速度向量共用同一仿射变换；默认等比例，`stretch` 可复现旧坐标系
- `FigureSpec` 统一标题/色阶/字段/坐标说明；PAGA 保持对称与 0–1 色阶
- 主图优先、诊断图折叠；图注写明“只显示方向/不能推出什么”
- PDF：设备像素 + 96 dpi 排版，A4，正文中位约 10.5 pt，页眉页码
- HTML：数据转义 + 白名单清洗 + CSP，恶意标签只显示为文本
- ZIP：区分报告包/复现包；枚举实际被引用的 PNG/SVG（含 `stream.png`）；附文件哈希与 `REPRODUCE.md`；full 模式含 ≤200 MB 输入与模型目录

### 4.6 工程与运维

- 282 项测试；夹具自动生成任务，不依赖历史结果
- `install.py --check` 真实 import + 版本探测；隔离环境用自身解释器探测
- `registry.isolated_ready` 运行时 `find_spec` 探测，6 小时缓存
- 未捕获异常写 `logs/api-errors.log` 并返回中文 JSON
- GitHub Actions：前端 `npm ci + tsc + build`；后端真实安装、`install.py --check`、文档链接、全量测试
- MIT License、`.gitignore` 排除数据/结果/环境/密钥

---

## 5. 审计项目书 F01–F40 落实情况

完整对照见 `docs/audit_response.md`。摘要：

| 分组 | 状态 |
|---|---|
| F01 解压安全（P0） | 已修 |
| F02–F05 任务/项目/去重 | 已修 |
| F06–F10 前端状态与数据契约 | 已修 |
| F11 veloVI 协议 | 已修（真实 smoke，2 epochs，仅接口） |
| F12–F18 图形/报告/PDF/ZIP | 已修 |
| F19 预处理判定 | 已修 |
| F20 RegVelo | **实验性**（本机无环境，未训练） |
| F21 比较契约 | 已修 |
| F22–F23 恢复/准备失败 | 已修 |
| F24 内存/磁盘 | **部分**（已计入 layers/raw；磁盘余量仍估算） |
| F25–F30 完整性格/预览/下载/移除/参数/命名 | 已修 |
| F31 异步状态 | 已修 |
| F32 测试流程 | **部分**（无浏览器 E2E/覆盖率；CI 可选环境跳过） |
| F33 规模 | 已修（分页/尾读/详情压缩） |
| F34 环境发布 | **部分**（运行时探测已加；manifest 非唯一来源；无签名安装器） |
| F35 窗口/DPI | **部分**（1180/980 实测；多屏缩放矩阵未系统测） |
| F36 多入口文档 | 已修 |
| F37 复现包 | **部分**（≤200 MB 输入/模型可打包；超大输入只记哈希） |
| F38 方法说明 | 已修 |
| F39 全图专业化 | **部分**（语义/配色/图注统一；完整论文渲染器未做） |
| F40 可维护性 | **部分**（重复定义/BOM 清理；adapters 仍大） |

统计：**32 已修 / 7 部分 / 1 实验性**。

---

## 6. 验证证据

### 6.1 自动化

```
.venv\Scripts\python.exe -m unittest discover -s tests
Ran 282 tests in 211.334s
OK
```

- 覆盖：解压安全、队列生命周期、数据契约、科学契约、图形语义、HTML 安全、比较、分页、下载、安装检查
- CI：本地 282；GitHub Actions 中 veloVI/loom 等可选环境会跳过（2 项）

### 6.2 GitHub Actions

- 成功提交：`aad10ec`、`65cbd2e`、`7b6ef01`、`965ba87`
- 最新：<https://github.com/yangfang041225/CellFlow/actions/runs/40203361045>
- 历史失败已定位修复：缺 `httpx2`、Windows cp1252 编码、CellRank 负概率、历史任务夹具

### 6.3 桌面实测

- 真实 Qt 窗口 1180×780，Windows 125% 缩放，`console_hwnd=0`
- 窗口内点击提交任务 `110af20278fb4103` → completed → 结果页显示图/指标/日志
- 关闭窗口：`WINDOW_CLOSED` → `API 已停止` → 8600 释放
- 980×620 无横向溢出（CDP 实测 `scrollWidth == innerWidth`）
- 截图：`docs/screenshots/qt-page.png`、`qt-results.png`、`qt-model-new.png`、`qt-model-980.png`

### 6.4 真实 HTTP 验收

- 任务 `9951ad091fd74f61`：completed，7 图（含 `stream.png`），quality 17 项全过，PDF 755 KB，ZIP 795 KB，含 manifest 与 A/B PDF

### 6.5 环境

- Python 3.11.15；scanpy 1.11.5、scvelo 0.3.4、cellrank 2.0.7、anndata 0.12.19
- 隔离 veloVI：scvi-tools 1.2.0、torch 2.5.1+cpu
- 无 NVIDIA GPU；未安装 CUDA 依赖

---

## 7. 修复过的真实缺陷（代表性）

按“根因 → 修复”列，便于审查者判断深度：

1. **点击快捷方式无窗口**：多个残留启动器 + 单实例锁；清理并改成单实例健康检查
2. **黑色控制台**：`.venv` 的 `pythonw.exe` 非 GUI 子系统；恢复官方 redirector，并在启动时自检/修复
3. **WebView2 空白**：切到 PySide6 Qt 窗口
4. **HTML 报告显示原始 Markdown**：后端改 Python-Markdown 渲染
5. **取消后子进程仍活**：只杀 supervisor；改为记录计算 PID/创建时间 + 进程树终止，确认退出才置终态
6. **跨项目复用任务**：去重键不含 project；改为 project_id + 事务 + 唯一索引
7. **删除 B 项目数据导致 A 降级**：workspace 里重复的 `unregister_dataset` 覆盖了正确实现；删除重复，改为字典保留 + 项目作用域
8. **移除公共数据又复活**：目录扫描无条件重新加入；加忽略列表 + 原子写
9. **恶意压缩包越界写**：`extractall`/tar 字符串检查不足；新增安全解压器
10. **上传读满内存**：先 `read()` 再判大小；改流式 + 边写边限流
11. **预览统计错**：稀疏矩阵按 `data` 数组统计；改为 `nnz/(n×g)`、`issparse`
12. **质控失败仍 completed**：质量门禁没阻断；改为 failed
13. **箭头方向失真**：点与向量用了不同缩放；统一仿射
14. **PAGA 视觉不对称**：逐行归一化；改原始对称矩阵 + 0–1 色阶
15. **PDF 字号 0.72 pt**：HighResolution 设备与 Point 单位混用；统一单位与缩放
16. **ZIP 缺 `stream.png`**：只 glob SVG；改为按引用枚举
17. **HTML 报告 XSS**：未转义/未清洗；数据转义 + 白名单 + CSP
18. **veloVI 把 raw counts 当 normalized**：按 1.2.0 官方协议改 Ms/Mu，并在训练前校验
19. **auto 无速度却宣称 scVelo**：报告叙述不依据实际步骤；改为 `executed_steps`
20. **比较允许不可比图**：只比 cells hash；改为完整身份 + 嵌入哈希
21. **CI 全红**：httpx2/cp1252/CellRank 负概率/历史夹具，逐项修复
22. **CellRank 负概率**：GPCCA 求解器在部分 scipy/BLAS 组合下失败；直接吸收求解 + 裁剪极小负值

---

## 8. 已知限制与风险（请重点审查）

### 8.1 科学可信度

1. **没有真实生物基准**：所有端到端验证都是 240×120 模拟数据；不能证明谱系方向、终末状态、参数稳健性。
2. **veloVI 只跑 2 epochs**：只验证接口/输入协议，**不声称收敛**。
3. **RegVelo 未训练**：本机无环境，保持 experimental；不能宣称可用。
4. **CellRank 回退的数值处理**：直接吸收求解后把极小负值裁剪为 0 并归一化——这是数值修复，不等于模型正确；需审查者判断是否应在报告中显式标注（目前只写日志）。
5. **单终末状态**：命运概率接近 1 是结构结果，不代表区分能力；报告已提示，但审查者应确认措辞。
6. **预处理判定**：仍以声明 + 观察结合；TPM/低表达归一化/log 的边界无法仅凭数值完全区分。

### 8.2 工程风险

1. **取消会连同 supervisor 一起结束**：该 supervisor 名下的其他排队任务要等下一次提交/启动才继续；未做“取消后立即重启 worker”。
2. **快照指纹与源文件**：指纹取自提交时源文件，复制在其后微秒级完成；极端并发修改源文件未被测试。
3. **`results/` 是本地状态**：换机器/删目录会丢历史；没有迁移工具。
4. **HTML 报告的历史文件**：旧任务不会自动重写；重新运行才生效。
5. **PDF 中文文本提取**：视觉正常，但 pypdf 提取 CJK 为乱码（Qt 未写 ToUnicode）；影响复制/搜索。
6. **>200 MB 输入**：复现包只记哈希，不打包；离线复跑需用户自行放置。
7. **DPI**：1180/980 已实测；125/150/200% 多屏原生矩阵未系统测。
8. **浏览器 E2E**：没有自动化；前端行为只做了 tsc/build + CDP 手动脚本。
9. **adapters.py 仍大**：约 1.5k 行；未完成 Method/Executor 协议重构。
10. **测试跳过**：CI 中 veloVI（隔离环境）与 loom 相关会跳过；本地因环境齐全而全过。

### 8.3 安全

- 解压/上传已做边界检查；但 HTML 报告的 CSP 允许 `file:` 图片（为离线显示），审查者应确认这是否可接受。
- 服务仅 127.0.0.1；无鉴权（单机单用户假设）。
- 用户曾把 GitHub PAT 发到聊天中，已建议吊销；仓库推送改用 `gh` 凭据。

---

## 9. 请 GPT / Codex 重点检查的问题

1. **结论是否过度**：`docs/audit_response.md` 把 32 项标“已修”，是否有证据不足却标已修的？
2. **CellRank 回退**：裁剪负值 + 归一化是否会掩盖真实错误？是否应改为失败并提示用户？
3. **veloVI 协议**：`Ms/Mu`、log1p 只作用于 X、2 epochs 的 smoke 是否足以称为“已修”？
4. **安全**：安全解压是否覆盖 Windows ADS、长路径、大小写、junction？CSP 允许 `file:` 是否过宽？
5. **数据契约**：缺失值填 0 仍会改变语义；当前只记录计数，是否应默认拒绝？
6. **队列**：`taskkill /T` 是否覆盖 Job Object 语义？PID 复用校验是否足够？supervisor 被杀后排队任务恢复是否可靠？
7. **项目/数据集**：公共文件“全局可见 + 移除即全局忽略”是否符合预期？是否需要显式公共库与项目关联两层模型？
8. **报告**：四层解释是否真的逐条绑定证据？模板语言是否仍有未执行步骤的暗示？
9. **测试**：282 项是否足以支撑“可交付”？哪些关键路径没有测试（前端 E2E、真实网络下载、长任务、OOM）？
10. **架构**：FastAPI + Qt 的单机方案是否会在多用户/远程/GPU 场景失效？当前是否值得重构 adapters？
11. **文档一致性**：README/验收/测试报告/审计对照是否存在互相矛盾的计数或状态？
12. **可复现性**：换机器后能否只凭仓库 + 文档恢复？`installation.json`、`results/`、`web/dist` 的缺失是否被清楚说明？

---

## 10. 复现命令

```powershell
$Root = "C:\Users\mtgmc\Documents\Codex\2026-10-24\referenced-chatgpt-conversation-this-is-an\outputs\CellFlow"
cd $Root

# 环境
python install.py --check
.venv\Scripts\python.exe -m unittest discover -s tests

# 前端
cd web
npm ci
npx tsc --noEmit
npm run build
cd ..

# 启动（桌面）
.venv\Scripts\pythonw.exe cellflow_app.pyw

# CLI 单任务
.venv\Scripts\python.exe -m cellflow.cli run --input examples\synthetic.npz --config .tmp\example-config.json --output .tmp\run

# 文档/重复定义检查
.venv\Scripts\python.exe tools\check_docs_links.py
.venv\Scripts\python.exe tools\check_duplicates.py
```

`examples/synthetic.npz` 是**明确标注的模拟数据**（240 细胞 × 120 虚构基因），只用于流程验证，不是生物学发现。

---

## 11. 提交与索引

- 仓库：<https://github.com/yangfang041225/CellFlow>（Public，MIT）
- 最新提交：`965ba87`（registry 环境探测）
- 关键提交：
  - `1ca203b` 初始提交
  - `46346d4` 图形/报告/PDF/ZIP 整改
  - `29d8a8e` 队列进程树/并发/去重/心跳
  - `ca16413` 安全解压/项目登记/上传/质量门禁
  - `93d951e` 前端作用域/声明/预检/请求取消
  - `bad3f3d` 比较契约/分页/下载持久化/窗口
  - `08a3680` HTML 安全
  - `7b6ef01` CellRank 吸收回退
  - `965ba87` 隔离环境运行时探测
- 未提交内容：无（工作区干净）

---

## 12. 一句话给审查者

这是一套**在本机真实跑通、带 282 项测试、按 40 项审计整改过**的单细胞工作台；它的强项是数据边界、任务生命周期、报告语义与桌面可用性；弱项是**真实生物验证、极端规模、多环境 DPI/E2E**。请优先质疑“已修”的证据强度与科学结论的措辞，而不是只看测试是否全绿。
