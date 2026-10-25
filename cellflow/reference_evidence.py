"""Verify task-local velocity comparison evidence; claims alone grant no level."""
import hashlib
import json
from pathlib import Path

import numpy as np

TOLERANCES = {'scvelo_deterministic': 1e-6, 'scvelo_stochastic': 1e-6,
              'scvelo_dynamical': 1e-5}


def sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def verify(folder, claim, provenance):
    """Recompute comparison, bindings and artifact digests without trusting passed."""
    if folder is None or not isinstance(claim, dict):
        return False, '缺少本任务可核验的外部证据'
    root = Path(folder).resolve()

    def local(name):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('证据路径越界或缺失')
        return path

    try:
        manifest_path = local(claim['manifest'])
        if sha256(manifest_path) != claim['sha256']:
            raise ValueError('比较清单哈希不符')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        config = json.loads(local('config.json').read_text(encoding='utf-8'))
        tolerance = TOLERANCES[config['method']]
        binding = manifest['binding']
        for key in ('input_sha256', 'code_sha256', 'started_at'):
            if not provenance.get(key) or binding.get(key) != provenance[key]:
                raise ValueError('证据不属于当前任务/输入/源码：' + key)
        if sha256(local(provenance['input_path'])) != provenance['input_sha256']:
            raise ValueError('输入快照哈希变化')
        for name, digest in provenance['code_sha256'].items():
            if sha256(local(str(Path('source') / name))) != digest:
                raise ValueError('源码快照哈希变化')
        if binding['config_sha256'] != sha256(local('config.json')) or \
                binding['environment_sha256'] != sha256(local('environment.json')):
            raise ValueError('配置或环境不匹配')
        if manifest.get('schema') != 1 or manifest.get('method') != config['method'] or manifest.get('passed') is not True:
            raise ValueError('方法或清单格式不匹配')
        paths = {}
        for role in ('reference', 'candidate'):
            item = manifest[role]
            paths[role] = local(item['path'])
            if sha256(paths[role]) != item['sha256']:
                raise ValueError('比较产物哈希不符')
        if paths['candidate'] != local('velocity.npz') or paths['reference'] == paths['candidate']:
            raise ValueError('候选必须是本任务速度产物，参考须为另一文件')
        arrays = {}
        for role, path in paths.items():
            with np.load(path, allow_pickle=False) as p:
                matrix = np.asarray(p['velocity'], dtype=float)
                cells, genes = p['cells'].astype(str), p['genes'].astype(str)
                if matrix.shape != (len(cells), len(genes)) or not matrix.size:
                    raise ValueError('空矩阵或轴维度错误')
                if len(set(cells)) != len(cells) or len(set(genes)) != len(genes):
                    raise ValueError('重复标识符')
                if not np.isfinite(matrix).all():
                    raise ValueError('完整有限覆盖未达到；部分覆盖证据不能升级整项')
                arrays[role] = (matrix, cells, genes)
        left, lc, lg = arrays['reference']
        right, rc, rg = arrays['candidate']
        if set(lc) != set(rc) or set(lg) != set(rg):
            raise ValueError('细胞/基因覆盖不完整')
        ci, gi = {c:i for i,c in enumerate(rc)}, {g:i for i,g in enumerate(rg)}
        right = right[np.ix_([ci[c] for c in lc], [gi[g] for g in lg])]
        if np.max(np.abs(left - right)) > tolerance:
            raise ValueError('现场重算超出方法容差')
        return True, config['method'] + ' 的完整速度矩阵对照通过（适配协议一致，不证明生物学方向）'
    except (KeyError, ValueError, TypeError, OSError) as exc:
        return False, '外部证据未通过核验：' + str(exc)
