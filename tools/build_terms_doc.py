from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cellflow import terms  # noqa: E402

lines = ['# 术语参考表', '',
         '由 `tools/build_terms_doc.py` 从 `cellflow/terms.py` 生成。',
         '**参考翻译，需自行核对**；未收录的术语保持原文，不做模糊翻译或猜测。', '',
         '| 术语 | 参考中文 |', '|---|---|']
for item in terms.glossary():
    lines.append(f"| {item['term']} | {item['zh']} |")
lines += ['', '## 使用位置', '',
          '- 数据集详情「obs 摘要」：类别值若精确命中本表，显示 `原值（参考中文）`；否则只显示原值。',
          '- API：`GET /api/terms`。',
          '', '## 原则', '',
          '1. 只收录含义明确、无争议的通用词；',
          '2. 精确匹配（大小写不敏感），不做词形/语义推测；',
          '3. 参考翻译不能替代原始元数据或文献中的正式命名。']
out = ROOT / 'docs' / '术语表.md'
out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('written', out, 'terms', len(terms.glossary()))
