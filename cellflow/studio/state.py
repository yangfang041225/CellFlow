"""应用状态：当前数据集、解析缓存、运行配置。"""
from __future__ import annotations

import streamlit as st

from .. import workspace
from ..data import inspect as inspect_data
from ..data import load as load_data
from ..runner import DEFAULTS, normalize_config
from ..registry import DEFAULT_VELOCITY_MODEL

SS = st.session_state


def init():
    items = workspace.list_datasets()
    SS.setdefault('dataset_path', str(items[0]['path']) if items else '')
    SS.setdefault('help_on', False)
    for key in ('seed', 'n_top_genes', 'n_neighbors', 'n_pcs', 'max_epochs',
                'min_shared_counts', 'max_iter', 'leiden_resolution', 'cellrank_n_states'):
        SS.setdefault('cfg_' + key, DEFAULTS[key])
    SS.setdefault('cfg_velocity_model', DEFAULT_VELOCITY_MODEL)
    SS.setdefault('cfg_device', 'auto')
    SS.setdefault('cfg_use_umap', DEFAULTS['use_umap'])


def datasets():
    return workspace.list_datasets()


def current():
    items = datasets()
    if not items:
        return None
    saved = SS.get('dataset_path')
    for item in items:
        if str(item['path']) == saved:
            return item
    SS['dataset_path'] = str(items[0]['path'])
    return items[0]


def select(path):
    SS['dataset_path'] = str(path)


def loaded():
    item = current()
    if item is None:
        return None, None
    path = item['path']
    try:
        stat = path.stat()
    except OSError:
        return None, None
    key = f'{path}|{stat.st_mtime_ns}|{stat.st_size}'
    if SS.get('payload_key') != key:
        try:
            data = load_data(path)
            fmt = inspect_data(data, path)
            SS['payload'] = (data, fmt)
        except Exception as exc:
            SS['payload'] = (None, {'error': f'{type(exc).__name__}: {exc}'})
        SS['payload_key'] = key
    return SS.get('payload')


def config():
    """把界面上的值拼成 runner 能接受的完整配置。"""
    raw = dict(DEFAULTS)
    for key in ('seed', 'n_top_genes', 'n_neighbors', 'n_pcs', 'max_epochs',
                'min_shared_counts', 'max_iter', 'cellrank_n_states'):
        value = SS.get('cfg_' + key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            raw[key] = int(value)
    raw['leiden_resolution'] = float(SS.get('cfg_leiden_resolution') or DEFAULTS['leiden_resolution'])
    raw['use_umap'] = bool(SS.get('cfg_use_umap', True))
    raw['velocity_model'] = str(SS.get('cfg_velocity_model') or DEFAULT_VELOCITY_MODEL)
    raw['device'] = str(SS.get('cfg_device') or 'auto')
    for key in ('raw_x_confirmed', 'raw_su_confirmed', 'grn_confirmed'):
        raw[key] = bool(SS.get(key, False))
    return normalize_config(raw)
