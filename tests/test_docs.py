"""文档链接与术语表生成的一致性检查。"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Docs(unittest.TestCase):
    def test_docs_links_resolve(self):
        proc = subprocess.run([sys.executable, 'tools/check_docs_links.py'],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding='utf-8', timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('DOC_LINKS_OK', proc.stdout)

    def test_terms_doc_matches_generated(self):
        from cellflow import terms
        text = (ROOT / 'docs' / '术语表.md').read_text(encoding='utf-8')
        for item in terms.glossary()[:10]:
            self.assertIn(item['term'], text)
            self.assertIn(item['zh'], text)


if __name__ == '__main__':
    unittest.main()
