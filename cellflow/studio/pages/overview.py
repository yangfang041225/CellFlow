"""概览工作区：项目状态与下一步。"""
from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from ... import workspace
from .. import help as help_mod
from .. import shell, state
from .. import theme as T


def _dur(job):
    start, end = float(job.get('submitted') or 0), float(job.get('updated') or 0)
    if not start or not end or end <= start:
        return '—'
    seconds = end - start
    return f'{seconds:.0f} 秒' if seconds < 120 else f'{seconds / 60:.1f} 分'


def render(pages):
    main, side = help_mod.layout()
    with main:
        project = workspace.project()
        dataset = state.current()
        counts = workspace.counts()
        shell.header(project, dataset, shell.queue_chips(counts))
        st.markdown('<h1>概览</h1>', unsafe_allow_html=True)
        T.tag('项目的当前状态：数据、任务与流程位置。')

        datasets = workspace.list_datasets()
        jobs = workspace.runs()
        T.kpi([(len(datasets), '数据集'), (counts.get('completed', 0), '已完成任务'),
               (counts.get('running', 0) + counts.get('queued', 0), '进行中'),
               (counts.get('failed', 0), '失败')])

        # ---------- 数据集
        T.section('数据集', '当前数据集决定可用方法')
        if not datasets:
            T.empty('还没有数据集', '到「数据」导入或使用内置示例')
        else:
            labels = [d['label'] for d in datasets]
            current = dataset['label'] if dataset else labels[0]
            picked = st.selectbox('当前数据集', labels, index=labels.index(current),
                                  key='ov_pick', label_visibility='collapsed')
            target = next(d for d in datasets if d['label'] == picked)
            if str(target['path']) != st.session_state.get('dataset_path'):
                state.select(target['path'])
                st.rerun()
            st.dataframe([{'数据集': d['label'], '来源': d['source'], '格式': d['kind'],
                           '大小': d['size']} for d in datasets],
                         hide_index=True, width='stretch', height=min(220, 38 + 30 * len(datasets)))

        # ---------- 最近任务
        T.section('最近任务', '结果在「分析结果」查看')
        if not jobs:
            T.empty('还没有分析任务', '到「模型分析」选择方法并运行')
        else:
            rows = []
            for job in jobs[:8]:
                info = workspace.job_summary(job)
                rows.append({'任务': info['id'][:8], '方法': info['method_label'],
                             '状态': T.chip(info['state'], info['state']),
                             '提交': time.strftime('%m-%d %H:%M', time.localtime(info['submitted'] or 0)),
                             '耗时': _dur(job)})
            st.dataframe(rows, hide_index=True, width='stretch', height=min(260, 38 + 30 * len(rows)))

        # ---------- 流程
        T.section('流程位置')
        steps = [('数据', bool(datasets)), ('模型运行', bool(jobs)),
                 ('结果', bool([j for j in jobs if j.get('state') == 'completed'])),
                 ('报告', False)]
        report_dir = None
        for job in jobs:
            if job.get('state') == 'completed':
                folder = Path(job['output'])
                if (folder / 'A_生物学分析报告.md').exists():
                    report_dir = folder
                    break
        steps[3] = ('报告', bool(report_dir))
        T.kv([(name, '已完成' if done else '未完成') for name, done in steps])

        if not datasets:
            T.note('下一步：进入「数据」，选择内置示例或导入自己的文件。', 'info')
        elif not jobs:
            T.note('下一步：进入「模型分析」，确认前置条件后运行一个方法。', 'info')
        elif counts.get('running') or counts.get('queued'):
            T.note('有任务正在运行。可到「分析结果」查看状态；本页面不显示伪造的百分比。', 'info')
        else:
            T.note('下一步：到「分析结果」检查图与指标，然后在「报告」导出 A/B 报告。', 'ok')

    if side is not None:
        with side:
            help_mod.panel('overview')
