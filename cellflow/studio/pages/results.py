"""分析结果工作区：查看一次任务的图、指标、基因与运行记录。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import streamlit as st

from ... import workspace
from ...registry import INDEX
from .. import help as help_mod
from .. import shell, state
from .. import theme as T

FIGURES = (
    ('embedding.svg', '二维投影（箭头仅表示模型给出的方向）'),
    ('qc.svg', '质量检查：总表达 vs 检测基因数（未自动过滤）'),
    ('phase.svg', '剪接相图（需要 spliced/unspliced）'),
    ('trends.svg', '候选基因趋势（探索性）'),
    ('paga.svg', 'PAGA 群间连接（无向强度）'),
    ('fate.svg', '命运概率（模型推断）'),
)


def _json(folder, name):
    path = Path(folder) / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception:
        return None


def _tail(path, limit=8000):
    path = Path(path)
    if not path.exists():
        return ''
    text = path.read_text(encoding='utf-8', errors='replace')
    return text[-limit:]


def _dur(job):
    start, end = float(job.get('submitted') or 0), float(job.get('updated') or 0)
    if not start or not end or end <= start:
        return '—'
    seconds = end - start
    return f'{seconds:.0f} 秒' if seconds < 120 else f'{seconds / 60:.1f} 分'


def _num(value):
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if value == int(value) and abs(value) < 1e6:
            return f'{int(value)}'
        return f'{value:.4g}'
    return str(value)


def render(pages):
    main, side = help_mod.layout()
    with main:
        project = workspace.project()
        dataset = state.current()
        shell.header(project, dataset)
        st.markdown('<h1>分析结果</h1>', unsafe_allow_html=True)
        T.tag('图形、指标与运行记录；只显示实际产生的文件。')

        jobs = workspace.runs()
        if not jobs:
            T.empty('还没有可查看的任务', '到「模型分析」运行一个方法')
            if side is not None:
                with side:
                    help_mod.panel('results')
            return

        labels = {}
        for job in jobs:
            method = INDEX[job['method']].name if job.get('method') in INDEX else job.get('method')
            state_label = {'queued': '排队', 'running': '运行中', 'completed': '已完成',
                           'failed': '失败'}.get(job.get('state'), job.get('state'))
            labels[job['id']] = f"{job['id'][:8]} · {method} · {state_label}"
        ids = [job['id'] for job in jobs]
        default = st.session_state.get('cf_result_job')
        index = ids.index(default) if default in ids else 0
        picked = st.selectbox('任务', ids, index=index, format_func=lambda k: labels[k],
                              key='res_job', label_visibility='collapsed')
        st.session_state['cf_result_job'] = picked
        job = next(item for item in jobs if item['id'] == picked)
        folder = Path(job['output'])
        result = _json(folder, 'result.json') or {}
        qc = _json(folder, 'qc.json') or {}
        prov = _json(folder, 'provenance.json') or {}

        T.kpi([(_dur(job), '耗时'),
               (str((result.get('metrics') or {}).get('n_genes_used')
                    or result.get('effective', {}).get('n_hvg') or '—'), '使用基因数'),
               (str(len(result.get('embedding') or []) or '—'), '细胞数'),
               (str(prov.get('device') or '—'), '计算设备')])

        tabs = st.tabs(['图形', '指标', '基因', '运行记录', '对比'])
        with tabs[0]:
            shown = 0
            cols = st.columns(2, gap='small')
            for name, caption in FIGURES:
                path = folder / 'figures' / name
                if not path.exists():
                    continue
                with cols[shown % 2]:
                    st.image(str(path), caption=caption, width='stretch')
                    st.markdown(f'<div class="tag" style="margin-top:-6px">figures/{name}</div>',
                                unsafe_allow_html=True)
                shown += 1
            if not shown:
                T.empty('这份任务没有图形文件', '任务失败或方法不产生图形时会出现这种情况')

        with tabs[1]:
            metrics = result.get('metrics') or {}
            if not metrics:
                T.empty('没有指标', '该方法没有输出可比较的数值指标')
            else:
                rows = []
                for key, value in sorted(metrics.items()):
                    rows.append({'指标': key, '值': _num(value)})
                st.dataframe(rows, hide_index=True, width='stretch')
            if qc:
                T.section('质控摘要')
                T.kv([(k, _num(v)) for k, v in list(qc.items())[:10] if not isinstance(v, (list, dict))])

        with tabs[2]:
            genes = result.get('gene_table') or []
            if not genes:
                T.empty('没有候选基因表', '只有速度类方法会输出候选基因')
            else:
                st.dataframe(genes[:40], hide_index=True, width='stretch')
                T.tag('候选基因按速度绝对值排序，属描述性结果，不证明因果。')

        with tabs[3]:
            T.kv([('任务', T.mono(job['id'])),
                  ('方法', job.get('method', '')),
                  ('开始', time.strftime('%Y-%m-%d %H:%M', time.localtime(job.get('submitted') or 0))),
                  ('目录', str(folder))])
            log = _tail(folder / 'run.log')
            st.code(log or '（没有 run.log）', language='text')

        with tabs[4]:
            same = [j for j in jobs if str(j.get('input')) == str(job.get('input'))
                    and j.get('state') == 'completed']
            if len(same) < 2:
                T.empty('同一数据集还没有第二个完成的任务', '再运行一个方法后，这里可以并排看指标')
            else:
                rows = []
                for item in same:
                    data = _json(Path(item['output']), 'result.json') or {}
                    metrics = data.get('metrics') or {}
                    rows.append({
                        '任务': item['id'][:8],
                        '方法': INDEX[item['method']].name if item.get('method') in INDEX else item.get('method'),
                        '速度长度中位数': _num(metrics.get('velocity_length_median', '—')),
                        '速度一致性中位数': _num(metrics.get('velocity_confidence_median', '—')),
                        '终末状态数': _num(metrics.get('n_terminal_states', '—')),
                    })
                st.dataframe(rows, hide_index=True, width='stretch')
                T.tag('指标定义不同，跨模型的数值不可直接排名。')

    if side is not None:
        with side:
            help_mod.panel('results')
