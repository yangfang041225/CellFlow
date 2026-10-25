"""模型分析工作区：选择方法、确认前提、配置并运行。"""
from __future__ import annotations

import streamlit as st

from ... import method_content, workspace
from ...advisor import budget as budget_estimate
from ...queue import start_worker, submit
from ...registry import (DEFAULT_VELOCITY_MODEL, FAMILIES, INDEX, METHODS, VELOCITY_MODELS,
                       missing_needs, status_of)
from ...runner import validate
from .. import help as help_mod
from .. import shell, state
from .. import theme as T


def _runs_for(method_key):
    for job in workspace.runs():
        if job.get('method') == method_key:
            return job
    return None


def render(pages):
    main, side = help_mod.layout()
    with main:
        project = workspace.project()
        dataset = state.current()
        shell.header(project, dataset)
        st.markdown('<h1>模型分析</h1>', unsafe_allow_html=True)
        T.tag('每个方法一个独立任务；不评“最佳模型”。')

        data, fmt = state.loaded()
        if data is None:
            T.empty('尚未导入数据', '先到「数据」导入或选择数据集')
            if side is not None:
                with side:
                    help_mod.panel('model')
            return

        config = state.config()
        try:
            facts = validate(data, config)
        except Exception as exc:
            T.note(f'数据校验失败：{type(exc).__name__}: {exc}', 'bad')
            return

        rows = {row['key']: row for row in _gates(facts, config)}
        ordered = [m for m in METHODS if m.key in rows]
        ready = [m for m in ordered if rows[m.key].get('can_run')]
        blocked = [m for m in ordered if not rows[m.key].get('can_run')]
        options = [m.key for m in ready + blocked]

        left, right = st.columns([1, 2.35], gap='large')
        with left:
            T.section('方法', f'{len(ready)} 可运行 / {len(ordered)} 登记')
            labels = {m.key: f'{m.name} · {"可运行" if rows[m.key].get("can_run") else "受限"}'
                      for m in ready + blocked}
            picked = st.selectbox('方法', options, key='model_pick',
                                  format_func=lambda key: labels[key],
                                  label_visibility='collapsed')
            method = INDEX[picked]
            row = rows[picked]
            T.tag('用途：' + FAMILIES[method.family]['label'])
            if not row.get('can_run'):
                T.note('当前数据不满足启动条件，原因见右侧。', 'warn')

        with right:
            card = method_content.card(method.key) or {}
            T.section(method.name, method.name_en, right=T.chip(status_of(method), status_of(method)))
            if card.get('question'):
                st.markdown(f'<div class="help">{card["question"]}</div>', unsafe_allow_html=True)
            if card.get('not_question'):
                T.note(card['not_question'], 'warn', title='不适用')

            T.section('前置条件')
            missing = missing_needs(facts, config, method)
            T.kv([('必需输入', '、'.join(method.needs) or '无'),
                  ('本机依赖', '、'.join(method.packages) or '无'),
                  ('缺失', '；'.join(missing) if missing else '无（满足）')])
            blockers = (row.get('runtime_blockers') or []) + (row.get('scientific_blockers') or [])
            if blockers:
                T.note('；'.join(blockers), 'warn', title='不能启动的原因')

            T.section('参数')
            c1, c2 = st.columns(2, gap='large')
            with c1:
                T.field('高变基因数 n_top_genes',
                        '参与降维与速度拟合的基因数；越大越慢，基因总数不足时自动限制。')
                st.number_input('高变基因数', min_value=10, max_value=10000, step=100,
                                key='cfg_n_top_genes', label_visibility='collapsed')
                T.field('邻居数 n_neighbors', '细胞邻接图的邻居数；小数据 10–15 更敏感。')
                st.number_input('邻居数', min_value=3, max_value=200, step=1,
                                key='cfg_n_neighbors', label_visibility='collapsed')
                T.field('主成分数 n_pcs', 'PCA 保留的主成分数，通常 20–50。')
                st.number_input('主成分数', min_value=2, max_value=100, step=1,
                                key='cfg_n_pcs', label_visibility='collapsed')
            with c2:
                if method.key in ('velovi', 'regvelo'):
                    T.field('训练轮数 max_epochs', '神经网络训练轮数；CPU 上越大越慢。')
                    st.number_input('训练轮数', min_value=1, max_value=1000, step=10,
                                    key='cfg_max_epochs', label_visibility='collapsed')
                if method.key in ('auto', 'cellrank') or method.key.startswith('scvelo'):
                    T.field('速度模型 velocity_model',
                            'scVelo 官方 mode；veloVI 需要独立环境，未装时不会自动替换。')
                    st.selectbox('速度模型', list(VELOCITY_MODELS), key='cfg_velocity_model',
                                 format_func=lambda k: VELOCITY_MODELS[k]['label'],
                                 label_visibility='collapsed')
                T.field('计算设备 device', '自动：检测到 CUDA 用 GPU；纯 CPU 方法用 GPU 不会更快。')
                st.selectbox('计算设备', ('auto', 'cpu', 'cuda'), key='cfg_device',
                             format_func=lambda v: {'auto': '自动', 'cpu': 'CPU',
                                                    'cuda': 'GPU（CUDA）'}[v],
                             label_visibility='collapsed')

            with st.expander('高级参数'):
                a1, a2 = st.columns(2, gap='large')
                with a1:
                    st.number_input('随机种子 seed', min_value=0, max_value=2 ** 31 - 1,
                                    step=1, key='cfg_seed')
                    st.number_input('最小共享计数 min_shared_counts', min_value=1, max_value=1000,
                                    step=1, key='cfg_min_shared_counts')
                with a2:
                    st.number_input('聚类分辨率 leiden_resolution', min_value=0.05, max_value=5.0,
                                    step=0.05, key='cfg_leiden_resolution')
                    st.checkbox('使用 UMAP 二维投影', key='cfg_use_umap')

            T.section('运行')
            estimate = budget_estimate({'n_cells': int(facts.get('n_cells') or 0),
                                        'n_genes': int(facts.get('n_genes') or 0),
                                        'matrices': {'X': {}}})
            T.tag(f'{estimate["cells"]} 细胞 × {estimate["genes"]} 基因 · '
                  f'内存 {estimate["memory_text"]} · {estimate["time_text"]}')
            if st.button('运行分析', type='primary', key='run_btn',
                         disabled=not row.get('can_run')):
                try:
                    job_id = submit(dataset['path'], {**state.config(), 'method': picked})
                    start_worker()
                    st.session_state['last_job'] = job_id
                    T.note(f'已入队：{job_id}。到「分析结果」查看。', 'ok')
                except Exception as exc:
                    T.note(f'提交失败：{type(exc).__name__}: {exc}', 'bad')
            if not row.get('can_run'):
                T.tag('按钮置灰：先补齐上方列出的缺失条件。')

            last = _runs_for(picked)
            if last:
                info = workspace.job_summary(last)
                T.section('本方法最近一次任务')
                T.kv([('任务', T.mono(info['id'][:8])),
                      ('状态', T.chip(info['state'], info['state'])),
                      ('错误', info['error'] or '无')])

    if side is not None:
        with side:
            help_mod.panel('model')


def _gates(facts, config):
    """准入行；隔离环境（veloVI / RegVelo）在这里补运行条件。"""
    from ...registry import eligibility, env_python
    rows = eligibility(facts, config)
    for row in rows:
        method = INDEX.get(row['key'])
        if method is None or method.runtime_env in ('.venv', ''):
            continue
        if env_python(method.key) is not None:
            row['runtime_blockers'] = []
            row['can_run'] = not row.get('scientific_blockers')
    return rows
