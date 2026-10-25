# 开源方案调研记录

更新时间：2026-10-25。原则：先查成熟实现，能复用就复用；科学计算只用官方库。
本文件记录**实际查阅过**的项目、用到的具体能力与最终决定，不写“参考了某某”这种空话。

## 1. 桌面窗口与界面

| 功能 | 检索/来源 | 使用的具体能力 | 许可证 | 版本 | 维护状态 | 复用方式 | 风险 | 决定 |
|---|---|---|---|---|---|---|---|---|
| 原生窗口 | https://github.com/pyside/pyside-setup （PySide6 / QtWebEngine） | `QMainWindow` + `QWebEngineView`，承载 React 页面；无地址栏 | LGPL-3.0 | 6.12.0 | 活跃（Qt 官方） | 直接依赖 | LGPL 分发义务；WebEngine 体积大 | **采用**（替代 pywebview，WebView2 恢复空白） |
| 原生窗口（旧） | https://github.com/r0x0r/pywebview | `create_window` + `start(gui='edgechromium')` | BSD-3-Clause | 6.2.1 | 活跃 | 直接依赖 | WebView2 在本机最小化恢复后空白 | 保留为可选回退（`CELLFLOW_WEBVIEW=1`），默认不用 |
| UI 组件 | https://github.com/palantir/blueprint | Button/FormGroup/NumericInput/HTMLTable/Tabs/Drawer/Callout/Tag/Popover | Apache-2.0 | 6.21.0 | 活跃 | 直接依赖 | 包体较大（已接受） | **采用**，统一样式覆盖 |
| 可调面板 | https://github.com/dockview/dockview | `DockviewReact` 多面板、拖拽、标签页 | MIT | 8.6.1 | 活跃 | 直接依赖（dockview-react） | 布局持久化尚未接 | **采用**于结果工作区 |
| 数据表格（备选） | https://github.com/TanStack/table | 无头表格：排序/筛选/分组/虚拟化 | MIT | 未安装 | 活跃 | **仅阅读 README**，未引入 | 需要自己写样式/交互 | 暂不引入：Blueprint `HTMLTable` + 服务端筛选已够用；任务列表超过千级时再评估 |
| 虚拟列表（备选） | https://github.com/TanStack/virtual | 虚拟滚动 | MIT | 未安装 | 活跃 | 未引入 | 无 | 同上，暂缓 |
| PDF 导出 | Qt 官方 `QTextDocument.print_(QPrinter)`（PySide6） | `cellflow/pdf.py`；A4、12 mm 边距、`setBaseUrl` 解析图片 | LGPL-3.0（随 PySide6） | 6.12.0 | 活跃 | 直接调用 | 只支持 HTML 子集；offscreen 插件无中文字体，必须用 windows 插件 | **采用**（零新增依赖） |
| 后端报告渲染 | https://github.com/Python-Markdown/markdown | `markdown.markdown(text, extensions=[extra, sane_lists])` → HTML 报告与 PDF | BSD-3-Clause | 3.11.2 | 活跃 | 直接依赖 | 只覆盖常用 Markdown 语法；缺库回退 `<pre>` | **采用** |
| 报告 Markdown 渲染 | https://github.com/markedjs/marked | `marked.parse` | MIT | 15.x | 活跃 | 直接依赖 | 不负责清洗 HTML | 采用 + DOMPurify 清洗 |
| HTML 清洗 | https://github.com/cure53/DOMPurify | `DOMPurify.sanitize` | Apache-2.0 或 MPL-2.0 | 3.x | 活跃 | 直接依赖 | 需配置允许的标签/协议 | **采用** |

## 2. 科学计算

| 功能 | 上游实现 | 用到的接口 | 许可证 | 版本 | 复用方式 | 风险/限制 | 决定 |
|---|---|---|---|---|---|---|---|
| RNA velocity | https://github.com/theislab/scvelo | `pp.filter_genes`、`pp.moments`、`tl.velocity(mode=deterministic/stochastic/dynamical)`、`tl.velocity_graph`、`tl.velocity_embedding`、`tl.velocity_pseudotime` | BSD-3-Clause | 0.3.4 | 直接调用 | dynamical 需先 `recover_dynamics`；CPU 实现 | 采用 |
| 变分速度 | https://github.com/scverse/scvi-tools（`scvi.external.VELOVI`） | `setup_anndata` → `train` → `get_velocity(return_numpy=True)` → `get_latent_time` → 后验采样 | BSD-3-Clause | 1.2.0（隔离环境 `.venv-velovi`） | 直接调用 | 1.2 起 VELOVI 移到 `external`；numpy<2 | 采用（独立环境） |
| 命运概率 | https://github.com/scverse/cellrank | `GPCCA`、`VelocityKernel`/`PseudotimeKernel`、`compute_fate_probabilities` | BSD-3-Clause | 2.0.7（最后一个支持 py3.11 的版本） | 直接调用 | >6000 细胞稠密分解内存风险 | 采用，超限拒绝 |
| 质控/降维/聚类 | https://github.com/scverse/scanpy | `qc` 指标、`pca`、`neighbors`、`leiden`、`umap`、`diffmap`/`dpt`、`paga` | BSD-3-Clause | 1.11.5 | 直接调用 | Leiden 依赖 igraph | 采用 |
| 数据结构 | https://github.com/scverse/anndata | `.h5ad` 读写、层/obsm/uns | BSD-3-Clause | 0.11.x | 直接调用 | 版本间 API 差异 | 采用 |
| 公开数据（GEO/URL） | https://github.com/chanzuckerberg/h5adify（MIT） | 设计：下载 → 标准化为 h5ad → 补 obs 字段 | MIT | 0.1.1 | **复用设计，重写代码**（不引入 GEOparse/bs4/lxml） | 仅测试过 GEO CSV/10x | 采用 |
| 公开数据（CELLxGENE） | https://github.com/chanzuckerberg/cellxgene-census（代码 MIT，数据 CC-BY） | **仅阅读 README**，确认 Census API/数据许可 | MIT / CC-BY | — | 未接入 | 需 `cellxgene-census` 包与网络；本机到该站网络曾不可达 | 暂缓；当前用 CELLxGENE 数据集下载接口 |
| 富集分析 | https://github.com/zqfang/GSEApy | **阅读 README**：GSEA/ssGSEA/GSVA/Enrichr，MIT | MIT | — | 未接入 | 新版需 Rust 工具链；引入会显著增加安装复杂度 | 暂缓（列入后续生物学解释阶段） |

## 3. 明确未采用/未找到

| 名称 | 检索结论 | 处理 |
|---|---|---|
| KoopCell | 按名称检索未找到可核实的官方仓库/论文/安装方式；本项目 registry 中也没有该方法 | 不接入、不展示，避免“假模型” |
| DeepVelo / UniTVelo / VeloAE / dynamo / MultiVelo | 官方实现存在，但需要独立环境与较长训练；本机未训练 | registry 标为“尚未接入”，界面如实显示，不执行 |
| RegVelo | 官方 0.4.2 已准备隔离环境；本机未安装、未训练 | 标为“实验接入” |
| Celery / Redis | 单用户本地应用不需要分布式队列 | 用 SQLite + 单 worker 子进程 |

## 4. 依赖版本与许可证清单

见项目根目录 `THIRD_PARTY_NOTICES`（随安装版本更新）。

## 5. 决策原则

1. 科学算法只调用官方实现，不复制论文公式自行重写。
2. 前端复用成熟组件（Blueprint/Dockview），不自造基础控件。
3. 有明确需求才引入新依赖；引入前检查许可证、维护状态、Windows 兼容与安装成本。
4. 对未验证的集成（CELLxGENE Census、GSEApy、TanStack 虚拟表）明确标注“未接入”，不写进界面承诺。
