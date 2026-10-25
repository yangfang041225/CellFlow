"""外壳：左侧工作区导航与顶部状态栏。"""
from __future__ import annotations

import streamlit as st

from .. import workspace
from . import help as help_mod
from . import theme as T


def sidebar(pages, project, counts):
    with st.sidebar:
        T.brand()
        st.markdown('<div class="tag">项目</div>', unsafe_allow_html=True)
        name = st.text_input('项目名', value=project.get('name', ''), key='project_name',
                             label_visibility='collapsed', placeholder='项目名')
        if name and name.strip() and name.strip() != project.get('name'):
            workspace.rename_project(name)

        st.markdown('<div class="tag" style="margin-top:6px">工作区</div>', unsafe_allow_html=True)
        for key, label in (('overview', '概览'), ('data', '数据'), ('model', '模型分析'),
                           ('results', '分析结果'), ('reports', '报告')):
            st.page_link(pages[key], label=label)

        st.divider()
        st.markdown(
            f'<div class="tag">排队 {counts.get("queued", 0)}　运行 {counts.get("running", 0)}<br>'
            f'完成 {counts.get("completed", 0)}　失败 {counts.get("failed", 0)}</div>',
            unsafe_allow_html=True)
        st.divider()
        st.toggle('上下文帮助', key='help_on',
                  help='在页面右侧显示本页说明与注意事项。')
        if st.button('使用说明', key='about_btn', width='stretch'):
            help_mod.about()


def header(project, dataset=None, right=''):
    T.bar(project.get('name', '项目'), dataset['label'] if dataset else None, right=right)


def queue_chips(counts):
    return ''.join([T.chip(f'排队 {counts.get("queued", 0)}', 'queued'),
                    T.chip(f'运行 {counts.get("running", 0)}', 'running'),
                    T.chip(f'完成 {counts.get("completed", 0)}', 'completed')])
