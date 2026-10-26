# P3-00 基线审计：分支与 PR 拓扑

- 生成时间：2026-10-26T08:00:56+08:00
- 仓库：C:\Users\mtgmc\Documents\deepseek-harness\default-workspace\CellFlow-hotfix（origin = yangfang041225/CellFlow）

## 1. 远端分支（git ls-remote --heads origin）

| 远端分支 | 远端 SHA | 本地同名分支 SHA | 本地提交时间 |
|---|---|---|---|
| audit/phase2-review-20261026 | a43ae48 | - | - |
| fix/scientific-contracts-and-acceptance | 4fcb61f | - | - |
| hotfix/public-data-download-connectivity | f688fc7 | f688fc7 | 2026-10-26 05:31:04 |
| main | ecf0bc5 | ecf0bc5 | 2026-10-25 22:52:57 |
| phase2/scientific-validation | 6b4c001 | - | - |
| phase3/cytelle-rebrand-and-validation | a43ae48 | - | - |

- p3/batch1-data-acquisition **只在本地存在，尚未推送到 origin**（远端分支列表里没有它），因此 CI 和协作者目前看不到 P3 分支。
- 远端 phase3/cytelle-rebrand-and-validation 与 audit/phase2-review-20261026 指向同一个提交（a43ae48），说明 phase3 分支是从 audit 提交拉出的，不含下载 hotfix。

## 2. 四个 PR（gh pr list --state all）

| PR | 状态 | base | head 分支 | head SHA | 文件 | 增/删 | 提交数 |
|---|---|---|---|---|---|---|---|
| #1 | OPEN（DRAFT） | main | fix/scientific-contracts-and-acceptance | 4fcb61f | 32 | +1063/−160 | 5 |
| #2 | OPEN（DRAFT） | fix/scientific-contracts-and-acceptance | phase2/scientific-validation | 6b4c001 | 33 | +2794/−18 | 13 |
| #3 | OPEN（DRAFT） | phase2/scientific-validation | audit/phase2-review-20261026 | a43ae48 | 42 | +1305/−461 | 1 |
| #4 | OPEN（DRAFT） | main | hotfix/public-data-download-connectivity | f688fc7 | 11 | +1244/−99 | 2 |

### PR #1 — fix: enforce scientific data and process safety contracts

- URL：https://github.com/yangfang041225/CellFlow/pull/1
- 状态：OPEN（草稿）；mergeable=MERGEABLE；mergeStateStatus=CLEAN
- base：main；head：fix/scientific-contracts-and-acceptance @ 4fcb61f5bf40666f9ebf73d512b95cde7de03edf
- 规模：32 个文件、+1063/−160、5 个提交

### PR #2 — Phase II: scientific validation benchmarks, evidence levels, import confirmation

- URL：https://github.com/yangfang041225/CellFlow/pull/2
- 状态：OPEN（草稿）；mergeable=MERGEABLE；mergeStateStatus=CLEAN
- base：fix/scientific-contracts-and-acceptance；head：phase2/scientific-validation @ 6b4c0011817773768b707dc647c9bcbd5baa3fe2
- 规模：33 个文件、+2794/−18、13 个提交

### PR #3 — Phase II independent review: verify evidence, preserve coverage and bind imports

- URL：https://github.com/yangfang041225/CellFlow/pull/3
- 状态：OPEN（草稿）；mergeable=MERGEABLE；mergeStateStatus=CLEAN
- base：phase2/scientific-validation；head：audit/phase2-review-20261026 @ a43ae4861adb8757d7473faccde642c2dbfd0276
- 规模：42 个文件、+1305/−461、1 个提交

### PR #4 — fix(public-data): 公开数据下载 Failed to fetch 与幽灵任务修复

- URL：https://github.com/yangfang041225/CellFlow/pull/4
- 状态：OPEN（草稿）；mergeable=MERGEABLE；mergeStateStatus=CLEAN
- base：main；head：hotfix/public-data-download-connectivity @ f688fc70a6296575da390942d456c688c837f474
- 规模：11 个文件、+1244/−99、2 个提交

## 3. 合并关系（merge-base 与提交计数）

| 分支 | HEAD | 与 main 的 merge-base | 领先 main 提交 | 落后 main 提交 | 变更文件 | git diff --shortstat main...分支 |
|---|---|---|---|---|---|---|
| main | ecf0bc5 | ecf0bc58ec3a | 0 | 0 | 0 |  |
| hotfix | f688fc7 | ecf0bc58ec3a | 2 | 0 | 11 | 11 files changed, 1244 insertions(+), 99 deletions(-) |
| audit | a43ae48 | ecf0bc58ec3a | 19 | 0 | 72 | 72 files changed, 4731 insertions(+), 208 deletions(-) |
| p3 | 0cfed72 | ecf0bc58ec3a | 3 | 0 | 22 | 22 files changed, 5399 insertions(+), 150 deletions(-) |

两两关系：

- origin/main ↔ origin/hotfix/public-data-download-connectivity：merge-base ecf0bc58ec3a，各自独有提交 0 / 2
- origin/main ↔ origin/audit/phase2-review-20261026：merge-base ecf0bc58ec3a，各自独有提交 0 / 19
- origin/main ↔ HEAD：merge-base ecf0bc58ec3a，各自独有提交 0 / 3
- origin/hotfix/public-data-download-connectivity ↔ origin/audit/phase2-review-20261026：merge-base ecf0bc58ec3a，各自独有提交 2 / 19
- origin/hotfix/public-data-download-connectivity ↔ HEAD：merge-base f688fc70a629，各自独有提交 0 / 1
- origin/audit/phase2-review-20261026 ↔ HEAD：merge-base ecf0bc58ec3a，各自独有提交 19 / 3

关键祖先判断（git merge-base --is-ancestor）：

- hotfix 的修复提交 1ab49e0 是否已在 audit 分支里：**否**
- audit HEAD 是否就是 phase3 分支 HEAD：**是**
- hotfix HEAD 是否是当前 P3 分支（HEAD）的祖先：**是**
- main 是否是当前 HEAD 的祖先：**是**

两条线改动的**重叠文件**（决定合并冲突面，由 diff --name-only 集合求交）：

- web/package.json
- web/src/services/api.ts
- web/src/views/Data.tsx

hotfix 独有的代码文件（audit 分支上不存在）：cellflow/downloads.py、web/src/services/download-monitor.ts、web/src/services/net.ts、web/tests/net.test.mjs、web/tsconfig.test.json

## 4. 结论：P3 应以哪个 commit 为基线

**建议：以 hotfix 线的 tip f688fc7 为 P3 集成基线（当前 P3 分支 HEAD 是在它之上再走 1 个提交：0cfed72）；并且必须在继续写 P3 代码前，把 a43ae48（audit/phase2-review-20261026）合并一次进 P3 分支。**

一句话答案：**P3 继续开发的基线是 0cfed72（当前 P3 分支 HEAD，= hotfix tip f688fc7 + 1 个 P3 提交）；要让基线同时包含契约/证据层，必须先合入 a43ae48 再继续。**

可核查的理由：

1. 下载修复只在 hotfix 线上。ancestry 检查显示 1ab49e0 在 audit 分支里：**否**；web/src/services/net.ts、web/src/services/download-monitor.ts、tests/test_download_hotfix.py、web/tests/net.test.mjs 只存在于 hotfix 树。P3 batch1 的主题就是数据获取，从 audit 起手会丢掉已验收的修复。
2. 契约/证据层只在 audit 线上：audit 领先 main 19 个提交、72 个变更文件（72 files changed, 4731 insertions(+), 208 deletions(-)）；hotfix 只有 2 个提交。两者都不能省。
3. 两条线真正的分叉点是 main ecf0bc58ec3a：彼此不包含（各自独有提交 2、0），重叠改动只有 3 个文件（web/package.json、web/src/services/api.ts、web/src/views/Data.tsx），冲突面可控。
4. P3 分支已经建在 hotfix tip 上，改用 audit 起手会让已有分支作废；而 audit 的 tip 已经被远端 phase3/cytelle-rebrand-and-validation 占用，把 P3 建在那里会把 phase2 审计与 phase3 品牌工作混到同一条线上。
5. main 至今停在 ecf0bc58ec3a，PR #1–#4 全部是 DRAFT、一个都没合并；所以“以 main 为基线”会同时丢掉上面两层，不成立。

不合并 audit 就开工，等于显式接受这些风险：

- P3 产出的证据/导入/进程代码会与 PR #1–#3 的契约重复实现或直接冲突，评审时无法判定先后；
- 重叠的前端文件会在最后合并时必然冲突，且冲突点正落在 P3 要改的下载 UI 上；
- audit 线上的 benchmarks/、cellflow/evidence.py 等在 P3 分支上不存在，任何引用它们的验收脚本在 P3 分支上会直接失败。

执行建议（由 Lead 决策；本审计不做任何 git 写操作）：

    # 在 p3/batch1-data-acquisition 上合并契约/审计线；产生的 merge commit 就是 P3 真实基线
    git merge --no-ff origin/audit/phase2-review-20261026
    # 预计冲突：web/package.json、web/src/services/api.ts、web/src/views/Data.tsx

无论是否合并，P3 报告里的「基线提交」都应写 40 位 SHA，不要只写分支名——本仓库的 main 就停在 ecf0bc58ec3a31ea59d1c6a379aad5caff0ffda1 没动。

## 5. 各分支相对 main 的差异规模

| 分支 | HEAD | 变更文件数 | shortstat | 变更文件样例 |
|---|---|---|---|---|
| main | ecf0bc5 | 0 | - | - |
| hotfix | f688fc7 | 11 | 11 files changed, 1244 insertions(+), 99 deletions(-) | .gitignore、cellflow/downloads.py、docs/hotfix-2026-10-26-public-data-download.md、tests/test_download_hotfix.py … |
| audit | a43ae48 | 72 | 72 files changed, 4731 insertions(+), 208 deletions(-) | README.md、benchmarks/__init__.py、benchmarks/catalog.py、benchmarks/compare_cellrank.py … |
| p3 | 0cfed72 | 22 | 22 files changed, 5399 insertions(+), 150 deletions(-) | .gitignore、cellflow/api.py、cellflow/downloads.py、cellflow/importers.py … |
