"""报告工作区：预览并导出 A/B 报告。"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import streamlit as st

from ... import workspace
from ... import reports
from ...registry import INDEX
from .. import help as help_mod
from .. import shell, state
from .. import theme as T

A_MD = 'A_生物学分析报告.md'
B_MD = 'B_技术与复现报告.md'
A_HTML = 'A_report.html'
B_HTML = 'B_report.html'


def _zip(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 与 API 导出使用同一份资源清单（含 figures/*.png，例如 stream.png）
        for path, arcname in reports.bundle_entries(folder):
            zf.write(path, arcname)
    return buf.getvalue()


def render(pages):
    main, side = help_mod.layout()
    with main:
        project = workspace.project()
        dataset = state.current()
        shell.header(project, dataset)
        st.markdown('<h1>报告</h1>', unsafe_allow_html=True)
        T.tag('A 生物学分析报告与 B 技术与复现报告，内容来自实际运行的产出。')

        jobs = [j for j in workspace.runs() if j.get('state') in ('completed', 'failed')]
        if not jobs:
            T.empty('还没有可导出报告的任务', '任务完成后报告会自动生成')
            if side is not None:
                with side:
                    help_mod.panel('reports')
            return

        labels = {}
        for job in jobs:
            method = INDEX[job['method']].name if job.get('method') in INDEX else job.get('method')
            labels[job['id']] = f"{job['id'][:8]} · {method} · {job.get('state')}"
        ids = [job['id'] for job in jobs]
        picked = st.selectbox('任务', ids, format_func=lambda k: labels[k], key='rep_job',
                              label_visibility='collapsed')
        job = next(item for item in jobs if item['id'] == picked)
        folder = Path(job['output'])

        a_path, b_path = folder / A_MD, folder / B_MD
        if not a_path.exists() and not b_path.exists():
            T.note('这份任务还没有报告文件；失败任务通常仍会生成 A 报告，请查看运行记录。', 'warn')
            if side is not None:
                with side:
                    help_mod.panel('reports')
            return

        T.kv([('任务', T.mono(job['id'])),
              ('目录', str(folder)),
              ('A 报告', '有' if a_path.exists() else '无'),
              ('B 报告', '有' if b_path.exists() else '无')])

        tabs = st.tabs(['A 生物学分析', 'B 技术与复现'])
        with tabs[0]:
            if a_path.exists():
                st.download_button('下载 A 报告（Markdown）', a_path.read_bytes(),
                                   file_name=A_MD, mime='text/markdown', key='dl_a_md')
                st.markdown(a_path.read_text(encoding='utf-8', errors='replace'))
            else:
                T.empty('没有 A 报告')

        with tabs[1]:
            if b_path.exists():
                st.download_button('下载 B 报告（Markdown）', b_path.read_bytes(),
                                   file_name=B_MD, mime='text/markdown', key='dl_b_md')
                st.markdown(b_path.read_text(encoding='utf-8', errors='replace'))
            else:
                T.empty('没有 B 报告')

        T.section('导出')
        cols = st.columns(3, gap='small')
        with cols[0]:
            if (folder / A_HTML).exists():
                st.download_button('A 报告 HTML', (folder / A_HTML).read_bytes(),
                                   file_name=A_HTML, mime='text/html', key='dl_a_html')
        with cols[1]:
            if (folder / B_HTML).exists():
                st.download_button('B 报告 HTML', (folder / B_HTML).read_bytes(),
                                   file_name=B_HTML, mime='text/html', key='dl_b_html')
        with cols[2]:
            try:
                blob = _zip(folder)
            except Exception as exc:
                blob = b''
                T.note(f'打包失败：{exc}', 'bad')
            if blob:
                st.download_button('打包 ZIP', blob, file_name=f'{job["id"]}_reports.zip',
                                   mime='application/zip', key='dl_zip')
        T.tag('报告包含本机绝对路径与样本 ID，外发前请审阅。')

    if side is not None:
        with side:
            help_mod.panel('reports')
