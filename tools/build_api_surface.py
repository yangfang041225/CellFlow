"""生成 docs/api_surface.md：列出本机 API 路由与处理函数。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cellflow import api  # noqa: E402


def main():
    rows = []
    for route in api.app.routes:
        methods = getattr(route, 'methods', None)
        if not methods:
            continue
        for method in sorted(methods):
            if method in ('HEAD', 'OPTIONS'):
                continue
            endpoint = getattr(route, 'endpoint', None)
            rows.append((getattr(route, 'path', ''), method,
                         endpoint.__name__ if endpoint else ''))
    lines = ['# 本机 API 路由', '',
             '由 `tools/build_api_surface.py` 从 `cellflow/api.py` 生成。',
             '默认只监听 127.0.0.1，供桌面窗口使用。', '',
             '| 方法 | 路径 | 处理函数 |', '|---|---|---|']
    for path, method, name in sorted(rows):
        lines.append(f'| {method} | `{path}` | `{name}` |')
    lines += ['', '## 维护提示', '',
              '- `/api/datasets/{id}/detail` 必须绑定到 `dataset_detail`，不能绑定内部辅助函数；',
              '  `tests/test_api_surface.py` 会检查处理函数名不以下划线开头。',
              '- 新增路由后重新运行本脚本，保持文档与代码一致。']
    out = ROOT / 'docs' / 'api_surface.md'
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('written', out, 'routes', len(rows))


if __name__ == '__main__':
    main()
