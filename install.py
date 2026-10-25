"""安装 CPU 优先的独立环境；不会改动系统 Python。

用法（在本目录打开 PowerShell）：
    python install.py                        # 基础环境：界面 + 质控/轨迹/速度（推荐先跑这个）
    python install.py --include-cellrank     # 额外装 CellRank 2（下游命运分析，纯 CPU，体积小）
    python install.py --include-regvelo      # 可选：RegVelo 独立 CPU 神经环境（下载很大，本机不推荐）

国内网络慢时只对本次进程换镜像：
    $env:UV_DEFAULT_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    python install.py
"""
import argparse
import json
import os
from pathlib import Path
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


UV = _find_uv()
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
    proc = subprocess.run([UV, *args], cwd=ROOT, env=ENV)
    steps[-1].update(returncode=proc.returncode,
                     state='completed' if not proc.returncode else 'failed',
                     seconds=round(time.time() - start, 2))
    (ROOT / 'installation.json').write_text(json.dumps(steps, ensure_ascii=False, indent=2), encoding='utf-8')
    if proc.returncode:
        raise RuntimeError(label + ' failed; see installation.json and console output')


def python_path(folder):
    return str(ROOT / folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))


def verify(folder, modules):
    code = ('import importlib; ' + '; '.join('importlib.import_module(' + repr(m) + ')' for m in modules)
            + '; print("IMPORT_OK")')
    subprocess.run([python_path(folder), '-c', code], cwd=ROOT, check=True)
    freeze(folder)


def freeze(folder):
    freeze_proc = subprocess.run([UV, 'pip', 'freeze', '--python', python_path(folder)],
                                 env=ENV, capture_output=True, text=True, check=True)
    (ROOT / ('environment-' + folder.lstrip('.') + '.lock.txt')).write_text(
        freeze_proc.stdout, encoding='utf-8')


def check_environment():
    """只读检查：不安装、不改文件，报告各环境是否可用。"""
    import importlib.util

    checks = {
        '.venv': ['streamlit', 'scanpy', 'scvelo', 'cellrank', 'anndata', 'markdown',
                  'fastapi', 'uvicorn', 'PySide6'],
        '.venv-velovi': ['scvi', 'torch', 'scanpy', 'scvelo'],
    }
    all_ok = True
    for folder, modules in checks.items():
        python = Path(python_path(folder))
        if not python.exists():
            print(f'[MISSING] {folder}: 环境未安装', flush=True)
            all_ok = all_ok and folder != '.venv'
            continue
        code = ('import importlib.util as u; '
                'missing=[m for m in ' + repr(modules) + ' if u.find_spec(m) is None]; '
                'print("MISSING:" + ",".join(missing))')
        proc = subprocess.run([str(python), '-c', code], capture_output=True, text=True)
        output = (proc.stdout or '').strip()
        missing = output.replace('MISSING:', '') if output.startswith('MISSING:') else ''
        if proc.returncode or missing:
            print(f'[INCOMPLETE] {folder}: 缺少 {missing or (proc.stderr or "").strip()[:200]}', flush=True)
            all_ok = all_ok and folder != '.venv'
        else:
            print(f'[OK] {folder}: {", ".join(modules)} 均可导入', flush=True)
    for name in ('web/dist/index.html', 'cellflow_app.pyw'):
        ok = (ROOT / name).exists()
        print(('[OK] ' if ok else '[MISSING] ') + name, flush=True)
        if not ok and name == 'web/dist/index.html':
            all_ok = False
    print('ENVIRONMENT_CHECK_OK' if all_ok else 'ENVIRONMENT_CHECK_FAILED', flush=True)
    return 0 if all_ok else 1


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
        return check_environment()

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
