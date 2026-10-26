# P3 第一批（P0 止损）阶段报告

- 日期：2026-10-26
- 分支：`p3/batch1-data-acquisition`（基于已推送的 hotfix 分支 `f688fc7`）
- 本地提交：`0cfed72` feat(p3-01/02): GEO 单文件选择下载 + 数据根目录与临时文件治理
- **推送状态：尚未推送**（见第 6 节）。代码与提交完整保存在本地分支，随时可重推。

---

## 1. 本批完成范围

| 工作包 | 状态 | 说明 |
|---|---|---|
| P3-00 基线审计 | 部分完成 | `docs/p3/storage-audit.md`、`docs/p3/geo-protocol.md` 已交付；6 份基线清单仍在生成中 |
| P3-01 单文件下载 | 完成（后端 + 最小前端） | 查询 → 清单 → 选一个 → 只下这一个 |
| P3-02 磁盘与临时文件 | 完成（下载/解压/转换链路） | 数据根可配置；默认值等于旧位置 |
| P3-03 loom.gz | 顺带完成 | 用户举的例子正是 `GSM7093919_kdm6bwt.loom.gz` |

---

## 2. 用户可见的变化

1. 输入 GSE 后先点「查询文件清单（不下载）」，看到官方标题、物种、样本数与文件表（名称/大小/类型/剪接证据等级）。
2. 默认不勾选任何文件；只有一个可导入文件时预选它，但仍需点「下载选中文件」。
3. 下载按钮在任务运行期间禁用并显示进度；失败不再需要重启软件。
4. 「数据与存储」区显示数据根目录、可用空间、临时文件占用，可切换数据目录（需勾选确认，不迁移旧数据）。
5. 剪接证据四级：已验证含剪接层 / 元数据提示 / 仅文件名暗示 / 未提示 —— **文件名不再被当成证据**。

---

## 3. 实测证据（真实 GEO）

### 3.1 清单阶段零下载

`GSE72857` 清单（8 秒内，不下载任何数据文件）：

- 官方标题（esummary，accession 校验通过）：Transcriptional heterogeneity and lineage commitment in myeloid progenitors [single cell RNA-seq]
- taxon=Mus musculus，n_samples=54
- 2 个可尝试导入的文件：`GSE72857_experimental_design.txt.gz` 113K、`GSE72857_umitab.txt.gz` 16M
- 全部标记 splice=absent（该研究没有剪接矩阵）

### 3.2 只下载选中文件

- 选择 `GSE72857_umitab.txt.gz` → `POST /api/public/geo/download` → 1 个任务
- 47 秒完成：`geo_GSE72857_GSE72857_umitab__6e001a8f.h5ad`，**111,204,697 字节**
- SHA-256 `6E001A8FCC6A380FFAAE4D02AF68AA1C221E9A55F90A11E996FBA259B3FDCD86`
- `.meta.json` 记录 `selected_files=[GSE72857_umitab.txt.gz]`、`selection_source=user_selected` —— 证明没有下载其它文件
- 下载后 `staging` 归零（0 个任务目录、0 字节）

### 3.3 样本级文件（用户举的例子）

`GSM7093919` 有独立 suppl 目录（实测 `/samples/GSM7093nnn/GSM7093919/suppl/` 才是 200，`GSM709nnn` 是 404）：

- `GSM7093919_kdm6bwt.loom.gz` 9.7M，kind=loom，splice=**filename_only**（只有文件名证据）

---

## 4. 本次修掉的真实缺陷

| # | 缺陷 | 证据 | 处理 |
|---|---|---|---|
| 1 | 输入 GSE 会遍历整个 Series 并合并 | 旧 `geo_fetch` 全量下载 + concat | 新增 `geo_fetch_selected`，只处理传入 URL |
| 2 | 大表转矩阵 OOM | `GSE72857` 27297×10368 实测 `MemoryError: Unable to allocate 213 KiB`（旧实现一次分配 float64 DataFrame + astype + to_numpy + 转稀疏，峰值约 4.5 倍稠密） | `_read_table_matrix` 改分块 + 直接建稀疏；同一文件 47 秒成功 |
| 3 | 中间文件写系统盘 | `publicdata.py` / `importers.py` 两处 `mkdtemp` | 改 `paths.make_workdir` → `<数据根>/staging/…` |
| 4 | 半成品可冒充完整文件 | 旧实现用正式文件名续传 | 下载写 `.part`，收完才改名；已存在的正式文件降级为 `.part` 续传 |
| 5 | GDS 内部 ID 当 GSE 用 | 旧 `geo_search` 直接返回 UID | 改 esearch→esummary，只返回 `accession`（GSE…） |
| 6 | 样本文件被系统性丢弃 | 旧的目录过滤用 `name.startswith('GSE')` | 新清单按扩展名过滤，样本目录单独查询 |
| 7 | 多物种元数据从未写盘 | `geo_fetch` 中 `out` 在赋值前被引用，`NameError` 被 `except: pass` 吞掉 | 移到 `out` 赋值之后 |
| 8 | 换数据根后下载"看不见" | `workspace` 只扫 `ROOT/data/public` | 同时扫描 `paths.datasets_dir()` |

---

## 5. 测试

| 套件 | 结果 |
|---|---|
| `tests/test_paths.py` | 通过（数据根优先级、旧布局兼容、staging、空间门禁、安全清理、原子提交） |
| `tests/test_geo_manifest.py` | 通过（真实 NCBI 目录页解析、大小换算、文件分类、四级剪接证据） |
| `tests/test_geo_selected_download.py` | 通过（本地 HTTP 计数：只请求选中文件；空选择拒绝；失败不产出假数据集；.part 清理；截断文件续传） |
| 合计 | **Ran 42 tests — OK** |
| 前端 | `tsc --noEmit` exit 0；`vite build` 成功 |
| 全量后端 | 本批次结束时正在重跑（上一轮曾因环境缺 `httpx2` 产生 9 个 import 错误，CI 有装） |

---

## 6. 未完成 / 阻塞

1. **推送未完成**：本轮最后阶段 shell 工具在当前环境连续超时（`pwsh` 调用在 45–300 秒内不返回，连 `echo` 与写文件都受影响），`git push` 从未真正开始（无 push 日志、无 git 进程）。代码已提交在本地分支 `0cfed72`，**下次会话第一件事就是重推并建草稿 PR**。
2. **`results/` 未接线**：切换到 D 盘后，分析结果、队列数据库、日志仍写在安装目录所在盘。这是 P3-02 最大的一块未落地收益，属于破坏性变更（需迁移方案 + 用户确认），本批**故意不做**。详见 `storage-audit.md` §8。
3. **上传落点**（`results/inputs`）与 **每任务复制两份输入**（`queue.py` + `runner.py`，4 GiB 输入约 14–20 GB 且不回收）未改，同样属于需要迁移方案的部分。
4. **GSM 枚举**：实测 `family SOFT` 很小（GSE72857 仅 5,888 字节即可列出 54 个样本与直链），但本轮未接入界面；样本级查询目前只能按已知 GSM 号查。
5. **多文件同时下载**：API 支持多文件（一文件一任务），界面本轮只做单选，避免未验证的并发进度逻辑。
6. **解压预估（gzip ISIZE）**、**filelist.txt 归档成员清单**未接入，仍以目录页大小为估算（四舍五入，配合 15% 空间余量）。
7. **`CELLFLOW_PORT`** 仍不存在（三处硬编码 8600），未在本次改动范围内。

---

## 7. 回滚

- 未合并、未发布。回退：删除分支 `p3/batch1-data-acquisition` 即可。
- 数据侧无破坏性操作：默认数据根仍是 `<repo>/data`，已登记数据集路径不变；新目录只在切换后创建。

---

## 8. 下一批建议（按项目书顺序）

1. 重推本批 + 建草稿 PR（目标分支：`hotfix/public-data-download-connectivity`，堆叠 PR）。
2. P3-00 收尾：6 份基线清单入库。
3. P3-03：导入契约（source asset / working dataset 分离、layers 全量保留、loom 方向判定）。
4. P3-04：GEO 元数据 → 中文注释（物种/组织/样本层级），并接 GSM 枚举。
5. P3-02 第二批：`results/` 迁移方案（必须单独评估与用户确认）。
