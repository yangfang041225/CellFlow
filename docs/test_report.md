# 测试报告

更新时间：2026-10-25。所有命令在本机 `C:\Users\mtgmc\Documents\Codex\2026-10-24\referenced-chatgpt-conversation-this-is-an\outputs\CellFlow` 执行。

## 1. 测试环境

| 项 | 值 |
|---|---|
| OS | Windows（`platform.platform()` 见 `logs/app.log`） |
| Python | 3.11.15（`.venv`），另有 `.venv-velovi`（scvi-tools 1.2.0 + numpy 1.26.4） |
| 前端 | Node 26.4.0 / npm 11.17.0；React 18.3.1 + Blueprint 6.21.0 + Dockview 8.6.1 |
| 数据 | `examples/synthetic.npz`（240×120，含 spliced/unspliced）、`data/public/geo_GSE84133.h5ad`（8569×20125） |

## 2. 自动化测试

命令：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果：**Ran 143 tests in 99.868 s — OK**（2026-10-25 16:50）。

| 测试文件 | 数量 | 覆盖 |
|---|---|---|
| `tests/test_contracts.py` | 24 | 版本/registry 一致性、配置校验、device、隔离环境、格式体检 |
| `tests/test_pipeline.py` | 11 | QC/Leiden/PAGA/DPT/scVelo deterministic+stochastic+dynamical/CellRank/auto/veloVI（隔离环境） |
| `tests/test_importers.py` | 18 | h5ad/loom/10x/CSV/zip、方向自动判断、10x 三件套一致性 |
| `tests/test_publicdata.py` | 7 | GEO 路径、10x 解压、CSV 计数表、下载与进度 |
| `tests/test_ui_smoke.py` | 6 | Streamlit 回退五页真实渲染、阅读页面不提交任务 |
| `tests/test_desktop.py` | 10 | 单实例锁、陈旧锁、GUI PE、启动器修复、隐藏进程、API 启停与端口释放 |
| `tests/test_review_fixes.py` | 7 | F01/F02/F15/ZIP/路径白名单/去重/未确认计数 |
| `tests/test_jobs_meta.py` | 20 | 任务名称、筛选、取消、重命名/删除、质量校验、环境接口、详情 stage、PDF 导出、参数对比、ZIP 清单、数据集中止登记/引用检查 |
| `tests/test_download_network.py` | 2 | 本地 HTTP 真实下载：完成移动、取消清理 |
| `tests/test_downloads.py` | 5 | 后台下载进度、取消清理、失败上报、API 启停 |
| `tests/test_projects.py` | 8 | 项目创建/切换/改名/归档/删除保护、数据集与任务按项目隔离 |
| `tests/test_terms.py` | 4 | 术语精确匹配、/api/terms、预览 zh 字段 |
| `tests/test_reports.py` | 4 | 箭头密度、显示缩放、仅显示说明 |
| `tests/test_api_surface.py` | 6 | 路由存在、处理函数公开、无重复、无参 GET 不 500、任务详情/PDF 端点 |
| `tests/test_docs.py` | 2 | 文档本地链接可解析、术语表与代码一致 |
| `tests/test_worker.py` | 1 | 真实后台 worker：提交 → CLI → 结果/报告/质量/阶段 |
| `tests/test_progress.py` | 1 | 运行中 provenance 阶段即时写盘 |
| `tests/test_memory.py` | 6 | 内存估算（npz/h5ad 稀疏）、超限拒绝、API 413、运行中低内存护栏 |
| `tests/test_h5ad.py` | 1 | 稀疏 h5ad 往返 |

> 运行时长在不同负载下波动（84–140 s）；`tests/__init__.py` 把 BLAS/Numba 线程限制为 1（与生产一致），避免 numba 并行 lowering 在 Windows 上偶发数分钟卡住。veloVI 测试需要 `.venv-velovi` 已安装，否则会被跳过。

## 3. 前端构建

```powershell
cd web
npx tsc --noEmit     # exit 0
npm run build        # ✓ built in ~6 s；有 >500 kB chunk 警告
```

## 4. 真实端到端证据

| 场景 | 结果 | 证据 |
|---|---|---|
| API 提交 auto（模拟数据） | completed，6 图、8 指标、A/B 报告与 HTML 齐全 | 任务 `6b575435d11b42ea`、`71f231ba91f74f9f` |
| GEO 真实数据（8569 细胞） | 只读读取与 QC 通过；无剪接层时速度方法被正确拒绝 | 任务 `fdf82f41b3eb4356` |
| veloVI（隔离环境） | completed，速度 + 不确定性 + 潜在时间 | 任务 `4c967bc71f4d4ccd`；测试 `test_velovi_runs_in_isolated_environment` |
| 报告图片链接 | API 返回 `/api/runs/{id}/figure/...`，不再 404 | 任务 `6b575435d11b42ea` 报告 Markdown |
| ZIP 导出 | HTTP 200，`PK` 开头，约 76 KB | 同上任务 |
| auto + velovi | HTTP 400，提示单独运行 veloVI | `tests/test_review_fixes.py::test_auto_velovi_is_rejected` |
| 未确认原始计数 | 适配器抛错并说明原因，不做数值改写 | `test_unconfirmed_counts_are_refused` |
| 取消排队任务 | 状态变为 cancelled，不再运行 | `test_cancel_queued_job` |
| 环境接口 | `/api/env` 返回版本、设备、磁盘、路径、队列 | `test_env_endpoint_reports_environment` |
| PDF 报告 | A/B 报告经 Qt 转 PDF（%PDF、>5 KB、中文可检索） | `test_pdf_export_when_completed_run_exists`；`A_report.pdf` 4 页 4938 字符 |
| 重命名/删除任务 | 排队任务禁止删除；取消后可删除且记录消失 | `test_rename_and_delete_job_record` |
| 参数对比 | 同任务对比无差异；不同任务返回变更键 | `test_compare_configs_between_runs` |
| 结果包 | ZIP 含 `manifest.json`（任务/配置/环境/质量/文件清单）与 A/B PDF | `test_zip_contains_manifest_and_pdfs` |
| 状态栏/健康检查 | `/api/health` 返回队列计数、设备、当前项目；底部状态栏每 10 s 刷新 | `test_health_reports_queue_and_device` |
| 安装自检 | `python install.py --check` 只读检查环境与关键导入 | `test_install_check_mode_reports_ready` |
| 未捕获异常 | 返回 JSON 中文提示 + 写 `logs/api-errors.log`，不把 traceback 直接给界面 | `test_unhandled_exception_returns_json_and_logs` |
| API 表面审计 | 处理函数不以下划线开头（捕捉装饰器挂错）、无重复路由、无参 GET 不 500 | `tests/test_api_surface.py`（5 项） |
| 术语参考 | 45 个通用术语精确匹配；未收录保持原文；界面标注“参考翻译” | `tests/test_terms.py`；`docs/术语表.md` |
| 数据集预览 | 稀疏/稠密表达范围、非零比例、前 5 细胞/10 基因、obs 示例与「唯一值数 + 最常见取值」摘要 | `test_dataset_detail_reports_hash_and_shape` 扩展断言 |
| 数据集移除 | 只移除登记、文件保留；被任务引用时返回 400 | `test_unregister_dataset_keeps_file`、`test_delete_referenced_dataset_is_refused` |
| 报告解释分层 | A 报告含「直接观察 / 计算推断 / 生物学假说」三层 | `test_interpretation_layers_are_separated`；真实 auto 报告 |
| HTML 报告渲染 | Markdown 已渲染为 `<h2>/<table>/<blockquote>`，不再是整篇 `<pre>` | `test_pipeline` 的 HTML 断言；真实报告 8 h2 / 3 table / 5 blockquote |
| PDF 页眉页脚 | 每页有「CellFlow · 任务 × · 日期」与「第 N / M 页」，页脚为来源说明 | 实测 `71f231ba91f74f9f`：7 页、528 KB、首页可检索到页眉与页码 |
| 大表分块读取 | >32 MB 的 CSV/TSV 逐块转 float32 稀疏后 vstack/hstack；小表保持原路径，两者数值一致 | `test_streaming_csv_matches_plain_read`、`test_streaming_csv_flipped_orientation` |
| CSV/表格内存 | 导入前统一内存预检；表格导入说明文件大小与估算峰值 | `test_memory_precheck_blocks_import`、`test_csv_import_reports_memory_estimate` |
| 10x 完整性 | 缺 features/维度不符/负数 → 明确报错；缺 barcodes → 占位名并记录；非整数 → 警告 | `test_10x_*`（5 项） |
| 图注 | 每图有编号与“画什么/颜色箭头含义/不能推出什么”说明；Markdown 与 HTML/PDF 都有 | `test_figure_captions_explain_limits`、auto 报告断言 |
| 项目管理 API | 创建/切换/归档/恢复/删除的完整生命周期与保护 | `test_api_project_lifecycle` |
| 项目归档/删除 | 归档切走当前项目；默认/当前/有数据/有任务的项目拒绝删除 | `test_archive_*`、`test_delete_project_*` |
| scVelo deterministic | 补跑并加入回归：completed、velocity、quality ok | `test_scvelo_deterministic_end_to_end`；`results/verified-scvelo-det-1792912892` |
| 两次运行对比 | 配置差异 + 指标差异；只有细胞集合与邻域/PCA/种子一致时才允许并排比较嵌入图 | 自比较 `can_compare_figures=True`；不同参数任务给出不可比原因 |
| 运行内存护栏 | 低内存时在计算前明确失败，而不是 OOM | `test_require_stops_when_memory_is_low` |
| 项目隔离 | 数据集登记与任务按项目过滤；切换项目后列表变化 | `test_datasets_are_scoped_per_project`、`test_queue_filters_by_project` |
| 后台 worker 全链路 | 提交后由独立 worker 进程执行 CLI，产出 result/报告/quality，阶段到 reports | `test_worker_completes_qc_job` |
| 运行阶段 | 运行中每阶段写 `provenance.last_stage`，界面显示真实阶段 | `test_stage_is_written_during_run` |
| 内存预检 | 超可用内存 90% 时 API 413 / 任务失败并说明；详情显示预估 | `test_precheck_refuses_when_over_limit`、`test_api_rejects_when_memory_check_fails` |
| 真实 HTTP 下载 | 本地 http.server → download_file → 标准化 → 移入目录；取消会立即抛出并清理 | `test_url_download_completes`、`test_url_download_cancel_cleans_partial` |
| 下载任务 | 后台进度/取消；成功才移入 data/public，取消/失败清理临时目录 | `test_progress_then_done_moves_file`、`test_cancel_stops_and_cleans_partial` |
| 数据集详情 | SHA-256、维度、obs、层、X 状态、引用任务数、表达范围/非零比例/前几个细胞与基因 | `test_dataset_detail_reports_hash_and_shape` |
| 结果一致性校验 | 14 项检查（含分群求和、嵌入维度、命运概率范围/行和、PAGA 对称、候选基因存在性）；速度按 HVG 子集校验细胞轴/基因列/基因归属；软问题只警告 | 真实 auto 质量记录 `ok=True`；`test_quality_flags_inconsistent_fate_and_embedding` |

## 5. 未验证（需要人工在桌面确认）

1. Qt 窗口内点击「运行分析」提交新任务（代码链路与 API 已验证；窗口内点击未完成人工验收）。
2. Qt 窗口关闭后 8600 端口释放（自动测试覆盖启动器生命周期；最终窗口关闭未人工点验）。
3. 高 DPI（125%/150%）下的布局与文字裁切。
4. 大文件（>1 GB）上传与长时间运行的内存曲线。
5. CELLxGENE 大文件真实下载（接口已实测可达；>100 MB 的下载未实测）。
6. GPU/CUDA 路径（本机无 NVIDIA GPU）。

## 6. 已知测试局限

- `test_ui_smoke` 验证的是 **Streamlit 回退界面**；React 界面由构建、截图与人工点击验证，没有 DOM 级自动化。
- `test_velovi_*` 依赖隔离环境，未安装时跳过（不算通过）。
- 桌面测试验证锁、进程与端口，不验证窗口像素级渲染。
- 没有 CI；所有测试均在当前机器手动执行。
