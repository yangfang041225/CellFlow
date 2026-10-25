# CellFlow 架构说明

更新时间：2026-10-25。本文描述**实际代码**的结构与数据流，不是规划稿。

## 1. 总体结构

```
CellFlow/
├─ app.py                  Streamlit 回退界面（迁移期保留）
├─ cellflow_app.pyw        Windows 桌面入口（单实例锁 → 启动 API → Qt 原生窗口）
├─ cellflow/
│  ├─ api.py               FastAPI：本机 HTTP 接口（127.0.0.1:8600）
│  ├─ desktop.py           单实例锁、窗口唤回、Python 解释器检查
│  ├─ pdf.py               报告 HTML → PDF（Qt 绘制，带页眉/页码，本地、无浏览器）
│  ├─ desktop_qt.py        PySide6 + QtWebEngine 原生窗口
│  ├─ queue.py             SQLite 任务队列：去重、PID 租约、孤儿恢复、取消
│  ├─ runner.py            单任务执行：准备 → 读取 → 校验 → 适配器 → 图 → 报告 → 质量校验
│  ├─ adapters.py          各模型适配器（scVelo / veloVI / CellRank / DPT / PAGA / …）
│  ├─ registry.py          方法注册表：输入需求、依赖、状态、准入
│  ├─ method_content.py    方法说明卡片（用途、假设、输出、限制）
│  ├─ data.py              Dataset 结构、读取、体检、QC、AnnData 转换
│  ├─ memory.py            读取前内存估算与超限拒绝
│  ├─ importers.py         h5ad / loom / 10x / CSV / zip 统一导入与方向判断
│  ├─ publicdata.py        GEO / CELLxGENE / URL 下载与标准化
│  ├─ downloads.py         后台下载任务：进度、取消、临时目录隔离
│  ├─ workspace.py         项目（projects.json）、数据集登记（按项目、加锁 + 原子写）
│  ├─ reports.py           A 生物学报告 / B 技术复现报告 + SVG 图
│  ├─ device.py            设备（CPU/CUDA）解析与说明
│  └─ compare.py           结果对比辅助
├─ web/                    React + TypeScript + Blueprint 6 + Dockview 8 前端
├─ tests/                  unittest（契约、流水线、导入、公开数据、桌面、API）
├─ docs/                   本目录：架构、开源调研、模型矩阵、数据流、测试、遗留
└─ results/                运行产物（任务目录、队列库、导入快照）
```

## 2. 分层与职责

| 层 | 位置 | 职责 | 不做什么 |
|---|---|---|---|
| 界面层 | `web/src`（主）、`app.py`（回退） | 渲染、交互、调用 API | 不判断科学条件 |
| 接口层 | `cellflow/api.py` | 参数校验、路径白名单、调用队列、读取产物；路由清单见 `docs/api_surface.md` | 不实现算法 |
| 调度层 | `cellflow/queue.py` | 入队、去重、PID 租约、取消、孤儿恢复 | 不解析数据 |
| 执行层 | `cellflow/runner.py` | 单任务生命周期、产物落盘、失败留档、质量校验 | 不直接实现模型 |
| 模型层 | `cellflow/adapters.py` | 调用上游官方库，转成统一结果结构 | 不自造算法 |
| 数据层 | `data.py` / `importers.py` / `publicdata.py` | 读取、体检、标准化、下载 | 不改原始文件 |

## 3. 关键数据流

```
数据文件 → importers/publicdata → Dataset（cells × genes）
        → data.inspect（格式体检）+ data.validate（校验）
        → registry.eligibility（方法准入，含隔离环境）
        → 界面展示“可运行 / 缺什么”
用户运行 → api.create_run → queue.submit（指纹去重）→ 后台 worker
        → runner.run（子进程，独立环境）
        → adapters.<method> → result.json / velocity.npz / figures/*.svg
        → reports.write_reports（A/B 报告）→ quality.json
        → queue 状态 completed / failed（报告缺失视为失败）
界面结果 → api.runs/{id} → 图形/指标/基因表/日志/质量
报告 → api.runs/{id}/report/{a|b} → Markdown / HTML / ZIP
```

## 4. 任务状态机

```
queued ──claim──> running ──ok──> completed
   │                 │
   │                 ├─异常/非0退出──> failed
   │                 └─用户取消──────> cancelled
   └─取消────────────> cancelled
running 且 PID 失联（重启后）──> failed（interrupted，日志保留）
```

- 任务记录：`results/queue.sqlite`（表 `jobs`，含 `fingerprint`、`pid`、`dataset`、`name`、`cancel_requested`、`project_id`）。
- 每个任务目录：`results/<任务ID>/`，含输入副本、配置、环境、源码副本、日志、图、报告、`quality.json`。
- 队列去重：同「输入内容哈希 + 方法 + 配置」的活动任务直接复用。

## 5. 桌面启动链

```
桌面快捷方式 → .venv\Scripts\pythonw.exe（GUI 子系统，无控制台）
             → cellflow_app.pyw（logs/desktop.lock 单实例）
             → 启动 .venv\Scripts\pythonw.exe -m uvicorn（CREATE_NO_WINDOW）
             → PySide6 + QtWebEngine 打开 http://127.0.0.1:8600/
关闭窗口 → finally 终止 API → 释放 8600
```

## 6. 安全边界

- API 只绑定 `127.0.0.1`，CORS 仅允许本机来源。
- 数据集路径白名单使用 `Path.is_relative_to`（防同前缀目录绕过）。
- 上传限制 1 GiB、扩展名白名单、解析失败不登记。
- 报告 Markdown 在前端经 DOMPurify 清洗；图片链接改写为受控接口。
- PDF 在独立子进程中生成（Qt 默认 Windows 插件，offscreen 无中文字体），失败不影响任务状态。
- 原始数据只读：导入时复制快照，不修改源文件。

## 7. 已知非目标

- 不做多用户/远程服务；不做云同步。
- 不做细胞类型注释、富集分析（GSEApy 已调研但未接入）。
- 不做模型排名；不同模型指标不可直接比较。
