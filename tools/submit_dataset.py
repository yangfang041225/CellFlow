"""命令行提交一个分析任务到本地队列（不用打开网页）。

例子：
    python tools/submit_dataset.py --input data/public/geo_GSE84133.h5ad --method auto
    python tools/submit_dataset.py --input my.h5ad --method auto --group-key leiden --species human
    python tools/submit_dataset.py --input my.h5ad --list-methods

任务会写进 results/queue.sqlite，打开界面「任务结果」页即可看到。
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cellflow.queue import list_jobs, start_worker, submit  # noqa: E402
from cellflow.registry import METHODS, status_of  # noqa: E402
from cellflow.runner import DEFAULTS  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='提交一个 CellFlow 分析任务')
    parser.add_argument('--input', type=Path, help='输入 .h5ad 路径')
    parser.add_argument('--method', default='auto', help='方法 key（默认 auto 一键分析）')
    parser.add_argument('--group-key', default='', help='分组列名（如 leiden / milestone）')
    parser.add_argument('--sample-key', default='', help='供体/样本列名')
    parser.add_argument('--species', default='', help='human / mouse / 留空')
    parser.add_argument('--root-cell', default='', help='起始细胞 ID（DPT/拟命运用，可留空）')
    parser.add_argument('--root-reason', default='', help='选择起点的生物学依据')
    parser.add_argument('--raw-x', action='store_true', help='X 是未归一化原始计数')
    parser.add_argument('--raw-su', action='store_true', help='spliced/unspliced 是原始计数')
    parser.add_argument('--n-top-genes', type=int, default=DEFAULTS['n_top_genes'])
    parser.add_argument('--list-methods', action='store_true', help='列出全部方法与当前状态')
    parser.add_argument('--wait', action='store_true', help='提交后等待任务结束')
    args = parser.parse_args()

    if args.list_methods:
        for method in METHODS:
            print(f"{method.key:20s} {status_of(method):12s} {method.name}")
        return 0

    if not args.input:
        parser.error('需要 --input（或用 --list-methods 看方法列表）')
    if not args.input.exists():
        parser.error(f'找不到输入文件：{args.input}')

    config = {**DEFAULTS,
              'method': args.method,
              'group_key': args.group_key,
              'sample_key': args.sample_key,
              'species': args.species,
              'root_cell': args.root_cell,
              'root_reason': args.root_reason,
              'raw_x_confirmed': bool(args.raw_x),
              'raw_su_confirmed': bool(args.raw_su),
              'n_top_genes': args.n_top_genes}
    job = submit(args.input, config)
    print('已提交任务：', job)
    start_worker()
    if not args.wait:
        print('后台执行中；打开界面「任务结果」页，或加 --wait 等待完成。')
        return 0
    for _ in range(1800):
        time.sleep(3)
        row = next((item for item in list_jobs() if item['id'] == job), None)
        if row and row['state'] in ('completed', 'failed'):
            print('状态：', row['state'], row.get('error') or '')
            return 0 if row['state'] == 'completed' else 1
    print('等待超时；请到界面查看，或检查 results/worker.log')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
