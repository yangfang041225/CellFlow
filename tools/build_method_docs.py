"""从 registry + method_content 生成 docs/模型介绍.md（离线可读版）。

内容与界面同源，避免“页面说一套、文档说另一套”。
用法：.venv\\Scripts\\python.exe tools\\build_method_docs.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cellflow.method_content import METHOD_CONTENT  # noqa: E402
from cellflow.registry import (CHECKED, FAMILIES, METHODS, NEEDS, STATUS_LABELS,  # noqa: E402
                               status_of)


def _bullets(items, empty='（无）'):
    if not items:
        return [f'- {empty}']
    return [f'- {x}' for x in items]


def build():
    order = sorted(METHODS, key=lambda m: (FAMILIES[m.family]['order'], m.key))
    counts = {}
    for method in order:
        counts[status_of(method)] = counts.get(status_of(method), 0) + 1
    lines = [
        '# 模型介绍（离线版）',
        '',
        '本文件由 `tools/build_method_docs.py` 从程序内的方法注册表与说明文字自动生成，'
        '与界面“模型介绍”页同源。',
        '',
        f'核查日期：{CHECKED}。当前状态统计：'
        + '，'.join(f'{STATUS_LABELS[k][0]} {v} 个' for k, v in counts.items()) + '。',
        '',
        '## 先分清用途',
        '',
        '| 用途 | 一句话 | 回答什么问题 |',
        '|---|---|---|',
    ]
    for key, info in sorted(FAMILIES.items(), key=lambda kv: kv[1]['order']):
        lines.append(f"| {info['label']} | {info['short']} | {info['question']} |")
    lines += [
        '',
        '> 速度推断、轨迹分析、调控动力学和下游命运分析回答的是**不同问题**，'
        '不能混成一个“哪个模型最好”的排行榜。',
        '',
        '## 输入前提速查',
        '',
        '| 前提 | 说明 | 数据里长什么样 |',
        '|---|---|---|',
    ]
    for key, (label, example) in NEEDS.items():
        lines.append(f'| {label} | `{key}` | {example} |')

    current_family = None
    for method in order:
        if method.family != current_family:
            current_family = method.family
            info = FAMILIES[method.family]
            lines += ['', f"## {info['label']}：{info['question']}", '']
        status = status_of(method)
        card = METHOD_CONTENT.get(method.key) or {}
        lines += [f"### {method.name}（`{method.key}`，{method.name_en}）", '']
        lines.append(f"- **当前状态**：{STATUS_LABELS[status][0]}")
        lines.append(f"- **必需输入**：{'、'.join(NEEDS.get(n, (n, ''))[0] for n in method.needs) or '无'}")
        lines.append(f"- **可选输入**：{'、'.join(method.optional) if method.optional else '无'}")
        lines.append(f"- **本程序实际输出**：{'；'.join(method.outputs) if method.outputs else '无（尚未接入）'}")
        lines.append(f"- **CPU / GPU**：{method.gpu_note}")
        if method.data_limit:
            lines.append(f"- **数据限制**：{method.data_limit}")
        lines.append(f"- **一句话警示**：{method.note}")
        lines.append(f"- **官方来源**：{method.source or '无'}")
        lines.append('')
        if not card:
            lines += ['> 该方法的详细说明仍在补充，以上为注册表中的事实信息。', '']
            continue
        lines += ['**适合回答的问题**：' + card['question'], '']
        lines += ['**不能回答什么**：' + card['not_question'], '']
        lines += ['**必需输入**', '']
        lines += _bullets(card['inputs_required'])
        lines += ['', '**可选信息**', '']
        lines += _bullets(card['inputs_optional'])
        lines += ['', '**计算过程（简版）**', '']
        lines += [f"{i}. **{s['title']}**：{s['detail']}" for i, s in enumerate(card['steps'], 1)]
        lines += ['', '**方法通常能输出**', '']
        lines += _bullets(card['usually_outputs'])
        lines += ['', '**本程序目前实际输出**', '']
        lines += _bullets(card['we_output'], '（尚未接入：本程序目前没有该方法的任何产出）')
        lines += ['', '**本程序明确不输出**', '']
        lines += _bullets(card['we_not_output'])
        lines += ['', '**主要假设**', '']
        lines += _bullets(card['assumptions'])
        lines += ['', '**解释边界**', '']
        lines += _bullets(card['boundaries'])
        tech = card.get('tech') or {}
        lines += ['', '**运行环境与状态**', '',
                  f"- 环境：{tech.get('env', '未知')}",
                  f"- 设备：{tech.get('device', '未知')}",
                  f"- 数据限制：{tech.get('data_limit', '未知')}",
                  f"- 本机验证情况：{tech.get('status_note', '未知')}", '']
    lines += ['', '---', '',
              '本文件只描述程序**实际做过**的事。未接入的方法不会因为在文档里出现就变成可运行；'
              '“运行成功”也不等于科学结论成立。', '']
    return '\n'.join(lines)


def main():
    out = ROOT / 'docs' / '模型介绍.md'
    out.write_text(build(), encoding='utf-8')
    print(out)


if __name__ == '__main__':
    main()
