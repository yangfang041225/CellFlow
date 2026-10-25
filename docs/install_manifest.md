# 安装与环境 Manifest（F34）

更新时间：2026-10-25。对应 `install.py --check` 与 `docs/COMPREHENSIVE_AUDIT_REPORT.md` 的 F34。

## 1. 它解决什么问题

旧版 `--check` 只看「解释器文件存在 + 包能 `find_spec`」。这会把两种情况误判成可用：

- 解释器在、包名能查到，但 **真实 import 失败**（DLL 缺失、版本不兼容、半装状态）；
- 隔离环境（`.venv-velovi`）只有 `python.exe` 和 lock 文件，但 `scvi` / `torch` 实际不可导入。

现在 `--check` 会**用每个环境自己的解释器真实 import 并读取发行版版本**，任何缺失或版本不符都记为 `not_ready`；
最后输出一行机器可读的 `MANIFEST_JSON:`，包含产品版本、Python、每个包的版本、检查时间和整体判定。

`--check` **只读**：不安装、不改项目文件、不改环境（第三方库自己可能写用户级缓存，与本项目无关）。

## 2. 命令与输出

```powershell
python install.py --check
```

退出码：`0` = 整体 ready；`1` = 核心环境或已存在的隔离环境不健康。

人类可读输出示例（本机 2026-10-25 实测，节选）：

```text
[OK] .venv: scanpy 1.11.5、scvelo 0.3.4、anndata 0.12.19、numpy 2.2.6、scipy 1.15.3、...
[OK] .venv-velovi: scvi 1.2.0、torch 2.5.1+cpu、scanpy 1.11.5、scvelo 0.3.4、...
[NOT_INSTALLED] .venv-regvelo: 可选环境未安装（不阻塞核心检查）
[OK] web/dist/index.html
[OK] cellflow_app.pyw
MANIFEST_JSON:{"schema": 1, "product": "CellFlow", "product_version": "0.2.0", ...}
ENVIRONMENT_CHECK_OK
```

保存 manifest（JSON 在同一行，可直接从日志里提取）：

```powershell
python install.py --check | Tee-Object -FilePath logs\install_check.log
Select-String '^MANIFEST_JSON:' logs\install_check.log | ForEach-Object { $_.Line.Substring(14) } |
  Set-Content -Encoding UTF8 logs\install_manifest.json
```

### manifest 字段

| 字段 | 含义 |
|---|---|
| `schema` | manifest 结构版本（当前 `1`） |
| `product` / `product_version` | 产品名与 `cellflow/__init__.py` 的 `__version__` |
| `generated_at` | 检查时间（带本机时区偏移） |
| `checker_python` | 执行 `install.py` 的解释器及其版本（不是被检查环境的版本） |
| `platform` | `platform.platform()` |
| `environments.<folder>.python` | 被检查环境的解释器路径 |
| `environments.<folder>.python_version` | 该解释器里 `platform.python_version()` 的结果 |
| `environments.<folder>.status` | `ready` / `not_ready` / `not_installed` |
| `environments.<folder>.optional` | 是否为可选隔离环境 |
| `environments.<folder>.packages` | 每个模块的 `distribution` / `required` / `installed` / `ok` / `error` |
| `environments.<folder>.problems` | 该环境不 ready 的中文原因 |
| `assets` | 启动所需资源是否存在（`web/dist/index.html`、`cellflow_app.pyw`） |
| `ready` / `problems` | 整体判定与汇总原因 |

## 3. 判定规则

| 情况 | 该环境状态 | 整体 `ready` |
|---|---|---|
| `.venv` 解释器不存在 | `not_installed` | **否** |
| `.venv` 里某个必需包 import 失败 | `not_ready` | **否** |
| `.venv` 里版本不满足约束（如 scanpy 1.9） | `not_ready` | **否** |
| 探测进程超时/崩溃/输出无法解析 | `not_ready` | **否** |
| `.venv-velovi` 不存在 | `not_installed`（optional） | 是（只提示，不阻塞核心） |
| `.venv-velovi` 存在但 `scvi`/`torch` 缺失或版本不符 | `not_ready`（optional） | **否**（防止“有 exe 就算就绪”） |
| `.venv-regvelo` 存在但依赖不完整 | `not_ready`（optional） | **否** |
| `web/dist/index.html` 或 `cellflow_app.pyw` 缺失 | — | **否** |

必需包清单写在 `install.py` 的 `REQUIRED_PACKAGES`，与 `requirements*.txt` 对齐；版本约束按数字段比较，
`torch 2.5.1+cpu` 按 `2.5.1` 处理。未在强制清单里的可选能力（CellRank、pypdf 等）不会被 `--check` 要求；
隔离环境（veloVI / RegVelo）不存在只提示，存在但 import/版本不健康会判失败。

## 4. 可重复安装输入

安装不是“随便 pip 一下”，输入固定为：

| 输入 | 作用 |
|---|---|
| Python **3.11.15**（uv `--managed-python` 安装到 `.runtimes/`） | 主环境与隔离环境的解释器 |
| `requirements.txt` | 主环境：界面、scanpy/scVelo、FastAPI、PySide6 等 |
| `requirements-cellrank.txt` | 可选：CellRank 2.0.7（Python 3.11 的最后支持版） |
| `requirements-velovi.txt` | 隔离 `.venv-velovi`：scvi-tools 1.2.0 + numpy<2 |
| `requirements-regvelo.txt` | 可选 `.venv-regvelo`：RegVelo 0.4.2 + CPU PyTorch |
| `https://download.pytorch.org/whl/cpu` | CPU 版 PyTorch 2.5.1（不装 CUDA） |
| `environment-venv.lock.txt` / `environment-venv-velovi.lock.txt` | **安装后** `uv pip freeze` 的完整快照 |
| `installation.json` | 每次安装的阶段、返回码、耗时 |

### 复现命令（干净目录）

```powershell
# 1) 主环境（CPU 基线）
python install.py

# 2) 可选：CellRank 2（下游命运概率，纯 CPU）
python install.py --include-cellrank

# 3) 可选：veloVI 独立环境（scvi-tools + CPU PyTorch）
python install.py --include-velovi

# 4) 健康检查：真实 import + 版本 + manifest
python install.py --check
```

### 精确复现已有环境（使用 lock 快照）

`install.py` 默认按 `requirements*.txt` 的**版本区间**安装，lock 文件是安装后的快照，不是安装输入。
如果要逐包复现本机快照，直接重放 lock：

```powershell
uv pip install --python .venv\Scripts\python.exe -r environment-venv.lock.txt
uv pip install --python .venv-velovi\Scripts\python.exe -r environment-venv-velovi.lock.txt
```

注意：lock 里包含平台相关的 wheel 版本（本机 Windows / CPython 3.11）；跨平台复现需要重新解析，
不能把本机 lock 当作跨平台锁。

## 5. 回滚

`install.py` 不修改系统 Python，也不写注册表；回滚只需删环境目录后重装：

```powershell
# 停止正在运行的任务与窗口后执行
Remove-Item -Recurse -Force .venv
Remove-Item -Recurse -Force .venv-velovi   # 如装过
python install.py
python install.py --check
```

- `data/` 与 `results/` 不会被 `install.py` 改动；删环境不会删数据或结果。
- 想回到某个已验证版本：保留对应的 `environment-venv*.lock.txt`，按上节命令重放。
- 安装失败时先看 `installation.json`（哪个阶段失败）和终端输出；`--check` 不负责修复。

## 6. 本次实测

2026-10-25 20:31，用系统 Python 3.12.8 执行 `python install.py --check`（被检查环境是 `.venv` 的 3.11.15）：

| 环境 | 状态 | 关键版本 |
|---|---|---|
| `.venv` | `ready` | scanpy 1.11.5、scvelo 0.3.4、anndata 0.12.19、numpy 2.2.6、scipy 1.15.3、pandas 2.3.3、igraph 0.11.9、fastapi 0.162.0、uvicorn 0.54.0、python-multipart 0.0.32、markdown 3.11.2、streamlit 1.66.1、plotly 6.9.0、PySide6 6.12.0 |
| `.venv-velovi` | `ready` | scvi-tools 1.2.0、torch 2.5.1+cpu、scanpy 1.11.5、scvelo 0.3.4、anndata 0.11.4、numpy 1.26.4 |
| `.venv-regvelo` | `not_installed`（可选） | — |
| 资源 | `ready` | `web/dist/index.html`、`cellflow_app.pyw` 均存在 |

整体：`ready = true`，`ENVIRONMENT_CHECK_OK`，退出码 0。

## 7. 边界与仍未完成

1. **运行时准入尚未消费 manifest**：`cellflow/registry.py` 判断隔离环境仍看「解释器 + lock 文件」，
   本次改动只覆盖 `install.py --check`。要让界面也拒绝“环境损坏但 exe 还在”的任务，需要后续把 manifest/真实 import
   接进 registry（本轮不修改 `cellflow/`）。
2. **lock 不是安装输入**：安装按 requirements 区间解析，lock 是事后快照；精确复现需按第 4 节重放 lock。
3. **测试专用依赖**：`pypdf` 只用于 PDF 断言，不属于产品运行依赖，未列入 `requirements.txt`；
   CI 显式安装 `pypdf==6.21.2`，见 `docs/test_report.md` 的已知问题。
4. **可选能力未强制**：CellRank、pypdf 不在 `REQUIRED_PACKAGES`；`--check` 只保证核心运行时契约。
5. **未验证**：`--check` 不验证 GPU/CUDA、不验证模型能否在具体数据上跑通；CI 尚未在 GitHub Actions 上实际执行过。
