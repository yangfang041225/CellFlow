"""扫描模块内重复的顶层函数/类定义（审计 F40 的静态检查）。"""
from pathlib import Path
import ast
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    problems = []
    for path in sorted((ROOT / 'cellflow').rglob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError as exc:
            problems.append((path, f'SyntaxError: {exc}'))
            continue
        seen = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                if node.name in seen:
                    problems.append((path, f'{node.name} defined at {seen[node.name]} and {node.lineno}'))
                else:
                    seen[node.name] = node.lineno
    if problems:
        for path, message in problems:
            print(f'DUPLICATE {path.relative_to(ROOT)}: {message}')
        print(f'DUPLICATES={len(problems)}')
        return 1
    print('NO_DUPLICATE_DEFINITIONS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
