# GEO 文件清单与元数据协议

P3-01 交付物 · 版本 1.0 · 2026-10-26
配套实现：`tools/p3_probe/geo_probe.py`（可直接运行，用于复现本文所有数字）
配套证据：`data/_probe/evidence/`（本文引用的原始输出都整份留档）

> 本文只描述**协议**，不含产品代码。所有"实测"标记的结论都附了真实命令输出；
> 标"文档推断"的是依据 NCBI 官方文档/接口语义得出、未逐一实测的；
> 标"未验证"的是本次确实没测到的，不得当作已知事实使用。

---

## 0. 结论摘要（给实现方的 8 条硬约束）

1. **文件大小只有两个可信来源**：`HEAD` 的 `Content-Length`（单文件直链），
   和 `filelist.txt` 的 `Size` 列（归档内成员）。目录列表里的 `113K/16M/5.9G` 是四舍五入值，
   **不能**当字节数用（实测：`113K` 实际 115,876 字节）。【实测】
2. **不要用 esearch 的 UID 当 GSE 号**。`db=gds` 返回的 UID 是 esummary 的 UID，
   一个 `GSE72857[ACCN]` 查询会同时命中 GSE / GPL / GSM 三种记录（UID 分别是
   200072857 / 100019057 / 301944947），必须读 `esummary.accession` 并过滤 `entrytype=='GSE'`。【实测】
3. **一次请求就能展开全部 GSM**：`GSE*_family.soft.gz` 只有 2 KB–6 KB，
   却包含 Series + 全部 Sample 记录以及每个样本的 `!Sample_supplementary_file_N` 直链。【实测】
4. **Series 目录 ≠ 全部文件**。GSE227256 的 Series 目录只有一个
   `GSE227256_RAW.tar`(20,080,640)，真正的 loom 在 Sample 层
   （`GSM7093919_kdm6bwt.loom.gz` 10,159,290）。只读 Series 目录会漏掉全部样本文件。【实测】
5. **同一个文件通常有两条路径**：Sample 层直链（可直接下单个文件）和
   `GSE*_RAW.tar` 内的同名成员。默认走直链，**永远不要为了让用户拿一个 loom 去下 5.87 GiB 的 tar**。【实测】
6. **解压预估可以零成本获得**：对 `.gz` 文件做 `Range: bytes=-4` 只读 4 字节，
   最后 4 字节的 ISIZE 就是解压后字节数（实测 10,159,290 → 12,613,264 与本地解压完全一致）。【实测】
7. **剪接证据只有解析过文件内部才能标 verified**。文件名带 "Velocyto" 的
   `GSE274591_...Velocyto...h5ad` 实测 `layers=[]`（空的），标 `absent`；
   `GSM7093919_kdm6bwt.loom.gz` 实测含 `spliced/unspliced/ambiguous`，标 `verified_by_content`。【实测】
8. **列出全部文件名与大小不需要下载任何文件**。GSE274591 的 19 个唯一文件
   （含 5.87 GiB 归档、3.31 GiB rds）在 8.3 秒内全部列出，下载量为 0 字节。【实测】

---

## 1. 范围与术语

| 术语 | 含义 |
|---|---|
| Series | `GSExxxxx`，一次实验的提交单元 |
| Sample | `GSMxxxxxxx`，一个样本；其补充文件挂在 Sample 层 |
| Platform | `GPLxxxxx`，测序平台 |
| 补充文件 | supplementary file，用户真正要下载的数据文件（h5ad/loom/mtx/rds/csv…） |
| 归档 | `GSExxxxx_RAW.tar`，把该 Series 下所有样本文件打成一个包 |
| 清单 | `filelist.txt`，GEO 自动生成，列出归档及其成员的**精确字节数** |

本协议服务于产品目标：**查询 → 预览 → 用户选单个文件 → 只下载该文件**。

---

## 2. 端点清单（全部为实测可用的真实 URL 模板）

### 2.1 元数据

| 用途 | URL 模板 | 实测结果 |
|---|---|---|
| Series/Sample 元数据（单条，SOFT 文本） | `https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={ACC}&targ=self&form=text&view=brief` | 200，GSE72857=5,063 字节 / GSM7093919=1,676 字节 |
| **整族元数据（首选）** | `https://ftp.ncbi.nlm.nih.gov/geo/series/{STUB}/{GSE}/soft/{GSE}_family.soft.gz` | 200，GSE227256=2,008 字节(→8,190 字符) / GSE72857=5,888 字节(→173,156 字符) |
| MINiML 等价物 | `…/series/{STUB}/{GSE}/miniml/{GSE}_family.xml.tgz` | 200，GSE72857=6,201 字节 |
| 表达矩阵（series matrix） | `…/series/{STUB}/{GSE}/matrix/{GSE}_series_matrix.txt.gz` | 200，GSE72857=5,151 字节 |

`{STUB}` 的构造：`GSE72857 → GSE72nnn`（保留前若干位，末 3 位换成 `nnn`）；
`GSM7093919 → GSM7093nnn`。

> **编码坑（实测）**：`acc.cgi` 返回的 `Content-Type: geo/text` **不带 charset**，
> `requests` 会按 ISO-8859-1 解码，导致 `Ya’ara` 变成 `YaâYara`。
> 必须强制 `resp.content.decode('utf-8')`。family.soft.gz 不存在此问题（自己解压）。

### 2.2 文件（下载端点）

| 层级 | URL 模板 |
|---|---|
| Series 补充文件目录 | `https://ftp.ncbi.nlm.nih.gov/geo/series/{STUB}/{GSE}/suppl/` |
| Sample 补充文件目录 | `https://ftp.ncbi.nlm.nih.gov/geo/samples/{STUB}/{GSM}/suppl/` |
| 单个文件直链 | 目录 URL + 文件名 |
| Series 归档清单 | `…/series/{STUB}/{GSE}/suppl/filelist.txt` |
| Series 目录结构 | `…/{GSE}/` 下只有 `matrix/ soft/ miniml/ suppl/` |
| Sample 目录结构 | `…/{GSM}/` 下**只有** `suppl/`（样本层没有 soft/、miniml/）【实测】 |

### 2.3 检索（E-utilities，`db=gds`）

| 用途 | URL 模板 |
|---|---|
| 关键词 → UID 列表 | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=gds&term={Q}&retmax={N}&retmode=json` |
| UID → 记录（含 accession） | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=gds&id={ID,ID,…}&retmode=json` |

**UID ≠ accession（必须实测过才知道）**

    查询 GSE72857[ACCN] → count=56，idlist 前三个 UID = 200072857 / 100019057 / 301944947
    esummary 展开：
      uid=200072857  accession=GSE72857  entrytype=GSE   n_samples=54
      uid=100019057  accession=GPL19057  entrytype=GPL   gse=212339;117320;…（该平台下全部 GSE，数千项）
      uid=301944947  accession=GSM1944947 entrytype=GSM  gse=72857;72859

即：一次 accession 查询会命中 GSE+GPL+GSM 三种记录，共 56 条（54 个样本 + Series + 平台）。
**只有 `entrytype=='GSE'` 的那条才是 Series**。把 UID 直接当 GSE 号显示，
会出现"用户点进 GSE200072857"这种不存在的编号。

**`suppfile` 字段不能用来生成文件清单**（实测）：

    GSE72857   suppfile='TXT'
    GSE227256  suppfile='LOOM'
    GSE274591  suppfile='H5AD, LOOM, MTX, RDS, TSV'

它只是"整个族里出现过的文件类型集合"，**没有文件名、大小、归属、数量**。
GSE227256 的 `LOOM` 实际在 Sample 层和 tar 里，Series 目录只有一个 tar。

### 2.4 明确不要用的端点

| 端点 | 实测问题 |
|---|---|
| `acc.cgi?acc=GSE…&targ=all&form=text` | 返回 11–32 MB（GSE227256 2 个样本就 31,824,026 字节），含数据表，不可用于预览 |
| `acc.cgi?acc=GSM1,GSM2,GSM3&targ=self&form=text` | 200 但 **`^SAMPLE` 计数 = 0**，多编号不生效；只能一条一个请求 |
| 目录列表的 `Size` 列 | 四舍五入（`113K` / `16M` / `5.9G`），非精确字节 |

---

## 3. 文件大小怎么可靠获得（核心）

### 3.1 三种来源的实测对比

| 来源 | 取法 | 精确性 | 覆盖范围 | 额外代价 |
|---|---|---|---|---|
| **HEAD `Content-Length`** | `HEAD <文件直链>` | **精确字节** | 任何可直接下载的 URL | 1 次轻量请求 |
| **`filelist.txt` 的 `Size`** | `GET …/suppl/filelist.txt` | **精确字节** | 归档成员 + 归档本身 | 1 次请求（几百字节） |
| 目录列表 `Size` 列 | `GET …/suppl/` | 只给 K/M/G 级显示值（113K / 16M / 9.7M / 5.9G / 1.6G），不可还原成字节 | 目录内所有文件 | 1 次请求 |
| `esummary` | — | **没有大小字段** | — | — |

实测三方对账（同一文件 3,000 万像素级一致）：

    GSM7093919_kdm6bwt.loom.gz
      目录列表        : 9.7M
      HEAD            : 10,159,290
      filelist.txt    : 10,159,290       ← 与 HEAD 完全一致
      Range bytes=-4  : 解压后 12,613,264（本地解压实测 12,613,264）

    GSE227256_RAW.tar         HEAD 20,080,640   filelist 20,080,640
    GSE274591_RAW.tar         HEAD 6,299,494,400 filelist 6,299,494,400
    GSE72857_umitab.txt.gz    HEAD 16,413,252   （目录列表写 16M）
    GSE72857_experimental_design.txt.gz  HEAD 115,876（目录列表写 113K）

### 3.2 推荐链路与降级

    单文件直链（Sample 层或 Series 层）：
      ① HEAD → Content-Length          ← 主路径，精确
      ② Range: bytes=0-0 → Content-Range 里的总长   ← 镜像不支持 HEAD(405) 时降级
      ③ 目录列表 Size 列 + "≈" 前缀     ← 仅用于占位显示，UI 必须标约等
      ④ 显示"未知大小"，仍允许用户下载  ← 最后兜底（不要因为没大小就不给下）

    归档内成员：
      ① filelist.txt 的 Size 列        ← 唯一来源，且精确
      ② 若该文件在 Sample 层有同名直链，直接用那个直链的 HEAD 值（两条路径互相校验）
      ③ 无 filelist.txt（老 GSE）→ 只能流式扫 tar 头（见 3.4）

**实务建议**：预览页先用 `filelist.txt` + 目录列表拼出完整清单（1–2 次请求），
再把需要显示的条目并发做 `HEAD` 补精确值（并发 8，实测 GSE274591 的 19 个可直链文件共 8.3 秒）。
不要在点击"预览"时就 `HEAD` 上百个文件。

### 3.3 解压预估（零带宽）

对 `.gz` 文件发 `GET`，头 `Range: bytes=-4`，读最后 4 字节，按 little-endian uint32 解出
ISIZE = 解压后大小 mod 2³²。【实测】

    GSM7093919_kdm6bwt.loom.gz   bytes=-4 → 206, Content-Range: bytes 10159286-10159289/10159290
                                 ISIZE=12,613,264（本地 gunzip 实测 12,613,264 ✔）
    GSE72857_umitab.txt.gz       ISIZE=566,680,946（压缩 16.4 MB → 解压 540 MiB）

注意事项：
- 必须 `stream=True` 且只读 4 字节；若对端忽略 Range 返回 200，立刻放弃（否则会拉下 GB 级文件）。
- **ISIZE 是 mod 2³²**，>4 GiB 会回绕。本次测到的最大 gz 是 3.31 GB（未越过 4 GiB），
  因此回绕行为**未验证**。UI 上对 >4 GiB 的 gz 应显示"解压后 ≥ 或 未知"。
- `.tar` / `.h5ad` / `.rds`（未压缩）没有 ISIZE 可读：tar 用成员求和，其余等于原大小。

### 3.4 没有 filelist.txt 时（老 GSE）

GSE72857（2015 年提交）的 suppl 目录**没有** `filelist.txt`，
只有 2 个直链文件 —— 也就是说 `filelist.txt` 不是所有 GSE 都有。
此时：
1. 直链文件照常 `HEAD`；
2. 对 `GSE*_RAW.tar` 只能"流式扫 tar 头"：顺序读 512 字节头 → 解析 `name`/`size` →
   `seek` 跳过数据 → 读下一个头。**带宽成本等于整个 tar**，因此只应在
   用户明确要求预览归档内容时按需进行，不能作为自动预览的一部分。

---

## 4. GSM 层级怎么展开

### 4.1 一次请求拿全族（首选做法）

`GET …/soft/GSE227256_family.soft.gz` → gunzip → 纯文本 SOFT，包含：

    ^SERIES = GSE227256
    !Series_supplementary_file = ftp://…/GSE227256/suppl/GSE227256_RAW.tar
    ^SAMPLE = GSM7093919
    !Sample_title = WT scRNAseq
    !Sample_organism_ch1 = Mus musculus
    !Sample_supplementary_file_1 = ftp://…/samples/GSM7093nnn/GSM7093919/suppl/GSM7093919_kdm6bwt.loom.gz
    !Sample_series_id = GSE227256
    ^SAMPLE = GSM7093920
    !Sample_supplementary_file_1 = ftp://…/samples/GSM7093nnn/GSM7093920/suppl/GSM7093920_kdm6bko.loom.gz

实测体积：GSE227256 2,008 字节（2 样本）、GSE72857 5,888 字节（54 样本）、
GSE274591 3,758 字节（3 样本）、GSE84133 3,510 字节（6 样本）。
**54 个样本只花 5.9 KB，1 次请求**，比逐样本发 54 次 `acc.cgi` 好两个数量级。

### 4.2 样本文件名的键名规则（踩过的坑）

SOFT 里的键是 **带序号**的：`!Sample_supplementary_file_1`、`_2`、…、`_n`。

    GSE274591 的 3 个样本共有 14 条 !Sample_supplementary_file_N
    只读 _1 会只拿到 3 条，漏掉 11 条（matrix.mtx.gz / barcodes / features / loom）

正确做法：匹配 `^supplementary_file(_\d+)?$` 收集全部序号。
Series 层没有序号（`!Series_supplementary_file`，可重复出现多行）。

### 4.3 Series 级 vs Sample 级文件如何区分（判定规则）

**不要用文件名前缀猜**，用来源层级：

| 判据 | Series 级 | Sample 级 |
|---|---|---|
| SOFT 键 | `!Series_supplementary_file` | `!Sample_supplementary_file_N` |
| URL 路径 | `/geo/series/{STUB}/{GSE}/suppl/…` | `/geo/samples/{STUB}/{GSM}/suppl/…` |
| 文件名前缀 | 通常 `GSE…` | 通常 `GSM…`（但**不是**判定依据） |
| 所属记录 | GSE | GSM |

**关键反例**：本协议编写时（2026-10-26，读到的 `cellflow/publicdata.py` 第 245–257 行）
`_list_geo_supplementary()` 在目录列表里用 `name.startswith('GSE')` 过滤 ——
这会**系统性丢掉所有 GSM 开头的样本文件**，这正是"只能整包下载"的根因之一。
判定必须用 URL 路径/SOFT 键，不能用文件名前缀。
（注：该文件当时正被其他 P3 任务并行修改，此处结论仅对应上述行号处的版本；
若文件已改动请以当前代码为准。）

实测反例：GSE227256 的 Series 目录只有 `GSE227256_RAW.tar`，
两个 `GSM*_*.loom.gz` 全在 Sample 目录里。

### 4.4 归档的双路径

GSE227256 实测（同一逻辑文件两条路径，字节数完全一致）：

| 路径 | URL | 大小 | 代价 |
|---|---|---|---|
| 直链（推荐） | `…/samples/GSM7093nnn/GSM7093919/suppl/GSM7093919_kdm6bwt.loom.gz` | 10,159,290 | 只下 10 MB |
| 归档内 | `…/series/GSE227nnn/GSE227256/suppl/GSE227256_RAW.tar` | 20,080,640 | 要下 20 MB 再解包，还要写盘 |

GSE274591 把差距放大到 5.87 GiB（`GSE274591_RAW.tar` = 6,299,494,400）
对比单个 h5ad 的 7.5 MB。这就是"改成只下单个文件"最直接的收益。

### 4.5 边界情况

| 情况 | 实测/推断 | 处理 |
|---|---|---|
| 样本没有补充文件 | 实测 GSE72857 全部 54 个样本 `!Sample_supplementary_file_1 = NONE` | 解析时把 `NONE` 视为"无文件"，不要生成空条目或拼出坏 URL |
| 一个 GSM 属于多个 Series | 实测 GSE72857 的样本有 `!Sample_series_id` 两行（GSE72857 + GSE72859，SubSeries） | 归属列要允许一对多，URL 仍按 GSM 自身路径构造 |
| Sample 目录没有 filelist.txt | 实测 GSM7093919 目录只有 1 个文件、无清单 | 样本层大小只能靠 HEAD |
| Series 目录有 `filelist.txt` 但 SOFT 没列它 | 实测 GSE274591：目录 6 项，SOFT 只列 5 项 | 文件清单以"目录列表 ∪ SOFT 声明"取并集；`filelist.txt` 自身不要展示给用户当数据文件 |

---

## 5. 生物学元数据字段从哪来

### 5.1 可自动获得（SOFT 直接给）

| 字段 | SOFT 键 | GSE72857 实测值 | GSE274591 实测值 |
|---|---|---|---|
| 标题 | `!Series_title` | Transcriptional heterogeneity and lineage commitment in myeloid progenitors [single cell RNA-seq] | In situ pharmacological induction of pancreatic beta-cell regeneration by THR-123… |
| 物种 | `!Sample_organism_ch1`（汇总唯一值） | Mus musculus | Mus musculus |
| 组织 | `!Sample_characteristics_ch1` 里 `tissue:` / `organ:` | bone marrow | Pancreas |
| 技术类型 | `!Series_type` | Expression profiling by high throughput sequencing | 同左 |
| 平台 | `!Series_platform_id` | GPL19057 | GPL24247 |
| 仪器 | `!Sample_instrument_model` | NextSeq 500 | Illumina NovaSeq 6000 |
| 样本数 | `^SAMPLE` 段计数 / esummary `n_samples` | 54 | 3 |
| 提交日期 | `!Series_submission_date` | Sep 09 2015 | Aug 12 2024 |
| 公开日期 | `!Series_status` | Public on Nov 25 2015 | Public on Jun 12 2025 |
| PubMed | `!Series_pubmed_id` | 26627738 → https://pubmed.ncbi.nlm.nih.gov/26627738/ | 40610457 |
| BioProject | `!Series_relation` 里的 BioProject | PRJNA295200 | PRJNA1147257 |
| 摘要/设计 | `!Series_summary` / `!Series_overall_design` | 有 | 有 |

### 5.2 半自动（要解析文本或推断，准确率不保证）

| 字段 | 来源 | 风险 |
|---|---|---|
| 疾病/条件/基因型 | `!Sample_characteristics_ch1` 的 `disease:` `genotype:` `age:` `strain:` | 各提交者自定键名，词表不统一（实测 GSE227256 用 `strain: C57BL6`、`age: E18.5`，GSE72857 完全没有这些键）|
| 测序化学/版本 | `!Sample_description` / `!Sample_extract_protocol_ch1` | 自由文本，只能关键词命中（实测 GSM7093919 的 `description = "10X Genomics V3"`）|
| 是否含 spliced/unspliced | `!Sample_data_processing` 文本 | 实测 GSM7093919 写了 "loom files required for RNA velocity analysis"，可作为 metadata_supported 依据 |
| 组织（Series 级） | 汇总各样本 characteristics | Series 层本身没有 tissue 字段，只能从样本汇总 |

### 5.3 只能人工确认，不要自动填

| 字段 | 原因 |
|---|---|
| 细胞类型注释是否可信 | 来自作者 `obs.CellType` 等列，GEO 元数据里没有，只有下载后才能看到（GSE274591 h5ad 的 `obs` 有 `CellType` 列） |
| 是否原始计数 vs 归一化 | 需读矩阵判断；GEO 元数据不声明 |
| 文库化学的确切版本、是否含 intron | 自由文本，常有歧义 |
| 组织/发育阶段的标准化映射 | 需要 ontology 映射，不能靠字符串匹配直接落库 |
| 该文件是否适合速度分析 | 必须解析文件内部（见第 7 节） |

**产品建议**：预览页把 5.1 的字段显示为"已自动获取"，
5.2 显示为"来自作者填写，可能不准确"，5.3 一律留空或标"需人工确认"，
不要用模型猜测填进数据库。

---

## 6. 文件清单字段规范（对应项目书 4.4 表）

每一条文件记录（`probe.FileEntry`）固定 12 个字段：

| # | 字段（4.4） | 实现字段 | 取值来源 | 约束 |
|---|---|---|---|---|
| 1 | 文件名 | `name` | URL 末段 / filelist `Name` | 原样保留，不改写 |
| 2 | 来源 | `level` | `Series` / `Sample` / `ArchiveMember` | 由 URL 路径或 SOFT 键决定，不用文件名前缀 |
| 3 | 所属记录 | `owner` | `GSE…` / `GSM…` / 归档名 | 一对多时保留主记录，另存 `series_ids` |
| 4 | 类型 | `ftype` | 后缀规则 + filelist `Type` | filelist 的 `Type` 优先（结构化元数据） |
| 5 | 压缩大小 | `size_bytes` | HEAD `Content-Length` / filelist `Size` | 必须带 `size_source` 说明来源 |
| 6 | 解压预估 | `uncompressed_bytes` | gzip ISIZE / 归档成员求和 | 必须带 `uncompressed_method`；>4 GiB 未知 |
| 7 | 生物学内容 | `biological_content` | 类型 → 固定文案 | 只描述容器语义，不声称有 velocity |
| 8 | 剪接证据等级 | `splice_evidence` | 见第 7 节 | 四值枚举，禁止自由文本 |
| 9 | 推荐用途 | `recommended_use` | 类型 → 固定文案 | 例：h5ad→"直接读入 AnnData（首选）" |
| 10 | 下载状态 | `download_state` | `not_downloaded` / `downloading` / `done` / `failed` / `cancelled` | 单文件粒度，不按 Series 汇总 |
| 11 | 校验 | `checksum` | 默认 `not_computed` | GEO 不提供官方校验和，见下 |
| 12 | 元数据来源 | `metadata_source` | 例如 `SOFT !Sample_supplementary_file_1 (family.soft.gz)` / `filelist.txt(GSE…_RAW.tar)` | 必须能追溯到具体端点 |

补充内部字段（不展示给用户，但影响行为）：
`url`、`downloadable`、`member_of`、`alternative_route`、`note`、`splice_reason`。

**关于校验（第 11 项）**：NCBI GEO **不发布** MD5/SHA 清单（实测：`filelist.txt` 只有
`Name/Time/Size/Type`，没有校验列）。因此：
- 下载后用 `Content-Length`/`filelist Size` 做**长度校验**（能做，且必须做）；
- 完整性校验只能自算 SHA-256 存本地，供"再次下载同一文件"时比对；
- 不要把"长度对得上"说成"文件完整"。

**四种证据等级在清单里的呈现**（UI 建议颜色）：
`verified_by_content` 绿 · `metadata_supported` 蓝 · `filename_only` 橙（提示"未验证"）·
`absent` 灰。

---

## 7. 剪接证据的判定（verified / metadata_supported / filename_only / absent）

### 7.1 四级定义

| 等级 | 含义 | 判定条件 | 可否写"含 spliced/unspliced" |
|---|---|---|---|
| `verified_by_content` | 已解析文件内部并看到数据 | 容器里存在 `layers/spliced` **和** `layers/unspliced`（h5ad 看 `/layers`，loom 看 `/layers`） | **可以**，并可报维度 |
| `metadata_supported` | 结构化元数据支持，但没解析内容 | GEO `filelist.txt` 的 `Type` 列写着 `LOOM`，或 `!Sample_data_processing` 明确写了 loom/RNA velocity（参考实现目前只接了前者；后者作为可选增强） | 只能说"元数据表明是 loom/速度用途"，**不能说含哪两层** |
| `filename_only` | 只有文件名像 | 文件名含 `velocyto / spliced / unspliced / loom / velocity / bam` | 只能说"文件名提示"，必须标"未验证" |
| `absent` | 无证据，或已证否 | 无任何线索；**或解析后确认没有剪接层** | 不能 |

> **硬规则：只有真正解析过文件内部才能标 `verified_by_content`。**
> `absent` 有两种情况要区分并记录到 `splice_reason`：
> "从未解析" vs "解析过、确认没有"。后者是最强结论，不能和前者混为一谈。

### 7.2 判定流程

    文件名/metadata 有剪接线索？
      ├─ 否 → absent（reason: 无线索）
      └─ 是 → 用户是否已让本地解析该文件？
            ├─ 否 → 有结构化元数据（filelist Type=LOOM）？ 
            │        ├─ 是 → metadata_supported
            │        └─ 否 → filename_only
            └─ 是 → 解析 h5ad/loom：
                     ├─ layers 含 spliced+unspliced → verified_by_content
                     └─ 其他（空 layers / 无该层） → absent（reason: 已解析，layers 为空）

### 7.3 实测案例（这两个例子合起来就是分级的意义）

**负例 · GSE274591 · 文件名骗人**

    GSE274591_Velocyto_Reclustering_H2+Endoc_of_Integrated_Alloxan+THR.h5ad
      大小 7,561,996 字节（HEAD 与目录列表一致）
      probe kind      : h5ad
      probe layers    : []                    ← 空的
      probe obsm_keys : ['X_pca','X_umap']
      probe obs_keys  : ['CellType','UMAP_1','UMAP_2','barcode','integrated_snn_res.0.1',
                         'integrated_snn_res.0.3','nCount_RNA','nCount_SCT','nFeature_RNA',
                         'nFeature_SCT','orig.ident','percent.mt','seurat_clusters']
      判定：absent（已解析：layers 为空）

文件名里带 "Velocyto"，但容器里**没有任何 layer**。如果按文件名标"含速度信息"，
下游 RNA velocity 流程会在 Layers 为空处炸掉。

**正例 · GSE227256 / GSM7093919 · 真的含 velocity**

    GSM7093919_kdm6bwt.loom.gz
      大小 10,159,290 字节；解压后 12,613,264 字节
      probe kind       : loom
      probe layers     : ['ambiguous','spliced','unspliced']
      layer shapes     : {'ambiguous': [1554,12569], 'spliced': [1554,12569], 'unspliced': [1554,12569]}
      matrix_shape     : [1554, 12569]
      orientation      : genes x cells（rows=基因，需转置成 cells x genes）
      判定：verified_by_content

**方向坑（实测）**：velocyto 的 loom 是 **基因 × 细胞**（`row_attrs` 里是
`Accession/Chromosome/Gene/Start/End/Strand`，`col_attrs` 里是 `CellID`）。
两个实测样本都验证了这点：GSM7093919 `len(row_attrs.Gene)=1554 = matrix.shape[0]`、
`len(col_attrs.CellID)=12569 = matrix.shape[1]`；GSM8453300 也是
`32287 × 1756`。直接当 AnnData 用会得到转置的矩阵，必须按 `row_attrs/col_attrs`
判定方向后转置，**不能假设 loom 一定是 cells × genes**。

**第三个例子 · GSE274591 的 loom 在归档里**

    GSM8453300_New_Alloxan_possorted_genome_bam_CLF5P.loom.gz  31,920,351 字节
    GSM8453301_THR_possorted_genome_bam_SI7SF.loom.gz         452,212,690 字节

两者在 `filelist.txt` 里 `Type=LOOM` → `metadata_supported`（未解析）。
本次只实际下载并解析了 GSM8453300 那个（31.9 MB）：
`layers = ['ambiguous','spliced','unspliced']`、`matrix (32287, 1756)` → 内容上确认含剪接层，
但**协议上**这个文件在"未下载"状态下只能是 `metadata_supported`。
这正是"进度"与"证据等级"要分开记录的原因。

### 7.4 禁止事项

1. 禁止把 `filename_only` 渲染成"含 spliced/unspliced"。
2. 禁止用大模型/关键词猜剪接层后写进数据库。
3. 禁止把"GEO 的 suppfile 字段提到 LOOM"当作文件级证据（那是族级类型汇总）。
4. 禁止把 `absent` 的两种成因合并：必须能回答"这个文件我们到底看过没有"。

---

## 8. 协议时序（查询 → 预览 → 选单文件 → 下载）

    ① 用户输入 GSE
       GET acc.cgi?acc=GSE…&targ=self&form=text&view=brief     (或 family.soft.gz)
       → 标题/物种/组织/技术/样本数/提交日期/PubMed

    ② 预览（不下载数据）
       GET …/series/{STUB}/{GSE}/suppl/                 → 目录列表（兜底 + 发现 filelist.txt）
       GET …/series/{STUB}/{GSE}/suppl/filelist.txt     → 归档及其成员的精确大小/类型
       GET …/soft/{GSE}_family.soft.gz                  → 全部样本及其 !Sample_supplementary_file_N
       并发 HEAD（仅用户可见的前 N 条）                  → 精确字节数
       Range bytes=-4（.gz）                            → 解压预估
       → 一张 12 字段的清单，每条都带 size_source 与 evidence 等级

    ③ 用户勾选**一个**文件
       → 按 `同名文件优先直链，归档仅在无直链时使用` 规则选出唯一 URL
       → 显示：文件名、精确大小、解压预估、证据等级、预计耗时

    ④ 下载
       GET 该 URL（Range 断点续传）
       → 边下边校长度（Content-Length / filelist Size）
       → 落盘后可选：probe_container() 解析内部 → 需要时把证据等级升到 verified

    ⑤ 状态回写
       下载状态是**单文件**粒度；不能因为 Series 里其它文件没下而显示"未下载"。

**绝不允许的行为**：用户选定 `GSM7093919_kdm6bwt.loom.gz` 时去下
`GSE227256_RAW.tar`（多花 10 MB 且要解包），或在 GSE274591 里下
`GSE274591_RAW.tar`（多花 5.87 GiB）。

---

## 9. 请求礼貌与性能

| 项 | 取值 | 依据 |
|---|---|---|
| User-Agent | 必带，含产品名与联系方式 | NCBI 要求可识别 UA，未带会被限速/封禁 |
| 超时 | 元数据 `(10,40)`；下载 `(10,180)` | 实测元数据请求 0.4–0.9 s |
| 重试 | 最多 3 次，指数退避 0.6 s | 不上无限重试 |
| 并发 | HEAD 并发 8 | 实测 GSE274591 19 个文件全程 8.3 s |
| E-utilities 频率 | 不给 key 时 ≤3 请求/秒（官方文档） | 本次实测 ~30 次请求未触发限速，但配额是"文档推断" |
| 大文件 | 一律 `stream=True` 或 `HEAD`，禁止一次性 `resp.content` | 实测教训：早期版本用 GET 取大小，遇到 GSE274591 的 6.3 GB tar 时整个探针挂到超时；改成 HEAD 后同一次查询 8.3 s 完成 |

本次实测的请求量（用计数器统计，含 HEAD 与 Range）：

     gse GSE72857    2 条记录  6 次请求（HEAD 2 + GET 4）  2.1 s
     gse GSE227256   5 条记录  8 次请求（HEAD 3 + GET 5）  2.9 s
     gse GSE274591  33 条记录 39 次请求（HEAD 19 + GET 20）8.3 s

全部带 UA、全部有超时与有限重试。同一个 GSE 的 family SOFT 只取一次（进程内缓存），
否则 series_metadata 与样本展开会各取一次。

---

## 10. 证据附录（全部为实际输出，原文留档在 `data/_probe/evidence/`）

### 10.1 GSE72857（已知可用，2 个补充文件）

    $ python tools/p3_probe/geo_probe.py gse GSE72857
    == GSE72857  Transcriptional heterogeneity and lineage commitment in myeloid progenitors [single cell RNA-seq]
       物种      : Mus musculus
       组织      : bone marrow
       技术      : Expression profiling by high throughput sequencing  (平台 GPL19057)
       样本数    : 54
       提交日期  : Sep 09 2015   公开: Public on Nov 25 2015
       PubMed    : https://pubmed.ncbi.nlm.nih.gov/26627738/
       BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA295200

    == 文件清单（2 项）
       层级     所属        类型        字节      解压后      证据等级  文件名
       Series   GSE72857    txt      115,876     700,632    absent   GSE72857_experimental_design.txt.gz
       Series   GSE72857    txt   16,413,252 566,680,946    absent   GSE72857_umitab.txt.gz

    Series 级 2 个 / Sample 级 0 个 / 归档成员 0 个

逐文件 HEAD 复核（独立命令）：

    GSE72857_experimental_design.txt.gz  HEAD 200 CL=115876     （目录列表显示 113K）
    GSE72857_umitab.txt.gz               HEAD 200 CL=16413252   （目录列表显示 16M）

结论：两个文件、真实字节数 115,876 与 16,413,252，解压后 700,632 与 566,680,946
（540 MiB 的 UMI 表藏在 16.4 MB 的 gz 里，这个数字是靠 4 字节 Range 拿到的）；
54 个样本本身**没有**样本级补充文件（全部 `!Sample_supplementary_file_1 = NONE`），
数据都在 Series 层。

### 10.2 GSE227256 / GSM7093919（loom.gz）

    $ python tools/p3_probe/geo_probe.py gse GSE227256
    == 文件清单（5 项）
       Series         GSE227256   tar       20,080,640        -  absent               GSE227256_RAW.tar
       Sample         GSM7093919  loom      10,159,290 12,613,264 metadata_supported   GSM7093919_kdm6bwt.loom.gz
       Sample         GSM7093920  loom       9,910,390 12,146,609 metadata_supported   GSM7093920_kdm6bko.loom.gz
       ArchiveMember  GSE227256   loom      10,159,290        -  metadata_supported  *GSM7093919_kdm6bwt.loom.gz
       ArchiveMember  GSE227256   loom       9,910,390        -  metadata_supported  *GSM7093920_kdm6bko.loom.gz
       Series 级 1 个 / Sample 级 2 个 / 归档成员 2 个
       同一文件多条路径：2 组

文件名 + 大小：`GSM7093919_kdm6bwt.loom.gz` = 10,159,290 字节（解压后 12,613,264）；
`GSM7093920_kdm6bko.loom.gz` = 9,910,390 字节。已下载前者（10.2 MB）并解析，
证明 `layers = ['ambiguous','spliced','unspliced']`（见 7.3 正例）。

### 10.3 GSE274591（负例：文件名含 Velocyto 但 layers 为空）

    $ python tools/p3_probe/geo_probe.py verify data/_probe/GSE274591_velocyto_h2endoc.h5ad
    == 内容探针 …\GSE274591_velocyto_h2endoc.h5ad
       kind          h5ad
       layers        []
       obsm_keys     ['X_pca', 'X_umap']
       剪接证据等级 : absent
       判定理由     : 已解析容器内部：layers 为空

该文件 7,561,996 字节，已实际下载并解析 —— **不是"未验证"，是已验证为空**。

### 10.4 多文件大 GSE：不下载任何文件列全清单

    $ python tools/p3_probe/geo_probe.py gse GSE274591     （耗时 8.3 秒，39 次请求）
    系列：5 条 · 样本级 14 条 · 归档成员 14 条 · 唯一文件 19 个
    Series 级 5 个：
      GSE274591_RAW.tar                              6,299,494,400  (5.87 GiB)
      GSE274591_Integrated-Alloxan+THR.rds.gz          492,460,648  → 解压 498,967,411
      GSE274591_Reclustering_of_Cluster_1_…UMAP.rds.gz   4,381,643  → 解压   4,418,865
      GSE274591_Reclustering_of_H2+Endoc_of_…rds.gz      8,696,778  → 解压   8,827,779
      GSE274591_Velocyto_Reclustering_…h5ad              7,561,996
    Sample 级 14 个（节选）：
      GSM8453301_THR-123.rds.gz                      3,308,073,854  → 解压 3,310,679,405
      GSM8453299_Control_ND_.rds.gz                  1,737,167,207  → 解压 1,738,589,010
      GSM8453301_THR_matrix.mtx.gz                     367,174,623  → 解压 1,300,995,213
      GSM8453300_…_CLF5P.loom.gz                        31,920,351  → 解压    38,478,401
      GSM8453301_…_SI7SF.loom.gz                       452,212,690  → 解压   491,404,299

如果按旧行为"下载整个 Series"：合计 **13,112,075,772 字节（12.21 GiB）**。
而用户真正需要的单个 h5ad 是 **7,561,996 字节（7.2 MiB）** —— 差 1734 倍。

这 19 个文件的名称与字节数全部在没有下载任何数据文件的前提下取得
（只用了：目录列表、`filelist.txt`、family SOFT、HEAD、Range 4 字节）。

### 10.5 检索：GDS UID 不是 GSE 号

    $ python tools/p3_probe/geo_probe.py search "pancreatic islet single cell RNA-seq" --max 6
    == 检索命中 6 个 GSE
       GSE349522   Mus musculus  n=4   2026/10/11  Transcriptional and Functional Profiling …
       GSE335464   Homo sapiens  n=28  2026/06/16  Integrative single-cell multi-omics profiling …
       GSE330587   Mus musculus  n=17  2026/05/11  Pax4 R192H variant impairs β cell function …
       …

反证（同一次实测的另一条查询）：

    $ esearch db=gds term=GSE72857[ACCN]
    count=56  idlist=['200072857','100019057','301944947']
    uid=200072857 → accession=GSE72857   entrytype=GSE
    uid=100019057 → accession=GPL19057   entrytype=GPL
    uid=301944947 → accession=GSM1944947 entrytype=GSM

**把 UID 当 accession 就会显示成"GSE200072857"这类不存在的编号。**

另一个实测坑：给关键词硬加 `[All Fields]` 会让带连字符的短语失效：

    'pancreatic islet single cell RNA-seq'             → count=1042
    'pancreatic islet single cell RNA-seq[All Fields]' → count=0

---

## 11. 结论分级：已实测 / 文档推断 / 未验证

### 11.1 已实测（有命令输出，可直接作为实现依据）

| # | 结论 |
|---|---|
| 1 | 目录列表 `Size` 是四舍五入值；`HEAD Content-Length` 与 `filelist.txt Size` 才是精确字节 |
| 2 | `filelist.txt` 精确列出归档成员（Name/Time/Size/Type），与 HEAD 交叉一致 |
| 3 | `esearch(db=gds)` 的 UID ≠ accession；一次 accession 查询同时命中 GSE/GPL/GSM |
| 4 | `esummary.suppfile` 只是族级类型集合，无文件名/大小/归属 |
| 5 | `GSE*_family.soft.gz` 2–6 KB 即含全部样本与 `!Sample_supplementary_file_N` 直链 |
| 6 | `targ=all` 返回 11–32 MB，不适合预览 |
| 7 | `acc.cgi` 多编号（逗号）查询无效（`^SAMPLE`=0） |
| 8 | Series 目录 ≠ 样本文件全集；样本文件在 `/geo/samples/…` |
| 9 | 同一文件同时存在 Sample 直链与 RAW.tar 成员，且字节数一致 |
| 10 | `Range: bytes=-4` 取 gzip ISIZE = 解压后字节数（本地解压对账一致） |
| 11 | Sample 目录下只有 `suppl/`，没有 soft/miniml/matrix |
| 12 | `acc.cgi` 需强制 UTF-8 解码，否则 `geo/text` 出乱码 |
| 13 | 样本补充文件键带序号 `_1…_n`，只读 `_1` 会漏文件（GSE274591 漏 11/14） |
| 14 | GSE274591 的 "Velocyto" h5ad 实际 `layers=[]`（已验证为空） |
| 15 | GSM7093919/GSM8453300 的 loom 实测含 `spliced/unspliced/ambiguous` |
| 16 | velocyto loom 是 基因×细胞（`row_attrs=Gene`、`col_attrs=CellID`） |
| 17 | 无 `filelist.txt` 的老 GSE 存在（GSE72857，2015 年） |
| 18 | GSE72857 全部 54 个样本声明 `supplementary_file_1 = NONE` |
| 19 | 样本可同时属于多个 Series（GSE72857 + GSE72859，SubSeries） |
| 20 | GSE274591 全清单 19 个文件可在 8.3 s / 39 次请求内零下载列出 |

### 11.2 依官方文档/接口语义推断（本次未逐条实测）

| # | 推断 | 依据 | 风险 |
|---|---|---|---|
| A | E-utilities 无 key 时限 3 请求/秒、10 请求/秒带 key | NCBI E-utilities 文档 | 未触发过限速，具体阈值未测 |
| B | `ftp://` 协议路径与 HTTPS 路径同构 | NCBI 文档说明 FTP 与 HTTPS 镜像同内容 | 本次**只测了 HTTPS**，未测 FTP 传输 |
| C | `filelist.txt` 是 GEO 生成的权威清单 | 文件名/格式/与 HEAD 一致 | 生成时机与是否全量未确认（见 11.3） |
| D | `member_of` 归档里的成员总能导出成相同字节数 | 同一文件两条路径字节数一致（实测 3 例） | 未做大样本统计 |
| E | `suppfile` 字段是族级类型汇总 | 字段值与实际类型集合吻合 | 语义未见官方明确定义 |

### 11.3 未验证（不得当成已知事实）

| # | 未验证项 | 说明 |
|---|---|---|
| U1 | gzip ISIZE 在 >4 GiB 时的回绕行为 | 本次最大 gz 为 3.31 GB，未越过 4 GiB 边界 |
| U2 | `filelist.txt` 是否对所有 GSE 存在/是否完整 | 4 个 GSE 中 3 个有、GSE72857 无；未见官方说明其生成条件 |
| U3 | 归档成员是否 100% 都能在 Sample 层找到同名直链 | 实测 3 个 GSE 全部成立；未验证反例（如非 GSM 命名的归档成员） |
| U4 | 超大 Series（>1000 样本）的 preview 耗时 | 未测；54 样本时 family SOFT 仅 5.9 KB，规模效应可能仍温和 |
| U5 | 已被撤回/删除的 GSE 的端点行为 | 未测 |
| U6 | 403/429 的具体触发阈值与恢复时间 | 未触发过 |
| U7 | `miniml/GSE*_family.xml.tgz` 是否含与 SOFT 完全等价的 supplementary 字段 | 只验了 200 与 6,201 字节，未解析 XML 内容 |
| U8 | CDN/镜像是否对 `HEAD` 有差异 | 只测了 `ftp.ncbi.nlm.nih.gov` 官方源 |
| U9 | 归档内 `.gz` 成员的 ISIZE | 成员无独立 URL，无法用 Range 取尾部；只能解包后读（未做） |
| U10 | 当前产品代码 `publicdata.py` 的实际行为是否与本文结论有冲突点 | 只读了源码（编写时版本：`_list_geo_supplementary` 用 `startswith('GSE')` 过滤；检索侧当时已有人加注"UID 仅作调试用"）。该文件正被并行任务修改，**未运行产品链路**验证，落地前需以当时代码复核 |

---

## 12. 给实现方的落地清单（按优先级）

1. 文件清单生成改为：目录列表 ∪ `filelist.txt` ∪ family SOFT 的
   `!Series_supplementary_file` 与 `!Sample_supplementary_file_N`，取并集去重。
2. 大小一律走 `HEAD`（+ `Range` 降级），显示时标注来源；归档成员用 `filelist.txt`。
3. 删掉一切 `startswith('GSE')` 之类的文件名前缀过滤，改用 URL 路径/SOFT 键判定层级。
4. 检索结果读 `esummary.accession` 且过滤 `entrytype=='GSE'`。
5. 样本展开用 `family.soft.gz`（1 次请求），不要逐样本循环 `acc.cgi`。
6. 预览阶段禁止 `HEAD` 全部文件；先按需取前 N 条。
7. 下载粒度 = 单个文件；同名文件优先直链；下载前把"预计大小/解压大小"告知用户。
8. 剪接证据等级严格按第 7 节四值枚举；`filename_only` 必须在 UI 上可见为"未验证"。
9. 校验只做长度校验 + 自算 SHA-256，不要宣称"官方校验通过"。
10. `absent` 的 `splice_reason` 必须区分"未解析"与"已解析确认没有"。
