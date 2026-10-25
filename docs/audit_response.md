# 审计项目书落实对照（F01–F40）

对应文档：`CellFlow_全面审查与修改项目书.md`。
状态口径：**已修**＝有代码 + 回归/实测证据；**部分**＝主路径已改但仍有明确边界；**实验性**＝保持不可宣称可用；**搁置**＝按范围明确不做。
最近验证：本地 272 项测试全过；GitHub Actions 后端/前端均绿（提交 `aad10ec` 之后的 CI）。

| 编号 | 状态 | 做了什么 | 证据 / 边界 |
|---|---|---|---|
| F01 解压安全 | 已修 | 新增 `cellflow/archive.py`：拒绝父目录、盘符、UNC、绝对路径、链接、特殊文件；累计字节/文件数/层级限额；校验后写入 | `tests/test_archive.py` 7 项；恶意 zip/tar 无越界副作用 |
| F02 取消进程树 | 已修 | 记录计算子进程 PID/创建时间；取消先置 cancelling，`taskkill /T`/进程组终止，确认退出才写 cancelled；PID 复用不误杀 | `tests/test_queue_lifecycle.py`（父子进程/孤儿/PID 复用） |
| F03 全局并发 | 已修 | supervisor 单行租约、`start_worker` 幂等、领取时全局 running 上限（默认 1，可配置） | 24 项队列测试；并发提交峰值 ≤1 |
| F04 项目登记 | 已修 | 删除重复 `unregister_dataset`；保留字典 entry；按 project+path 注销 | `test_unregister_one_project_keeps_other_association` |
| F05 任务去重 | 已修 | 去重键含 project_id；事务内复查 + 唯一索引；复用不落多余快照 | 队列生命周期测试（跨项目独立、并发唯一） |
| F06 模型准入 UI | 已修 | 前端按 registry 规则复算完整前提（root/group/GRN），表单变化即重新预检；按钮与提交同源 | `web/src/services/precheck.ts`；服务端完整配置预检仍以运行器为准 |
| F07 声明作用域 | 已修 | 声明绑定 `dataset.id@mtime`，切换数据/版本需重新确认；旧全局键清理 | 前端逻辑冒烟 17 项 |
| F08 结果归属 | 已修 | 结果只显示当前数据集任务；空列表/切项目立即清空；旧响应按 token 丢弃；「查看」定位 | 前端改动 + 构建；未做浏览器 E2E |
| F09 矩阵方向 | 已修 | AnnData 信任 obs/var，不再按形状转置；文本仍按表头方向判断并记录 | `test_h5ad_axes_are_authoritative`、100×6000 实测 |
| F10 导入语义 | 已修 | 缺失/重复值计数写入 notes+extras；元数据空值保留空；缺失分组列拒绝分群 | `tests/test_data_contract.py` 4 项；CSV 修复预览 UI 未做 |
| F11 veloVI 协议 | 已修 | 按 scvi-tools 1.2.0：S/U 计数校验 → normalize/log1p/moments → `Ms/Mu` 训练；输出协议与审计文件 | 真实 smoke（2 epochs，仅接口，不声称收敛）；`test_science_contract` 13 项 |
| F12 箭头几何 | 已修 | 点与向量共用 `ScreenAffine`；默认等比例，`stretch` 保留 5.857 探针 | `test_figures_semantics`：45°/基向量/斜率 |
| F13 绘图语义 | 已修 | `FigureSpec` 统一标题/色阶/字段；PAGA 原始对称矩阵 0–1；命运概率来自声明字段 | 同上；8 张图 spec 落盘 |
| F14 报告真实性 | 已修 | 4.10 四层解释（观察/派生/推断/假说）由 `executed_steps` 驱动；未执行速度不再宣称；推断与观测分开 | `test_figures_semantics`（无速度/三模式/叙述断言） |
| F15 质量闸门 | 已修 | `quality.ok=False` 抛错并 failed，不写 completed；硬/软检查分开 | `test_quality_failure_blocks_completed` |
| F16 HTML 安全 | 已修 | 数据单元转义；最终 HTML 白名单清洗；CSP；无脚本/事件属性 | `test_html_safety` 11 项；离线渲染验证 |
| F17 PDF | 已修 | 设备像素 + 96 dpi 排版；A4、页眉页码；正文中位 10.5 pt | pypdf 实测 11 页；B 报告 9.75 pt |
| F18 ZIP 资源 | 已修 | `bundle_entries` 枚举报告/JSON/PDF/figures（含 stream.png）；导出前校验引用 | `test_jobs_meta` ZIP 引用齐全 |
| F19 预处理判定 | 已修 | 声明/观察/有效结论/冲突/步骤；auto 与独立 scVelo 共用协议；unknown 不改数值 | `test_science_contract`；provenance 记录声明 |
| F20 RegVelo | 实验性 | 保持 experimental；补 n_top_genes/设备审计与 velocity 输出协议；无输出即报错 | 本机无 RegVelo 环境，**未真实训练** |
| F21 比较契约 | 已修 | 细胞/基因/预处理哈希 + HVG/UMAP/邻域/种子 + 实际嵌入哈希；不同坐标禁止同图比较；指标不排名 | `test_compare_contract` 6 项 + API 自比较 |
| F22 恢复/心跳 | 已修 | drain 启动恢复、心跳/租约；仅 PID 失联或租约超时判中断；区分 interrupted/failed | 24 项队列测试 |
| F23 准备失败 | 已修 | 领取后配置/启动/收尾全在保护块；三类准备失败均落终态 | 队列测试 |
| F24 内存/磁盘 | 部分 | 上传流式限流；预览统计修正；内存预检分段；HDF5 预估已计入 layers/raw/10x | 磁盘余量与压缩展开仍是估算 |
| F25 完整性标识 | 已修 | 大文件仅首尾+大小 → 标 `sha256-partial-headtail-size`；完整哈希保留给内容版本 | UI 显示指纹类型；`test_hash_kind_is_explicit` |
| F26 预览统计 | 已修 | sparse 用 nnz/(n×g)、整体均值；dense 用 `issparse` 判定 | 25% 非零/0.5 均值测试 |
| F27 下载 | 已修 | 内容版本化文件名、原子发布、失败不覆盖、状态持久化、项目归属；多物种筛选写入 sidecar 并记录到任务 | `test_downloads_persistence`（新增筛选记录用例） |
| F28 数据移除 | 已修 | 项目作用域注销 + 公共文件忽略列表，重启/刷新不复活 | `test_removed_public_file_does_not_resurrect` |
| F29 参数一致性 | 已修 | 前端能力表驱动；无效键不提交；补 max_iter/mt_prefix/tissue/cellrank_n_states/grn_confirmed；配置 JSON 导入导出 | `tsc/build`；逻辑冒烟 52 项 |
| F30 数据集识别 | 已修 | 保留原始名/来源；复合扩展 `.csv.gz/.tar.gz`；内部 id 仅详情 | `test_dataset_identity` 3 项 |
| F31 异步状态 | 已修 | 请求 token/AbortController、草稿版本、日志持续刷新、下载记录恢复 | 前端逻辑冒烟；未做真实延迟注入 |
| F32 测试流程 | 部分 | 新增夹具生成任务、重复定义/文档链接检查、CI 前后端 | 无浏览器 E2E/覆盖率基线；CI 中 veloVI 等环境项跳过 |
| F33 历史规模 | 已修 | runs 服务端 limit/offset/total；单行查询；日志尾读；counts 聚合；详情默认压缩大数组（`?compact=0` 取完整） | `test_api_pagination`（分页/尾读/压缩用例）；未做前端虚拟滚动 |
| F34 环境发布 | 部分 | `install.py --check` 真实 import/版本；隔离环境自身解释器探测；manifest | registry 运行时仍未消费 manifest；无签名安装器 |
| F35 窗口/DPI | 部分 | 按 availableGeometry 收敛；980 宽单栏/工具栏收纳；实测 1180/980 无横向溢出 | 125/150/200% 原生多屏矩阵未系统实测 |
| F36 多入口文档 | 已修 | README 只推荐 Qt；历史入口边界表；页面名/端口修正 | 链接检查通过 |
| F37 复现包 | 部分 | 报告包 vs 复现包；哈希清单 + REPRODUCE.md；full 含 ≤200 MB 输入与 ≤200 MB 模型目录 | 超大输入仍只记哈希 |
| F38 方法说明 | 已修 | 方法卡（解决什么/数据/不适用/失败下一步）；计划方法折叠标注未接入 | 前端文案逐字对齐 method_content |
| F39 全图专业化 | 部分 | 统一仿射/配色/单位/图注；主图优先、诊断折叠 | 未交付完整论文级渲染器（距离/区域掩膜等） |
| F40 可维护性 | 部分 | 清理重复定义/BOM；新增重复定义检查；报告/队列拆分职责 | adapters.py 仍较大，未做 Method/Executor 协议重构 |

## 仍未完成（明确保留）

- RegVelo 真实训练（无 `.venv-regvelo`）、GPU/远程执行。
- 浏览器端 E2E、系统 DPI 矩阵、干净机器冷安装。
- F37 大输入/模型检查点的完整复现包。

## 复用方式

- 每个「已修」项都能在表中找到对应测试文件；`python -m unittest discover -s tests` 可复跑。
- 状态不把「有代码」写成「已验证」：实验性/部分项在验收文档中保持 ⚠️。
