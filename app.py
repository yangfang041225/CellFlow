"""CellFlow 工作站入口。

界面层：cellflow/studio（全新编写）
计算层：cellflow/{data,runner,queue,adapters,reports,registry}
"""
import streamlit as st

from cellflow import workspace
from cellflow.studio import shell, state, theme

st.set_page_config(page_title='CellFlow · 单细胞动力学分析', page_icon='cellflow.ico',
                   layout='wide', initial_sidebar_state='expanded')
theme.apply()
state.init()

pages = {}


def _page(module):
    def run():
        from importlib import import_module
        import_module(module).render(pages)
    return run


pages['overview'] = st.Page(_page('cellflow.studio.pages.overview'), title='概览',
                            url_path='overview', default=True)
pages['data'] = st.Page(_page('cellflow.studio.pages.data'), title='数据', url_path='data')
pages['model'] = st.Page(_page('cellflow.studio.pages.model'), title='模型分析', url_path='model')
pages['results'] = st.Page(_page('cellflow.studio.pages.results'), title='分析结果',
                           url_path='results')
pages['reports'] = st.Page(_page('cellflow.studio.pages.reports'), title='报告',
                           url_path='reports')

page = st.navigation(list(pages.values()), position='hidden')
project = workspace.project()
counts = workspace.counts()
shell.sidebar(pages, project, counts)
page.run()
