"""从 registry 生成模型支持矩阵文档（docs/model_support_matrix.md）。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cellflow import method_content  # noqa: E402
from cellflow.registry import FAMILIES, METHODS, STATUS_LABELS, status_of  # noqa: E402


def main():
    lines = ['# 模型支持矩阵', '',
             '由 `tools/build_support_matrix.py` 从 `cellflow/registry.py` 生成，',
             '状态按本机安装情况实时判定。', '',
             '| 方法 | 类别 | 官方实现 | 必需输入 | 运行环境 | 本机状态 | 主要限制 |',
             '|---|---|---|---|---|---|---|']
    for method in METHODS:
        card = method_content.card(method.key) or {}
        needs = '、'.join(method.needs) or '无'
        env = method.runtime_env or '.venv'
        status = STATUS_LABELS[status_of(method)][0]
        limits = '；'.join(card.get('boundaries') or [])[:120] or '—'
        lines.append(f'| {method.name} | {FAMILIES[method.family]["label"]} | '
                     f'`{method.name_en}` | {needs} | `{env}` | {status} | {limits} |')

    lines += ['', '## 说明', '',
              '- “本机状态”按当前解释器是否安装了所需依赖判断；隔离环境（`.venv-velovi`）单独判断。',
              '- 未标为可运行的方法不会被执行；界面会显示缺什么。',
              '- 不同方法的似然/损失/时间尺度不可直接比较。']
    out = ROOT / 'docs' / 'model_support_matrix.md'
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('written', out)


if __name__ == '__main__':
    main()
