"""数据工作区：导入与检查。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import streamlit as st

from ... import workspace
from ...data import inspect as inspect_data, make_demo
from ...registry import eligibility, missing_needs
from ...runner import validate
from .. import help as help_mod
from .. import shell, state
from .. import theme as T

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / 'examples'
PUBLIC = ROOT / 'data' / 'public'
INCOMING = ROOT / 'results' / 'inputs'

SOURCES = {'demo': '内置示例', 'upload': '上传文件', 'local': '本机路径', 'public': '公开数据'}


def _save_upload(upload):
    lower = upload.name.lower()
    suffix = '.tar.gz' if lower.endswith('.tar.gz') else (Path(upload.name).suffix.lower() or '.bin')
    raw = upload.getbuffer()
    INCOMING.mkdir(parents=True, exist_ok=True)
    target = INCOMING / (hashlib.sha256(raw).hexdigest()[:16] + suffix)
    if not target.exists():
        target.write_bytes(raw)
    return target


def _public_fetch(identifier, progress=None):
    from ...publicdata import fetch
    identifier = (identifier or '').strip()
    if not identifier:
        raise ValueError('请输入 GEO 编号（如 GSE84133）或 .h5ad 直链')
    source = 'geo' if identifier.upper().startswith('GSE') else 'url'
    files = fetch(source, identifier, PUBLIC, progress=progress)
    if not files:
        raise RuntimeError('没有取回可用文件')
    match = [p for p in files if p.suffix.lower() in ('.h5ad', '.h5', '.loom')]
    return match[0] if match else files[0]


def render(pages):
    main, side = help_mod.layout()
    with main:
        project = workspace.project()
        dataset = state.current()
        shell.header(project, dataset)
        st.markdown('<h1>数据</h1>', unsafe_allow_html=True)
        T.tag('导入数据，检查它满足哪些方法的前提。')

        left, right = st.columns([1.1, 1], gap='large')
        with left:
            T.section('导入')
            source = st.radio('来源', list(SOURCES), horizontal=True, key='data_src',
                              format_func=lambda k: SOURCES[k], label_visibility='collapsed')
            path = None
            if source == 'demo':
                path = EXAMPLES / 'synthetic.npz'
                if not path.exists():
                    EXAMPLES.mkdir(parents=True, exist_ok=True)
                    make_demo(path)
                T.tag('240 细胞 × 120 基因的模拟数据，用于验证流程。')
            elif source == 'upload':
                up = st.file_uploader('数据文件', type=['h5ad', 'loom', 'h5', 'csv', 'tsv', 'txt',
                                                        'mtx', 'zip', 'gz', 'tgz'],
                                      label_visibility='collapsed')
                if up is not None:
                    path = _save_upload(up)
            elif source == 'local':
                text = st.text_input('文件或 10x 目录路径', key='local_path',
                                     placeholder=r'D:\data\sample.h5ad')
                if text.strip():
                    candidate = Path(text).expanduser()
                    if candidate.exists():
                        path = candidate
                    else:
                        T.note('路径不存在：' + text, 'bad')
            else:
                text = st.text_input('GEO 编号或 .h5ad 直链', key='public_id',
                                     placeholder='GSE84133 或 https://…/x.h5ad')
                if st.button('下载', key='public_go'):
                    try:
                        with st.spinner('下载并转换…'):
                            path = _public_fetch(text)
                    except Exception as exc:
                        T.note(f'下载失败：{type(exc).__name__}: {exc}', 'bad')

            if path is not None and str(path) != st.session_state.get('dataset_path'):
                state.select(path)
                st.rerun()

        with right:
            T.section('检查结果')
            data, fmt = state.loaded()
            if data is None:
                T.empty('尚未选择可用数据', '左侧导入后，这里显示细胞数、基因数与剪接层')
                if fmt and fmt.get('error'):
                    T.note(fmt['error'], 'bad')
            else:
                shape = fmt.get('shape') or {}
                layers = fmt.get('layers_present') or []
                splice = fmt.get('splice') or {}
                mt = (fmt.get('var') or {}).get('mt_matched')
                T.kv([
                    ('细胞数', f"{(shape.get('n_cells') or 0):,}"),
                    ('基因数', f"{(shape.get('n_genes') or 0):,}"),
                    ('X 类型', str(fmt.get('x_kind_text') or '未判定')),
                    ('剪接层', '、'.join(layers) if layers else '无'),
                    ('剪接覆盖', f"{splice.get('both_positive_pct', 0):.1f}%"
                     if splice else '不适用'),
                    ('线粒体基因', f'{mt} 个' if mt is not None else '—'),
                    ('obs 列', '、'.join(str(o.get('name') if isinstance(o, dict) else o)
                                for o in (fmt.get('obs') or [])) or '无'),
                ])

        T.section('方法适用性', '按当前数据与声明计算')
        if data is None:
            if side is not None:
                with side:
                    help_mod.panel('data')
            return

        config = state.config()
        try:
            facts = validate(data, config)
        except Exception as exc:
            T.note(f'数据校验失败：{type(exc).__name__}: {exc}', 'bad')
            return
        for message in facts.get('errors', []):
            T.note(message, 'bad')
        for message in facts.get('warnings', []):
            T.note(message, 'warn')

        from ...registry import INDEX
        rows = eligibility(facts, config)
        table = []
        for row in rows:
            method = INDEX.get(row['key'])
            missing = missing_needs(facts, config, method) if method else []
            table.append({'方法': row.get('name', row.get('key')),
                          '状态': '可运行' if row.get('can_run') else '不可运行',
                          '缺失': '；'.join(missing) or '—'})
        st.dataframe(table, hide_index=True, width='stretch', height=min(420, 38 + 28 * len(table)))

        T.section('数据声明', '影响方法准入，不修改数据')
        T.tag('核实来源后勾选；“像整数”不代表就是原始计数。')
        c1, c2 = st.columns(2)
        with c1:
            st.checkbox('X 是未归一化原始计数', key='raw_x_confirmed')
        with c2:
            st.checkbox('spliced/unspliced 是原始剪接计数', key='raw_su_confirmed')

    if side is not None:
        with side:
            help_mod.panel('data')
