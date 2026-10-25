"""安装 CPU 优先的独立环境；不会改动系统 Python。

用法（在本目录打开 PowerShell）：
    python install.py --check                # 只读健康检查：真实 import + 版本，输出结构化 manifest
    python install.py                        # 基础环境：界面 + 质控/轨迹/速度（推荐先跑这个）
    python install.py --include-cellrank     # 额外装 CellRank 2（下游命运分析，纯 CPU，体积小）
    python install.py --include-regvelo      # 可选：RegVelo 独立 CPU 神经环境（下载很大，本机不推荐）

国内网络慢时只对本次进程换镜像：
    $env:UV_DEFAULT_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    python install.py

`--check` 不安装、不改项目文件：在目标解释器里逐个真实 import 并读版本，
缺包 / 版本不符 / 解释器损坏都判为 not_ready；最后打印 `MANIFEST_JSON:` 一行
（产品版本、Python、包版本、检查时间）和 `ENVIRONMENT_CHECK_OK|FAILED`。
可重复安装输入与回滚见 docs/install_manifest.md。
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def _find_uv():
    """找到 uv 解释器；找不到就给出可操作的中文提示，而不是一个英文报错。"""
    candidate = os.environ.get('CELLFLOW_UV', 'uv')
    found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
    if found:
        return found
    # 这台机器（conda/mamba 环境）常见位置，按顺序尝试
    for guess in ('F:/mamba/Library/bin/uv.exe',
                  str(Path.home() / '.local/bin/uv.exe'), str(Path.home() / '.cargo/bin/uv.exe')):
        if Path(guess).exists():
            return guess
    print('找不到 uv。请先安装 uv（https://docs.astral.sh/uv/），或设置环境变量 CELLFLOW_UV 指向 uv.exe。',
          file=sys.stderr)
    print('临时替代方案：pip install -r requirements.txt  （用你自己的 Python 3.11 环境）', file=sys.stderr)
    raise SystemExit(2)


# uv 在 run() / freeze() 里按需解析：--check 与单元测试不应因为本机没装 uv 而失败。
ENV = dict(os.environ, UV_CACHE_DIR=str(ROOT / '.cache' / 'uv'),
           UV_PYTHON_INSTALL_DIR=str(ROOT / '.runtimes'), UV_SYSTEM_CERTS='true',
           UV_HTTP_TIMEOUT='120', UV_CONCURRENT_DOWNLOADS='3')
# conda 启动器可能带过期证书路径；这里改用系统信任库。
for key in ('SSL_CERT_DIR', 'SSL_CERT_FILE'):
    ENV.pop(key, None)
steps = []


def run(label, args):
    print(label, flush=True)
    start = time.time()
    steps.append(dict(stage=label, returncode=None, state='running'))
    (ROOT / 'installation.json').write_text(json.dumps(steps, ensure_ascii=False, indent=2), encoding='utf-8')
    proc = subprocess.run([_find_uv(), *args], cwd=ROOT, env=ENV)
    steps[-1].update(returncode=proc.returncode,
                     state='completed' if not proc.returncode else 'failed',
                     seconds=round(time.time() - start, 2))
    (ROOT / 'installation.json').write_text(json.dumps(steps, ensure_ascii=False, indent=2), encoding='utf-8')
    if proc.returncode:
        raise RuntimeError(label + ' failed; see installation.json and console output')


def python_path(folder, root=None):
    base = Path(root or ROOT)
    return str(base / folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))


def verify(folder, modules):
    code = ('import importlib; ' + '; '.join('importlib.import_module(' + repr(m) + ')' for m in modules)
            + '; print("IMPORT_OK")')
    subprocess.run([python_path(folder), '-c', code], cwd=ROOT, check=True)
    freeze(folder)


def freeze(folder):
    freeze_proc = subprocess.run([_find_uv(), 'pip', 'freeze', '--python', python_path(folder)],
                                 env=ENV, capture_output=True, text=True, check=True)
    (ROOT / ('environment-' + folder.lstrip('.') + '.lock.txt')).write_text(
        freeze_proc.stdout, encoding='utf-8')


# ---------------------------------------------------------------- 环境健康检查
# 强制清单只覆盖核心运行时（CellRank、pypdf 等可选能力不在此列）。
# 隔离环境（veloVI / RegVelo）不存在只提示；存在但 import/版本不健康则整体判失败。
REQUIRED_PACKAGES = {
    '.venv': [
        ('scanpy', 'scanpy', '>=1.11,<1.12'),
        ('scvelo', 'scvelo', '>=0.3.3,<0.4'),
        ('anndata', 'anndata', '>=0.11,<0.13'),
        ('numpy', 'numpy', '>=1.26,<2.3'),
        ('scipy', 'scipy', '>=1.13,<1.16'),
        ('pandas', 'pandas', '>=2.2,<3'),
        ('igraph', 'igraph', '>=0.11,<0.12'),
        ('fastapi', 'fastapi', '>=0.115,<1'),
        ('uvicorn', 'uvicorn', '>=0.30,<1'),
        ('multipart', 'python-multipart', '>=0.0.9'),
        ('markdown', 'markdown', '>=3.5,<4'),
        ('streamlit', 'streamlit', '>=1.49,<2'),
        ('plotly', 'plotly', '>=6,<7'),
        ('PySide6', 'PySide6', '>=6.8,<7'),
    ],
    '.venv-velovi': [
        ('scvi', 'scvi-tools', '>=1.2,<1.3'),
        ('torch', 'torch', '>=2.5,<2.6'),
        ('scanpy', 'scanpy', '>=1.10,<1.12'),
        ('scvelo', 'scvelo', '>=0.3.3,<0.4'),
        ('anndata', 'anndata', '>=0.10.8,<0.12'),
        ('numpy', 'numpy', '>=1.26,<2'),
    ],
    '.venv-regvelo': [
        ('regvelo', 'regvelo', '>=0.4.2,<0.5'),
        ('scvi', 'scvi-tools', '>=1.2,<1.2.1'),
        ('torch', 'torch', '>=2.5,<2.6'),
        ('scipy', 'scipy', '>=1.13,<1.16'),
    ],
}
OPTIONAL_ENVIRONMENTS = ('.venv-velovi', '.venv-regvelo')
CHECK_ASSETS = ('web/dist/index.html', 'cellflow_app.pyw')
CHECK_TIMEOUT_SECONDS = 180
MANIFEST_MARKER = 'MANIFEST_JSON:'

# 在目标解释器里执行：真实 import（不是 find_spec）+ 读发行版版本。
_PROBE = r'''
import importlib
import importlib.metadata as metadata
import json
import platform
import sys

requirements = json.loads(sys.argv[1])
report = {
    "python_version": platform.python_version(),
    "python_executable": sys.executable,
    "packages": {},
}
for module, distribution, spec in requirements:
    record = {"module": module, "distribution": distribution, "required": spec}
    try:
        imported = importlib.import_module(module)
        record["imported"] = True
        version = None
        for name in (distribution, module):
            try:
                version = metadata.version(name)
                break
            except Exception:
                continue
        if version is None:
            version = getattr(imported, "__version__", None)
        record["version"] = str(version) if version is not None else None
    except Exception as exc:
        record["imported"] = False
        record["version"] = None
        record["error"] = type(exc).__name__ + ": " + str(exc)
    report["packages"][module] = record
print("PROBE_JSON:" + json.dumps(report, ensure_ascii=False))
'''

_SPEC_CLAUSE = re.compile(r'^(>=|<=|==|!=|>|<)\s*([0-9][0-9A-Za-z.*+_!-]*)$')


def _version_tuple(value):
    match = re.match(r'\s*v?(\d+(?:\.\d+)*)', str(value or ''))
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split('.'))


def version_satisfies(version, spec):
    """判断版本是否满足 `>=1.11,<1.12` 这类逗号分隔的约束。

    只实现本项目 requirements 用到的运算符；版本按数字段比较，
    因此 `2.5.1+cpu` 会按 2.5.1 处理。无法解析时返回 False（宁可报告 not ready）。
    """
    if not spec:
        return True
    numbers = _version_tuple(version)
    if numbers is None:
        return False
    for raw in str(spec).split(','):
        clause = raw.strip()
        if not clause:
            continue
        match = _SPEC_CLAUSE.match(clause)
        if not match:
            return False
        operator, wanted_text = match.groups()
        wanted = _version_tuple(wanted_text)
        if wanted is None:
            return False
        width = max(len(numbers), len(wanted))
        left = numbers + (0,) * (width - len(numbers))
        right = wanted + (0,) * (width - len(wanted))
        if operator == '>=' and not left >= right:
            return False
        if operator == '>' and not left > right:
            return False
        if operator == '<=' and not left <= right:
            return False
        if operator == '<' and not left < right:
            return False
        if operator == '==' and not left == right:
            return False
        if operator == '!=' and not left != right:
            return False
    return True


def _product_version(root):
    """从 cellflow/__init__.py 读产品版本，避免为了检查而导入整个包。"""
    try:
        text = (Path(root) / 'cellflow' / '__init__.py').read_text(encoding='utf-8')
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", text)
        return match.group(1) if match else 'unknown'
    except Exception:
        return 'unknown'


def probe_environment(python, requirements):
    """用目标解释器真实 import 并返回版本；失败时返回带 error 的结构。"""
    payload = json.dumps([[module, distribution, spec]
                          for module, distribution, spec in requirements])
    try:
        proc = subprocess.run(
            [str(python), '-c', _PROBE, payload],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=CHECK_TIMEOUT_SECONDS,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}', 'packages': {}}
    for line in reversed((proc.stdout or '').splitlines()):
        if line.startswith('PROBE_JSON:'):
            try:
                return json.loads(line[len('PROBE_JSON:'):])
            except Exception as exc:
                return {'error': f'探测输出无法解析：{exc}', 'packages': {}}
    tail = ((proc.stderr or '') or (proc.stdout or '')).strip()[-500:]
    return {'error': f'探测进程返回码 {proc.returncode}：{tail}', 'packages': {}}


def evaluate_environment(python, requirements, probe):
    """返回单个环境的 status / packages / problems。"""
    path = Path(python)
    entry = {
        'python': str(path),
        'status': 'not_installed',
        'optional': False,
        'python_version': None,
        'packages': {},
        'problems': [],
    }
    if not path.exists():
        entry['problems'].append('解释器不存在：' + str(path))
        return entry
    report = probe(str(path), list(requirements))
    entry['python_version'] = report.get('python_version')
    if report.get('error'):
        entry['status'] = 'not_ready'
        entry['problems'].append('探测失败：' + str(report['error']))
        return entry
    packages = report.get('packages') or {}
    problems = []
    for module, distribution, spec in requirements:
        record = packages.get(module) or {}
        installed = record.get('version')
        item = {'distribution': distribution, 'required': spec,
                'installed': installed, 'ok': False}
        if not record.get('imported'):
            item['error'] = record.get('error') or '未导入'
            problems.append(f'{module} 缺失或无法 import（{item["error"]}）')
        elif not version_satisfies(installed, spec):
            problems.append(f'{module} 版本 {installed} 不满足 {spec}')
        else:
            item['ok'] = True
        entry['packages'][module] = item
    entry['status'] = 'ready' if not problems else 'not_ready'
    entry['problems'] = problems
    return entry


def check_environment(root=None, probe=None, now=None):
    """只读健康检查，返回结构化 manifest（不写文件）。

    判定规则：
    - `.venv` 必须存在、所有必需包真实 import 且版本满足约束；
    - 隔离环境（veloVI / RegVelo）不存在 → 记为 not_installed，不拖垮核心检查；
      存在但 import 失败 / 版本不符 → not_ready，整体也判失败（防止“有 exe 就算就绪”）；
    - 启动所需资源（web/dist/index.html、cellflow_app.pyw）缺失 → 整体失败。
    """
    base = Path(root or ROOT)
    probe = probe or probe_environment
    generated_at = now or dt.datetime.now().astimezone().isoformat(timespec='seconds')
    manifest = {
        'schema': 1,
        'product': 'CellFlow',
        'product_version': _product_version(base),
        'generated_at': generated_at,
        'checker_python': {
            'executable': sys.executable,
            'version': platform.python_version(),
        },
        'platform': platform.platform(),
        'environments': {},
        'assets': {},
        'ready': False,
        'problems': [],
    }
    for folder, requirements in REQUIRED_PACKAGES.items():
        active = [item for item in requirements
                  if not (item[0] == 'PySide6' and os.name != 'nt')]
        entry = evaluate_environment(python_path(folder, base), active, probe)
        entry['optional'] = folder in OPTIONAL_ENVIRONMENTS
        manifest['environments'][folder] = entry
        if entry['status'] == 'not_ready':
            manifest['problems'].append(folder + ': ' + '；'.join(entry['problems']))
        elif entry['status'] == 'not_installed' and not entry['optional']:
            manifest['problems'].append(folder + ': 环境未安装')
    for name in CHECK_ASSETS:
        ok = (base / name).exists()
        manifest['assets'][name] = ok
        if not ok:
            manifest['problems'].append('缺少 ' + name)
    core = manifest['environments'].get('.venv', {})
    optional_ready = all(
        entry['status'] in ('ready', 'not_installed')
        for name, entry in manifest['environments'].items() if name != '.venv'
    )
    manifest['ready'] = bool(
        core.get('status') == 'ready'
        and optional_ready
        and all(manifest['assets'].values())
    )
    return manifest


def manifest_json(manifest):
    return json.dumps(manifest, ensure_ascii=False)


def print_manifest(manifest):
    """打印人类可读摘要 + 一行机器可读 JSON + 最终判定。"""
    try:  # 管道/重定向时也保持 UTF-8，避免中文变成乱码
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    for folder, entry in manifest['environments'].items():
        if entry['status'] == 'ready':
            summary = '、'.join(f'{module} {item.get("installed")}'
                               for module, item in entry['packages'].items())
            print(f'[OK] {folder}: {summary}', flush=True)
        elif entry['status'] == 'not_installed':
            if entry.get('optional'):
                print(f'[NOT_INSTALLED] {folder}: 可选环境未安装（不阻塞核心检查）', flush=True)
            else:
                print(f'[MISSING] {folder}: 环境未安装', flush=True)
        else:
            print(f'[NOT_READY] {folder}: ' + '；'.join(entry['problems']), flush=True)
    for name, ok in manifest['assets'].items():
        print(('[OK] ' if ok else '[MISSING] ') + name, flush=True)
    print(MANIFEST_MARKER + manifest_json(manifest), flush=True)
    print('ENVIRONMENT_CHECK_OK' if manifest['ready'] else 'ENVIRONMENT_CHECK_FAILED', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--include-cellrank', action='store_true',
                        help='额外安装 CellRank 2（下游命运概率，纯 CPU，体积小）')
    parser.add_argument('--include-velovi', action='store_true',
                        help='额外安装 veloVI 独立环境（scvi-tools + CPU PyTorch，约 500 MB 下载）')
    parser.add_argument('--include-regvelo', action='store_true',
                        help='可选 RegVelo 独立 CPU 神经环境（下载数 GB，本机不推荐）')
    parser.add_argument('--check', action='store_true',
                        help='只检查现有环境与关键导入，不安装任何东西')
    args = parser.parse_args()

    if args.check:
        manifest = check_environment()
        print_manifest(manifest)
        return 0 if manifest['ready'] else 1

    if not Path(python_path('.venv')).exists():
        run('Create Python 3.11 base environment', ['venv', '--python', '3.11', '--managed-python', '.venv'])
    run('Install UI and scientific core', ['pip', 'install', '--python', python_path('.venv'),
                                           '-r', 'requirements.txt'])
    if os.name == 'nt':
        from cellflow.desktop import repair_console_python, repair_gui_python
        repaired = repair_gui_python(ROOT / '.venv')
        print('GUI_PYTHON: ' + ('restored CPython GUI redirector' if repaired else 'verified GUI subsystem'), flush=True)
        repaired = repair_console_python(ROOT / '.venv')
        print('CLI_PYTHON: ' + ('restored CPython console redirector' if repaired else 'verified CPython redirector'), flush=True)
    verify('.venv', ['streamlit', 'plotly', 'anndata', 'scanpy', 'scvelo', 'igraph', 'markdown',
                     'fastapi', 'uvicorn', 'multipart', 'webview']
           + (['PySide6.QtWebEngineWidgets'] if os.name == 'nt' else []))

    if args.include_cellrank:
        # CellRank 2.0.7 是最后一个支持 Python 3.11 的版本；2.1+ 要求 Python ≥3.12。
        run('Install optional CellRank 2 (CPU)', ['pip', 'install', '--python', python_path('.venv'),
                                                 '-r', 'requirements-cellrank.txt'])
        verify('.venv', ['streamlit', 'scanpy', 'scvelo', 'cellrank'])
        print('CELLRANK_INSTALLED: 任务结果页出现“CellRank 2 · 命运概率”即为可运行。', flush=True)

    if args.include_velovi:
        # scvi-tools 1.2 要求 numpy<2，必须独立环境，否则会和主环境冲突。
        run('Create isolated veloVI environment', ['venv', '--python', '3.11', '--managed-python', '.venv-velovi'])
        run('Install CPU PyTorch for veloVI', ['pip', 'install', '--python', python_path('.venv-velovi'),
                                               'torch==2.5.1', '--index-url', 'https://download.pytorch.org/whl/cpu'])
        run('Install pinned veloVI stack', ['pip', 'install', '--python', python_path('.venv-velovi'),
                                            '-r', 'requirements-velovi.txt'])
        verify('.venv-velovi', ['scvi', 'torch', 'scanpy', 'scvelo'])
        print('VELOVI_INSTALLED: 界面「主流 RNA velocity 模型」里 veloVI 会显示为可运行。', flush=True)

    if not args.include_regvelo:
        print('CORE_INSTALLATION_AND_IMPORTS_OK; optional neural environments were not installed', flush=True)
        return

    run('Create isolated RegVelo environment', ['venv', '--python', '3.11', '--managed-python', '.venv-regvelo'])
    run('Install CPU PyTorch for RegVelo', ['pip', 'install', '--python', python_path('.venv-regvelo'),
                                            'torch==2.5.1', '--index-url', 'https://download.pytorch.org/whl/cpu'])
    run('Install pinned RegVelo stack', ['pip', 'install', '--python', python_path('.venv-regvelo'),
                                         '-r', 'requirements-regvelo.txt'])
    verify('.venv-regvelo', ['regvelo', 'scvi', 'torch', 'cellrank'])
    print('INSTALLATION_AND_IMPORTS_OK', flush=True)


if __name__ == '__main__':
    main()
