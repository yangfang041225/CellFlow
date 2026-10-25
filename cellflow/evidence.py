"""证据等级（L0–L4）：工程输出不能自动升级为科学证据。

- L0 工程输出：任务完成、文件存在、可打开
- L1 数值验证：结构/概率/有限值等数值约束通过
- L2 参考实现对照：与官方实现同配置比较（必须由 benchmark 显式写入，不自动）
- L3 生物学证据支持：已知方向/实验观察/独立生物学参照
- L4 独立实验验证：谱系追踪、扰动等

运行时只自动给出 L0/L1；L2+ 必须由外部验证记录显式声明。
"""
from __future__ import annotations

from pathlib import Path

LEVELS = {
    0: '工程输出',
    1: '数值验证',
    2: '参考实现对照',
    3: '生物学证据支持',
    4: '独立实验验证',
}

_STATUS_LABEL = {'met': '已满足', 'not_reached': '未达到', 'not_assessed': '未评估'}


def assess(result, quality, provenance=None, folder=None) -> dict:
    provenance = provenance or {}
    items = []
    artifacts = provenance.get('artifacts') or {}
    l0_ok = bool(provenance.get('status') == 'completed')
    items.append({
        'level': 0, 'name': LEVELS[0], 'status': 'met' if l0_ok else 'not_reached',
        'evidence': ['任务完成，结果目录与报告文件可生成' if l0_ok else '任务未完成'],
    })
    checks = (quality or {}).get('checks') or []
    hard_ok = bool((quality or {}).get('ok'))
    items.append({
        'level': 1, 'name': LEVELS[1], 'status': 'met' if (hard_ok and checks) else 'not_reached',
        'evidence': [f'结构/数值检查 {len(checks)} 项，全部通过' if hard_ok
                     else '数值/结构检查未通过或缺失'],
    })
    reference = (provenance or {}).get('reference_comparison')
    items.append({
        'level': 2, 'name': LEVELS[2],
        'status': 'met' if reference else 'not_reached',
        'evidence': ['存在显式参考实现对照记录' if reference else
                     '本次运行没有与官方实现的同配置对照；运行成功不会自动升级到 L2'],
    })
    items.append({
        'level': 3, 'name': LEVELS[3], 'status': 'not_reached',
        'evidence': ['需要独立生物学参照（已知分化方向/实验观察）；本程序不自动判定'],
    })
    items.append({
        'level': 4, 'name': LEVELS[4], 'status': 'not_reached',
        'evidence': ['需要独立实验验证（谱系追踪/扰动），本机流程不提供'],
    })
    met = [item['level'] for item in items if item['status'] == 'met']
    return {
        'items': items,
        'max_level': max(met) if met else -1,
        'max_level_name': LEVELS.get(max(met)) if met else '无',
        'note': ('L2+ 只能由独立的参考对照/生物学证据显式写入；'
                 '工程测试通过不等于科学结论可信。'),
    }


def markdown(levels) -> str:
    if not levels:
        return ''
    rows = ['| 等级 | 名称 | 状态 | 依据 |', '|---|---|---|---|']
    for item in levels.get('items') or []:
        rows.append(f"| L{item['level']} | {item['name']} | "
                    f"{_STATUS_LABEL.get(item['status'], item['status'])} | "
                    f"{'；'.join(item.get('evidence') or [])} |")
    return ('### 4.12 证据等级\n\n' + '\n'.join(rows) +
            f"\n\n本次最高：**L{levels.get('max_level')} · {levels.get('max_level_name')}**。"
            f"\n\n> {levels.get('note', '')}\n")
