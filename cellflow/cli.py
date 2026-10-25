import argparse
import json
import os
from pathlib import Path

for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(key, '1')
from .data import make_demo  # noqa: E402
from .runner import run, DEFAULTS  # noqa: E402


def _cmd_demo(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    make_demo(args.output)
    print(args.output.resolve())
    return 0


def _cmd_run(args):
    # utf-8-sig：用户在记事本里存过的 JSON 可能带 BOM，直接 utf-8 会解析失败
    config = json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else DEFAULTS.copy()
    result = run(args.input, config, args.output)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['status'] == 'completed' else 1


def _cmd_search(args):
    from . import publicdata
    hits = publicdata.search(args.source, args.query, args.max_results)
    print(json.dumps([h.__dict__ for h in hits], ensure_ascii=True, indent=2))
    return 0


def _cmd_fetch(args):
    from . import publicdata
    out = publicdata.fetch(args.source, args.identifier, args.out, progress=lambda m: print(m, flush=True),
                           species=args.species)
    print(json.dumps(publicdata.describe(out), ensure_ascii=False, indent=2))
    return 0


def _cmd_list_geo(args):
    from . import publicdata
    files = publicdata.list_geo_files(args.gse)
    print(json.dumps(files, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description='CellFlow 本地可复现单细胞分析')
    sub = parser.add_subparsers(dest='action', required=True)

    demo = sub.add_parser('demo', help='生成内置模拟数据')
    demo.add_argument('--output', type=Path, default=Path('examples/synthetic.npz'))
    demo.set_defaults(func=_cmd_demo)

    task = sub.add_parser('run', help='运行一个分析方法')
    task.add_argument('--input', type=Path, required=True)
    task.add_argument('--config', type=Path)
    task.add_argument('--output', type=Path, required=True)
    task.set_defaults(func=_cmd_run)

    search = sub.add_parser('search', help='搜索公开数据库（需要联网）')
    search.add_argument('--source', choices=['geo', 'cellxgene'], required=True)
    search.add_argument('--query', required=True)
    search.add_argument('--max-results', type=int, default=20)
    search.set_defaults(func=_cmd_search)

    fetch = sub.add_parser('fetch', help='下载公开数据并转成标准 .h5ad（需要联网）')
    fetch.add_argument('--source', choices=['geo', 'cellxgene', 'url'], required=True)
    fetch.add_argument('--id', dest='identifier', required=True,
                       help='GEO: GSE 编号；CELLxGENE: 数据集 UUID 或 .h5ad 直链；url: .h5ad 链接')
    fetch.add_argument('--out', type=Path, default=Path('data/public'))
    fetch.add_argument('--species', default='', help='human / mouse / rat；留空则按基因名猜')
    fetch.set_defaults(func=_cmd_fetch)

    geo = sub.add_parser('list-geo', help='列出某个 GSE 的可下载补充文件（需要联网）')
    geo.add_argument('--gse', required=True)
    geo.set_defaults(func=_cmd_list_geo)

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
