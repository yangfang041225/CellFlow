"""检查 Markdown 里的本地链接是否存在（忽略 http/锚点/图片外链）。"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'\[[^\]]*\]\(([^)]+)\)')


def main():
    problems = []
    candidates = list(ROOT.glob('*.md')) + list((ROOT / 'docs').rglob('*.md'))
    for path in sorted(candidates):
        if any(part in ('.venv', '.venv-velovi', 'node_modules', '.tmp', '.runtimes', 'results')
               or part.startswith('_backup') for part in path.parts):
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for match in LINK.finditer(text):
            target = match.group(1).strip().split('#')[0].split(' ')[0]
            if not target or target.startswith(('http://', 'https://', 'mailto:', '#')):
                continue
            if target.startswith('data:'):
                continue
            candidate = (path.parent / target).resolve()
            if not candidate.exists():
                problems.append((path.relative_to(ROOT), target))
    if problems:
        for source, target in problems:
            print(f'MISSING {source} -> {target}')
        print(f'BROKEN_LINKS={len(problems)}')
        return 1
    print('DOC_LINKS_OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
