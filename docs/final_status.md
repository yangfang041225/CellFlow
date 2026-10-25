# 交付状态（2026-10-25）

本文件是当前可交付状态的**单一入口**；细节见各专题文档。

## 一句话

CellFlow 已可作为本机（Windows / 16 GB / 无 NVIDIA GPU）的单细胞动力学科研工作台使用：
11 个方法可真实运行，任务队列/结果/报告/导出/数据管理/项目隔离/内存保护均已实现，
**274 项自动化测试通过**；剩余主要是**需要人工在桌面窗口点一次的验收**与明确标注的可选扩展。

## 证据（可复跑）

| 项 | 命令 / 位置 | 结果 |
|---|---|---|
| 单元/集成测试 | `.venv\Scripts\python.exe -m unittest discover -s tests` | **274 通过**（约 200 s） |
| 前端类型与构建 | `cd web && npx tsc --noEmit && npm run build` | tsc 0；build 成功 |
| 依赖一致性 | `uv pip check --python .venv\Scripts\python.exe` | 115 个包兼容 |
| Python 语法 | `python -m compileall cellflow tools install.py` | 通过 |
| 文档链接 | `python tools/check_docs_links.py` | `DOC_LINKS_OK` |
| API 路由 | `docs/api_surface.md` | 生成脚本可复跑 |
| 真实 HTTP 验收 | 任务 `9951ad091fd74f61` | completed、7 图、PDF 755 KB、ZIP 795 KB |
| 界面截图 | `docs/screenshots/{overview,data,model,results,reports}.png` | 1440×1000 实际渲染 |

## 持续集成

GitHub Actions 前后端均已成功（`.github/workflows/ci.yml`）：前端 `npm ci` + tsc + build，
后端真实安装依赖、`install.py --check`、文档链接与全量 unittest。

## 本机能力

- 可运行（11）：auto、qc、demo、leiden、dpt、paga、scVelo deterministic/stochastic/dynamical、CellRank、veloVI（独立环境）
- 实验接入（1）：RegVelo（未安装/未训练）
- 未接入（5）：DeepVelo、UniTVelo、VeloAE、dynamo、MultiVelo
- 需要 CUDA/GPU 的模型全部搁置，不在本机强行运行；详见 `docs/local_capability.md`

## 已实现的关键能力

- 数据：h5ad/loom/10x/CSV/zip 导入、10x 一致性校验、内存预检、大表分块、下载进度/取消、数据集详情/预览/哈希
- 任务：SQLite 队列、去重、孤儿恢复、取消、重命名/删除、项目隔离与归档、运行阶段实时显示
- 结果：质量校验（结构性 + 一致性，区分硬错误/软警告）、参数对比、图像可比性判断
- 报告：A 生物学 / B 技术复现；Markdown/HTML/PDF（页眉页码）；ZIP 含 manifest 与 A/B PDF
- 可解释性：观察/推断/假说三层、图注（含“不能推出什么”）、术语参考表（精确匹配，未收录不翻译）
- 桌面：Python → FastAPI → Qt 原生窗口；无浏览器界面、无控制台；单实例锁与端口释放测试

## 桌面验收（2026-10-25 已由自动化完成）

- 真实启动 Qt 窗口：1180×780，125% 缩放（dpr=1.25），console_hwnd=0（无控制台），五个视图可切换。
- 窗口内真实点击提交任务 110af20278fb4103（数据页勾选原始计数 → 模型页运行），completed 后结果页显示图/指标/日志。
- 关闭窗口：WINDOW_CLOSED → API 已停止 → 8600 释放。
- 证据截图：docs/screenshots/qt-page.png、qt-results.png。

## 仍未完成（明确搁置/可选）

1. 外网 >100 MB 下载与断点续传的真实网络抖动。
2. 跨项目移动、任务表深层虚拟化（当前分页 50/页）。
3. CUDA/GPU 模型（明确搁置）。

以上不影响本机 CPU 分析主流程；人工验收通过后即可视为可交付。

## 文档索引

- [架构](architecture.md)｜[数据流水线](data_pipeline.md)｜[开源调研](open_source_research.md)
- [模型矩阵](model_support_matrix.md)｜[本机能力](local_capability.md)｜[术语表](术语表.md)
- [测试报告](test_report.md)｜[验收清单](acceptance.md)｜[已知问题](remaining_issues.md)｜[API 路由](api_surface.md)
- 变更流水：`验证记录.md`
