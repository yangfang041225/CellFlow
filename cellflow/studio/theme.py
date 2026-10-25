"""全新界面层：从零编写的视觉与交互基础（不依赖旧的 ui_* 模块）。

风格取向：传统科研仪器配套软件 —— 浅灰工作区、白面板、细线、方角、
单一深蓝强调色；信息密集但层级清楚。
"""
from __future__ import annotations

import html

import streamlit as st

INK = '#1d2329'
MUTED = '#566069'
FAINT = '#8b939b'
ACCENT = '#20395b'
OK = '#1d5b3b'
WARN = '#7a4a00'
BAD = '#8a1d1d'
LINE = '#d3d8de'
SOFT = '#e7eaee'
BG = '#f3f4f6'
PANEL = '#ffffff'
SUNK = '#fafbfc'

STATUS = {
    'queued': ('排队', WARN),
    'running': ('运行中', ACCENT),
    'completed': ('已完成', OK),
    'failed': ('失败', BAD),
    'ready': ('可运行', OK),
    'experimental': ('实验接入', WARN),
    'planned': ('尚未接入', FAINT),
}

CSS = f"""
<style>
html, body, .stApp {{ background: {BG}; color: {INK};
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  font-size: 13px; }}
.block-container {{ max-width: none; padding: 10px 16px 36px 16px; }}
h1, h2, h3, h4 {{ color: {INK}; font-weight: 600; letter-spacing: 0; }}
h1 {{ font-size: 15px; margin: 0; }}
h2 {{ font-size: 13.5px; margin: 8px 0 4px 0; }}
h3 {{ font-size: 12.8px; margin: 8px 0 3px 0; }}
a {{ color: {ACCENT}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
p, li {{ line-height: 1.6; }}
hr {{ border: 0; border-top: 1px solid {SOFT}; margin: 8px 0; }}
code {{ background: {SUNK}; border: 1px solid {SOFT}; padding: 0 4px; font-size: .92em; }}

header[data-testid="stHeader"], div[data-testid="stToolbar"], footer, #MainMenu,
div[data-testid="stDecoration"], div[data-testid="stStatusWidget"] {{ display: none !important; }}

section[data-testid="stSidebar"] {{ background: {PANEL}; border-right: 1px solid {LINE};
  min-width: 224px; max-width: 224px; }}
section[data-testid="stSidebar"] > div {{ padding-top: 10px; }}
section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] {{
  border-radius: 2px; padding: 5px 8px; font-size: 12.6px; color: {INK}; }}
section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"]:hover {{ background: {SUNK}; }}
section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"][aria-current="page"] {{
  background: #edf0f4; border-left: 2px solid {ACCENT}; font-weight: 600; }}

button, .stButton > button, .stDownloadButton > button {{
  border-radius: 3px !important; border: 1px solid #b6bec7 !important; background: {PANEL} !important;
  color: {INK} !important; font-size: 12.4px !important; font-weight: 500 !important;
  min-height: 30px; padding: .28rem .8rem !important; box-shadow: none !important; }}
.stButton > button:hover, .stDownloadButton > button:hover {{
  border-color: {ACCENT} !important; color: {ACCENT} !important; background: #f7f9fb !important; }}
.stButton > button[kind="primary"] {{ background: {ACCENT} !important; border-color: {ACCENT} !important;
  color: #fff !important; }}
.stButton > button[kind="primary"]:hover {{ background: #172c47 !important; color: #fff !important; }}
div[data-baseweb="select"] > div, div[data-testid="stTextInput"] input,
div[data-testid="stNumberInput"] input, textarea {{ border-radius: 3px !important;
  border-color: #b6bec7 !important; background: {PANEL} !important; font-size: 12.6px !important; }}
div[data-testid="stExpander"] {{ border: 1px solid {SOFT} !important; border-radius: 3px !important;
  background: {PANEL} !important; margin-bottom: 4px; }}
div[data-testid="stExpander"] summary {{ font-size: 12.4px; color: {MUTED}; }}
div[data-testid="stMetric"] {{ background: {PANEL}; border: 1px solid {SOFT}; border-radius: 3px;
  padding: 7px 9px; }}
div[data-testid="stMetricValue"] {{ font-size: 17px; font-weight: 600; color: {INK};
  font-variant-numeric: tabular-nums; }}
div[data-testid="stMetricLabel"] {{ font-size: 11.2px; color: {MUTED}; }}
div[data-testid="stTabs"] button {{ font-size: 12.6px; border-radius: 0; padding: 3px 9px; }}
div[data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ background: {ACCENT}; height: 2px; }}
div[data-testid="stAlert"] {{ border-radius: 3px; border: 1px solid {SOFT}; font-size: 12.6px;
  padding: 7px 9px; }}
div[data-testid="stVerticalBlock"] {{ gap: .42rem; }}
div[data-testid="stHorizontalBlock"] {{ gap: .55rem; }}
div[data-testid="stDataFrame"] {{ border: 1px solid {SOFT}; }}
div[data-testid="stFileUploader"] section {{ border: 1px dashed #b6bec7; border-radius: 3px;
  background: {SUNK}; padding: 10px; }}
div[data-testid="stPopover"] button {{ width: 20px !important; height: 20px !important;
  min-height: 20px !important; padding: 0 !important; font-size: 11px !important;
  border-radius: 2px !important; color: {MUTED} !important; }}
div[data-testid="stImage"] img {{ border: 1px solid {SOFT}; background: #fff; }}
img {{ border-radius: 0; }}

.bar {{ display:flex; align-items:center; gap:8px; padding: 8px 0 7px 0;
  border-bottom: 1px solid {LINE}; margin-bottom: 10px; }}
.bar .crumb {{ font-size: 12.4px; color: {MUTED}; }}
.bar .crumb b {{ color: {INK}; font-weight: 600; }}
.bar .right {{ margin-left:auto; display:flex; gap:4px; }}
.brand {{ display:flex; align-items:baseline; gap:7px; padding: 2px 0 8px 0; }}
.brand .n {{ font-size: 13.5px; font-weight: 650; }}
.brand .s {{ font-size: 11px; color: {FAINT}; }}
.sec {{ display:flex; align-items:baseline; gap:8px; margin: 9px 0 5px 0;
  padding-bottom: 3px; border-bottom: 1px solid {SOFT}; }}
.sec .t {{ font-size: 12.8px; font-weight: 600; }}
.sec .h {{ font-size: 11.6px; color: {MUTED}; }}
.sec .r {{ margin-left:auto; font-size: 11.6px; color: {FAINT}; }}
.chip {{ display:inline-block; padding: 0 6px; line-height: 17px; font-size: 11.2px;
  border: 1px solid currentColor; border-radius: 2px; margin-right: 4px; }}
.kv {{ width: 100%; border-collapse: collapse; font-size: 12.4px; border: 1px solid {SOFT}; }}
.kv td {{ padding: 3px 7px; border-bottom: 1px solid {SOFT}; vertical-align: top; }}
.kv td:first-child {{ color: {MUTED}; width: 30%; background: {SUNK}; white-space: nowrap; }}
.kv tr:last-child td {{ border-bottom: 0; }}
.note {{ border-left: 3px solid var(--c); padding: 2px 0 2px 9px; margin: 3px 0;
  font-size: 12.4px; color: {MUTED}; line-height: 1.6; }}
.note b {{ color: var(--c); }}
.tag {{ font-size: 11.6px; color: {FAINT}; }}
.mono {{ font-family: ui-monospace, Consolas, monospace; font-size: 11.8px;
  background: {SUNK}; border: 1px solid {SOFT}; padding: 0 4px; }}
.empty {{ text-align:center; padding: 20px 10px; border: 1px dashed {LINE}; background: {SUNK};
  font-size: 12.4px; color: {MUTED}; }}
.empty .hint {{ font-size: 11.8px; color: {FAINT}; margin-top: 3px; }}
.kpi {{ display:flex; border: 1px solid {SOFT}; background: {PANEL}; }}
.kpi .it {{ flex:1; padding: 6px 9px; border-right: 1px solid {SOFT}; }}
.kpi .it:last-child {{ border-right: 0; }}
.kpi .n {{ font-size: 15.5px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.kpi .l {{ font-size: 11.2px; color: {MUTED}; }}
.field {{ font-size: 12.4px; font-weight: 500; margin: 3px 0 1px 0; }}
.field .req {{ color: {BAD}; margin-left: 2px; }}
.help {{ font-size: 12.2px; color: {MUTED}; line-height: 1.6; }}
.help b {{ color: {INK}; }}
</style>
"""


def apply():
    st.markdown(CSS, unsafe_allow_html=True)


def chip(text, state='muted'):
    color = STATUS.get(state, (None, MUTED))[1] if state in STATUS else MUTED
    label = STATUS[state][0] if state in STATUS else str(text)
    return f'<span class="chip" style="color:{color}">{html.escape(label)}</span>'


def brand(name='CellFlow', sub='单细胞动力学分析工作站'):
    st.markdown(f'<div class="brand"><span class="n">{html.escape(name)}</span>'
                f'<span class="s">{html.escape(sub)}</span></div>', unsafe_allow_html=True)


def bar(project, dataset=None, right=''):
    crumb = f'<b>{html.escape(project)}</b>'
    if dataset:
        crumb += f' <span style="color:{FAINT}">/</span> {html.escape(dataset)}'
    st.markdown(f'<div class="bar"><div class="crumb">{crumb}</div>'
                f'<div class="right">{right}</div></div>', unsafe_allow_html=True)


def section(title, hint=None, right=None):
    h = f'<span class="h">{html.escape(hint)}</span>' if hint else ''
    r = f'<span class="r">{right}</span>' if right else ''
    st.markdown(f'<div class="sec"><span class="t">{html.escape(title)}</span>{h}{r}</div>',
                unsafe_allow_html=True)


def kv(rows):
    body = ''.join(f'<tr><td>{html.escape(str(k))}</td><td>{v}</td></tr>' for k, v in rows)
    st.markdown(f'<table class="kv">{body}</table>', unsafe_allow_html=True)


def note(text, kind='info', title=None):
    color = {'info': ACCENT, 'ok': OK, 'warn': WARN, 'bad': BAD}.get(kind, MUTED)
    head = f'<b>{html.escape(title)}</b>　' if title else ''
    st.markdown(f'<div class="note" style="--c:{color}">{head}{text}</div>', unsafe_allow_html=True)


def tag(text):
    st.markdown(f'<div class="tag">{text}</div>', unsafe_allow_html=True)


def mono(text):
    return f'<span class="mono">{html.escape(str(text))}</span>'


def empty(text, hint=None):
    h = f'<div class="hint">{html.escape(hint)}</div>' if hint else ''
    st.markdown(f'<div class="empty">{html.escape(text)}{h}</div>', unsafe_allow_html=True)


def kpi(items):
    body = ''.join(f'<div class="it"><div class="n">{html.escape(str(n))}</div>'
                   f'<div class="l">{html.escape(l)}</div></div>' for n, l in items)
    st.markdown(f'<div class="kpi">{body}</div>', unsafe_allow_html=True)


def field(label, help_text=None, required=False):
    req = '<span class="req">*</span>' if required else ''
    st.markdown(f'<div class="field">{html.escape(label)}{req}</div>', unsafe_allow_html=True)
    if help_text:
        with st.popover('?'):
            st.markdown(f'<div class="help">{help_text}</div>', unsafe_allow_html=True)
