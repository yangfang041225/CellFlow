from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import traceback
from .data import load, validate, qc_metrics, digest, inspect
from .registry import eligibility
from .adapters import run_adapter
from .reports import dump_json, make_figures, write_reports
from .trends import descriptive_trends
from . import device as device_mod
from .registry import VELOCITY_MODELS

VALID_DEVICES = ('auto', 'cpu', 'cuda')

DEFAULTS = dict(method='qc', seed=42, n_top_genes=2000, n_neighbors=30, n_pcs=30,
                min_shared_counts=20, max_iter=10, max_epochs=100, raw_x_confirmed=False,
                raw_su_confirmed=False, mt_prefix='MT-', species='', tissue='', sample_key='',
                group_key='', root_cell='', root_reason='', grn_path='', grn_source='', grn_confirmed=False,
                use_umap=True, leiden_resolution=1.0, cellrank_n_states=3,
                device='auto', velocity_model='stochastic',
                arrow_scale=1.0, arrow_density=120)

_TRUE_FALSE = ('raw_x_confirmed', 'raw_su_confirmed', 'grn_confirmed', 'use_umap')
_TEXT = ('method', 'mt_prefix', 'species', 'tissue', 'sample_key', 'group_key', 'root_cell',
         'root_reason', 'grn_path', 'grn_source', 'device', 'velocity_model')


def normalize_config(config):
    unknown = set(config) - set(DEFAULTS)
    if unknown:
        raise ValueError('未知配置字段：' + str(sorted(unknown)))
    c = {**DEFAULTS, **config}
    for key in _TRUE_FALSE:
        if not isinstance(c[key], bool):
            raise ValueError(key + ' 必须是 true/false')
    for key in _TEXT:
        if not isinstance(c[key], str):
            raise ValueError(key + ' 必须是文本')
    for key, lo, hi in [('seed', 0, 2 ** 31 - 1), ('n_top_genes', 10, 10000), ('n_neighbors', 3, 200),
                        ('n_pcs', 2, 100), ('min_shared_counts', 1, 1000), ('max_iter', 1, 100),
                        ('max_epochs', 1, 1000), ('cellrank_n_states', 2, 10),
               ('arrow_density', 20, 500)]:
        if isinstance(c[key], bool) or not isinstance(c[key], int) or not lo <= c[key] <= hi:
            raise ValueError(f'{key} 必须是 {lo}–{hi} 的整数')
    if isinstance(c['leiden_resolution'], bool) or not isinstance(c['leiden_resolution'], (int, float)):
        raise ValueError('leiden_resolution 必须是数字')
    if not 0.05 <= float(c['leiden_resolution']) <= 5:
        raise ValueError('leiden_resolution 必须在 0.05–5 之间')
    c['leiden_resolution'] = float(c['leiden_resolution'])
    if isinstance(c['arrow_scale'], bool) or not isinstance(c['arrow_scale'], (int, float)):
        raise ValueError('arrow_scale 必须是数字')
    if not 0.5 <= float(c['arrow_scale']) <= 3.0:
        raise ValueError('arrow_scale 必须在 0.5–3.0 之间（只影响显示，不改速度值）')
    c['arrow_scale'] = float(c['arrow_scale'])
    if c['device'] not in VALID_DEVICES:
        raise ValueError('device 只能是 auto / cpu / cuda')
    if c['velocity_model'] not in VELOCITY_MODELS:
        raise ValueError('velocity_model 只能是 ' + ' / '.join(VELOCITY_MODELS))
    return c



def _progress(folder, provenance, stage):
    """把当前阶段即时写入 provenance.json，供界面在运行中显示真实进度。"""
    try:
        snapshot = dict(provenance)
        snapshot['last_stage'] = stage
        snapshot['updated_at'] = datetime.now(timezone.utc).isoformat()
        dump_json(Path(folder) / 'provenance.json', snapshot)
    except Exception:
        pass


def _quality_report(folder, result, data):
    """结构性 + 一致性校验：不评价生物学对错，只检查结果是否自洽、可读。

    hard=True 的失败会让 quality.ok=False（结构性错误）；
    soft=True 只写入 warnings（例如命运概率未严格归一），不否定计算完成。
    """
    checks, warnings = [], []

    def add(name, ok, detail='', soft=False):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail, 'soft': bool(soft)})
        if not ok:
            warnings.append(f'{name}：{detail}' if detail else name)

    def is_number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def finite(value):
        try:
            import math
            return value == value and abs(value) != math.inf
        except Exception:
            return False

    result_path = folder / 'result.json'
    try:
        import json as _json
        _json.loads(result_path.read_text(encoding='utf-8'))
        add('result.json 可解析', True)
    except Exception as exc:
        add('result.json 可解析', False, repr(exc))

    n_cells = int(getattr(data, 'X').shape[0])
    n_genes = int(getattr(data, 'X').shape[1])
    known_genes = {str(item).upper() for item in getattr(data, 'genes', [])[:200000]}

    import numpy as _np

    velocity_path = folder / 'velocity.npz'
    if velocity_path.exists():
        try:
            with _np.load(velocity_path) as payload:
                velocity = payload['velocity']
                npz_cells = payload['cells'] if 'cells' in payload else None
                npz_genes = payload['genes'] if 'genes' in payload else None
            add('速度矩阵细胞轴维度与数据一致', velocity.shape[0] == n_cells,
                f'{velocity.shape[0]} vs {n_cells}')
            if npz_genes is not None:
                # scVelo 只在 HVG 子集上算速度：列数应等于存档基因数，且基因名必须属于数据
                add('速度矩阵列数维度与存档基因一致', velocity.shape[1] == len(npz_genes),
                    f'{velocity.shape[1]} vs {len(npz_genes)}')
                missing = [str(gene) for gene in npz_genes if str(gene).upper() not in known_genes]
                add('速度基因都属于输入数据', not missing,
                    f'{len(missing)} 个不在基因表：{", ".join(missing[:3])}')
            else:
                add('速度矩阵维度与细胞/基因一致', velocity.shape == (n_cells, n_genes),
                    f'{velocity.shape} vs ({n_cells}, {n_genes})')
            if npz_cells is not None and len(npz_cells) == velocity.shape[0]:
                same_order = (list(_np.asarray(npz_cells, dtype=str)) ==
                              list(_np.asarray(getattr(data, 'cells', []), dtype=str)))
                add('速度矩阵细胞顺序与数据一致', same_order, soft=True)
            finite_ratio = float(_np.isfinite(velocity).mean()) if velocity.size else 1.0
            add('速度矩阵无 NaN/Inf', finite_ratio == 1.0)
            if finite_ratio < 0.95:
                warnings.append(f'速度矩阵有 {100 * (1 - finite_ratio):.1f}% 非有限值，方向解释需谨慎')
        except Exception as exc:
            add('速度矩阵可读取', False, repr(exc))

    groups = (result or {}).get('groups') or {}
    sizes = groups.get('sizes')
    if sizes:
        try:
            total = (sum(int(value) for value in sizes.values())
                     if isinstance(sizes, dict) else sum(int(item) for item in sizes))
            add('分群大小之和等于细胞数', total == n_cells, f'{total} vs {n_cells}')
        except Exception as exc:
            add('分群大小可读取', False, repr(exc))

    embedding = (result or {}).get('embedding')
    if embedding:
        try:
            array = _np.asarray(embedding, dtype=float)
            add('嵌入坐标维度与细胞数一致', array.shape == (n_cells, 2),
                f'{array.shape} vs ({n_cells}, 2)')
            add('嵌入坐标无 NaN/Inf', bool(_np.isfinite(array).all()))
        except Exception as exc:
            add('嵌入坐标可读取', False, repr(exc))

    fate = (result or {}).get('fate') or {}
    probabilities = fate.get('probabilities')
    if probabilities:
        try:
            array = _np.asarray(probabilities, dtype=float)
            add('命运概率行数与细胞数一致', array.shape[0] == n_cells,
                f'{array.shape[0]} vs {n_cells}')
            if array.ndim == 2 and array.shape[0] == n_cells:
                in_range = bool(((array >= -1e-3) & (array <= 1 + 1e-3)).all())
                add('命运概率在 0–1 范围内', in_range, soft=True)
                row_sums = array.sum(axis=1)
                close = bool(_np.allclose(row_sums, 1.0, atol=5e-3))
                add('命运概率每行之和约为 1', close,
                    f'偏差最大 {abs(row_sums - 1.0).max():.3g}', soft=True)
        except Exception as exc:
            add('命运概率可读取', False, repr(exc))

    paga = (result or {}).get('paga') or {}
    matrix = paga.get('matrix')
    if matrix:
        try:
            array = _np.asarray(matrix, dtype=float)
            if array.ndim == 2 and array.shape[0] == array.shape[1]:
                add('PAGA 矩阵对称', bool(_np.allclose(array, array.T, atol=1e-6)), soft=True)
                add('PAGA 连接强度在 0–1 范围内',
                    bool(((array >= -1e-3) & (array <= 1 + 1e-3)).all()), soft=True)
            else:
                add('PAGA 矩阵为方阵', False, f'shape={array.shape}')
        except Exception as exc:
            add('PAGA 矩阵可读取', False, repr(exc))

    metrics = (result or {}).get('metrics') or {}
    bad = [key for key, value in metrics.items() if is_number(value) and not finite(value)]
    add('指标数值有效', not bad, '；'.join(bad))
    confidence = metrics.get('velocity_confidence_median')
    if is_number(confidence):
        add('速度置信度在 0–1 范围内', 0 <= confidence <= 1, f'{confidence}', soft=True)

    time_values = (result or {}).get('time')
    if time_values:
        add('时间序列长度与细胞数一致', len(time_values) == n_cells,
            f'{len(time_values)} vs {n_cells}')

    gene_table = (result or {}).get('gene_table') or []
    genes = []
    for row in gene_table:
        if isinstance(row, dict) and row.get('gene'):
            genes.append(str(row['gene']))
        elif isinstance(row, dict) and row.get('genes'):
            genes.extend(str(item.get('gene')) for item in row['genes']
                         if isinstance(item, dict) and item.get('gene'))
    if genes and known_genes:
        missing = [gene for gene in genes if gene.upper() not in known_genes]
        add('候选基因都能在数据基因表中找到', not missing,
            f'{len(missing)} 个不在基因表：{", ".join(missing[:3])}', soft=True)

    figures = list((folder / 'figures').glob('*.svg')) if (folder / 'figures').exists() else []
    add('至少生成一张图', bool(figures), f'{len(figures)} 张')

    if (result or {}).get('velocity') and not gene_table:
        warnings.append('速度结果存在但候选基因表为空（可能是过滤过严）')

    report = {'checks': checks,
              'warnings': warnings,
              'ok': all(item['ok'] for item in checks if not item.get('soft'))}
    import json as _json
    (folder / 'quality.json').write_text(_json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding='utf-8')
    return report


def _artifact_status(folder):
    a = (folder / 'A_生物学分析报告.md').exists()
    b = (folder / 'B_技术与复现报告.md').exists()
    return {'a_report': a, 'b_report': b, 'complete': bool(a and b)}


def run(input_path, config, folder):
    """执行单个任务。所有失败（含准备阶段、报告阶段）都写进 provenance 与 run.log。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    c = normalize_config(config)
    source = Path(input_path).resolve()
    local = folder / ('input' + source.suffix.lower())
    shutil.copyfile(source, local)
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    device_info = device_mod.resolve(c['device'])
    provenance = dict(
        input_path=str(local.resolve()), input_sha256=digest(local),
        python=sys.version, executable=sys.executable, platform=platform.platform(),
        reproduce_cwd=str((folder / 'source').resolve()),
        device=device_info.device, device_info=device_info.as_dict(),
        device_note=device_mod.note_for(c['method'], device_info.device),
        seed=c['seed'], duration=None,
        thread_limits={k: os.environ.get(k, '未显式设置') for k in
                       ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS')},
        started_at=datetime.now(timezone.utc).isoformat(),
        reproduce_command=subprocess.list2cmdline([
            sys.executable, '-m', 'cellflow.cli', 'run', '--input', str(local.resolve()),
            '--config', str((folder / 'config.json').resolve()),
            '--output', str((folder.parent / (folder.name + '-replay')).resolve())]),
        status='preparing')
    facts, qc, result, error, fmt = {}, {}, {}, None, None
    stage = 'prepare'
    with (folder / 'run.log').open('w', encoding='utf-8', buffering=1) as handle, \
            redirect_stdout(handle), redirect_stderr(handle):
        def log(message):
            print(datetime.now(timezone.utc).isoformat() + ' ' + message, flush=True)

        try:
            # ---- 准备阶段（失败也要留档）
            if c['grn_path']:
                grn = Path(c['grn_path']).resolve()
                shutil.copyfile(grn, folder / 'prior_grn.csv')
                c['grn_path'] = str((folder / 'prior_grn.csv').resolve())
                provenance['prior_grn_sha256'] = digest(c['grn_path'])
            dump_json(folder / 'config.json', c)
            packages = sorted({d.metadata['Name']: d.version for d in metadata.distributions()}.items())
            dump_json(folder / 'environment.json', dict(packages))
            code_files = list((root / 'cellflow').glob('*.py')) + [root / 'app.py']
            for path in code_files:
                if path.exists():
                    target = folder / 'source' / path.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
            provenance['code_sha256'] = {str(p.relative_to(root)): digest(p)
                                         for p in code_files if p.exists()}
            log('准备完成：配置、环境、源码副本已写入')

            random.seed(c['seed'])
            stage = 'read_input'
            _progress(folder, provenance, stage)
            log('读取输入；检查全部矩阵数值与标识符')
            from .memory import precheck, require
            reason, _memory = precheck(local)
            if reason:
                raise RuntimeError(reason)
            data = load(local)
            log(f'读取完成；当前可用内存 {require(0.5):.2f} GB')
            stage = 'validate'
            _progress(folder, provenance, stage)
            facts = validate(data, c)
            dump_json(folder / 'validation.json', facts)
            fmt = inspect(data, local, c['mt_prefix'])
            dump_json(folder / 'format.json', fmt)
            provenance.update(input_format=fmt['file']['suffix'], n_cells=facts['n_cells'],
                              n_genes=facts['n_genes'], layers=fmt['layers_present'])
            stage = 'eligibility'
            _progress(folder, provenance, stage)
            gates = eligibility(facts, c)
            dump_json(folder / 'eligibility.json', gates)
            chosen = next((x for x in gates if x['key'] == c['method']), None)
            if not chosen or not chosen['can_run']:
                reasons = (chosen['scientific_blockers'] + chosen['runtime_blockers']) if chosen else ['未知方法']
                raise ValueError('方法未通过准入：' + '；'.join(reasons))
            stage = 'qc'
            _progress(folder, provenance, stage)
            qc = qc_metrics(data, c)
            dump_json(folder / 'qc.json', qc)
            stage = 'adapter'
            _progress(folder, provenance, stage)
            log('调用适配器：' + c['method'] + '；配置见 config.json；模型警告全部保留在下方')
            log(f'计算前可用内存 {require(1.0):.2f} GB')
            result = run_adapter(c['method'], data, c, folder, log)
            stage = 'trends'
            _progress(folder, provenance, stage)
            result['gene_trends'] = descriptive_trends(data, result, c)
            dump_json(folder / 'result.json', result)
            dump_json(folder / 'gene_table.json', result.get('gene_table', []))
            stage = 'figures'
            _progress(folder, provenance, stage)
            log(f'绘图前可用内存 {require(0.5):.2f} GB')
            make_figures(folder, data, qc, result, config=c)
            provenance['quality'] = _quality_report(folder, result, data)
            provenance['status'] = 'completed'
            log('结果序列化与图形导出完成')
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            traceback.print_exc()
            provenance['status'] = 'failed'
            provenance['error'] = error
            provenance['failed_stage'] = stage
        finally:
            stage = 'reports'
            _progress(folder, provenance, stage)
            provenance['last_stage'] = stage
            provenance['finished_at'] = datetime.now(timezone.utc).isoformat()
            provenance['elapsed_seconds'] = round(time.monotonic() - started, 3)
            try:
                write_reports(folder, facts, qc, result, c, provenance, error, fmt)
            except Exception as exc:
                provenance['report_error'] = f'{type(exc).__name__}: {exc}'
                traceback.print_exc()
            artifacts = _artifact_status(folder)
            provenance['artifacts'] = artifacts
            if provenance['status'] == 'completed' and not artifacts['complete']:
                provenance['status'] = 'failed'
                provenance['failed_stage'] = 'reports'
                provenance['error'] = '报告未完整生成（A/B 缺失）：计算可能完成，但交付不完整'
            dump_json(folder / 'provenance.json', provenance)
    return provenance
