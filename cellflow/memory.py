"""数据内存预检：在真正 load 之前估算峰值内存，避免 16 GB 机器被大文件拖死。

估算不是精确值，只用于“提前拒绝明显装不下的文件”：
- .h5ad / .h5 / .loom：读 h5py 头部的 shape 与稀疏 nnz；
- CSV/TSV：按文件大小 × 4；
- 压缩包：按文件大小 × 5；
- 其他：按文件大小 × 3。
"""
from pathlib import Path
import ctypes
import os


def available_bytes():
    """当前可用物理内存（Windows GlobalMemoryStatusEx；其他平台退回 sysconf）。"""
    if os.name == 'nt':
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong),
                        ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong),
                        ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullAvailPhys)
    try:
        return os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except Exception:
        return 0


def _h5_group_shape(group):
    """返回 (rows, cols, nnz, kind) 或 None。"""
    try:
        if 'data' in group and 'indptr' in group:
            nnz = int(group['data'].shape[0])
            rows = int(group['indptr'].shape[0] - 1)
            shape = group.attrs.get('shape')
            cols = int(shape[1]) if shape is not None and len(shape) > 1 else 0
            return rows, cols, nnz, 'sparse'
        if 'shape' in group.attrs and len(group.attrs['shape']) == 2:
            shape = group.attrs['shape']
            return int(shape[0]), int(shape[1]), 0, 'dense'
    except Exception:
        return None
    return None


def _h5_shape(path: Path):
    """返回 (n_cells, n_genes, nnz, 说明)。

    除 /X 外，把 /layers/*（spliced/unspliced 等）和 /raw/X 也计入，
    避免“只按 X 估算”低估 RNA velocity 任务的峰值内存。
    """
    try:
        import h5py
        with h5py.File(path, 'r') as handle:
            entries = []
            x = handle.get('X')
            if x is not None:
                if isinstance(x, h5py.Group):
                    info = _h5_group_shape(x)
                    if info:
                        entries.append(('X', info))
                else:
                    entries.append(('X', (int(x.shape[0]), int(x.shape[1]), 0, 'dense')))
            layers = handle.get('layers')
            layer_count = 0
            if isinstance(layers, h5py.Group):
                for name in layers:
                    item = layers[name]
                    if isinstance(item, h5py.Group):
                        info = _h5_group_shape(item)
                    else:
                        info = (int(item.shape[0]), int(item.shape[1]), 0, 'dense')
                    if info:
                        entries.append(('layer:' + name, info))
                        layer_count += 1
            raw = handle.get('raw')
            if isinstance(raw, h5py.Group) and 'X' in raw:
                item = raw['X']
                if isinstance(item, h5py.Group):
                    info = _h5_group_shape(item)
                else:
                    info = (int(item.shape[0]), int(item.shape[1]), 0, 'dense')
                if info:
                    entries.append(('raw/X', info))
            matrix = handle.get('matrix')
            if matrix is not None and 'shape' in getattr(matrix, 'attrs', {}):
                info = _h5_group_shape(matrix)
                if info:
                    entries.append(('10x/matrix', info))
            if not entries:
                return None
            n_cells = max(item[0] for _, item in entries)
            n_genes = max(item[1] for _, item in entries)
            nnz = sum(item[2] for _, item in entries)
            dense = sum(item[0] * item[1] for name, item in entries if item[3] == 'dense')
            kind = 'sparse' if nnz else 'dense'
            note = kind + (f'（{layer_count} 个层）' if layer_count else '')
            return n_cells, n_genes, nnz, note, dense
    except Exception:
        return None
    return None


def estimate(path):
    path = Path(path)
    size = path.stat().st_size if path.exists() else 0
    suffix = path.suffix.lower()
    available = available_bytes()

    shape = None
    if suffix in ('.h5ad', '.h5', '.loom'):
        shape = _h5_shape(path)

    if shape:
        n_cells, n_genes, nnz, kind, dense_items = shape
        if nnz:
            # CSR：data(float64) + indices(int32) + indptr(int32) + AnnData 开销
            peak = nnz * (8 + 4 + 4) + n_cells * 256 + n_genes * 64 + dense_items * 8
        else:
            peak = max(int(n_cells) * int(n_genes) * 8 + dense_items * 8, size * 2)
        method = f'{kind} {n_cells}×{n_genes}'
    elif suffix in ('.csv', '.tsv', '.txt'):
        peak, method = int(size * 4), '文本表 ×4'
    elif suffix in ('.zip', '.gz', '.tgz', '.tar'):
        peak, method = int(size * 5), '压缩包 ×5'
    else:
        peak, method = int(size * 3), '文件 ×3'
    peak = int(peak)
    ratio = (peak / available) if available else 0.0
    return {
        'bytes': peak,
        'gb': round(peak / 1024 ** 3, 2),
        'available_gb': round(available / 1024 ** 3, 2),
        'ratio': round(ratio, 3),
        'method': method,
        'file_mb': round(size / 1024 ** 2, 1),
    }


def precheck(path, limit=0.9):
    """超过可用内存的 limit 倍时返回拒绝原因；否则返回 None。"""
    info = estimate(path)
    if info['ratio'] > limit:
        return (f"预计需要 {info['gb']} GB 内存（{info['method']}），"
                f"当前可用 {info['available_gb']} GB；已拒绝加载。"
                '请关闭其他程序、改用更小的子集，或在更强内存的机器上运行。'), info
    return None, info


def require(min_gb=1.0):
    """在重计算阶段前检查可用内存；低于阈值就明确失败，而不是让系统 OOM。"""
    free_gb = available_bytes() / 1024 ** 3
    if free_gb < min_gb:
        raise MemoryError(
            f'可用内存仅 {free_gb:.2f} GB（低于安全阈值 {min_gb:.1f} GB）；'
            '已停止本次计算。请关闭其他程序，或改用更小的数据子集。')
    return free_gb
