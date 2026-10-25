"""Streamlit 回退界面的冒烟测试：真正渲染五个页面，检查没有异常。

说明：主界面已迁移到 React（web/），本测试保护 Streamlit 回退入口
（app.py + cellflow/studio），确保迁移期间旧入口仍可用。
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HAS_APPTEST = importlib.util.find_spec('streamlit') is not None

PAGES = ('overview', 'data', 'model', 'results', 'reports')


def _texts(at):
    out = []
    for attr in ('markdown', 'caption', 'text', 'code', 'subheader', 'header', 'title',
                 'metric', 'label', 'dataframe', 'link_button', 'button', 'success', 'info'):
        try:
            elements = getattr(at, attr)
        except Exception:
            continue
        for element in elements:
            for field in ('value', 'label'):
                value = getattr(element, field, None)
                if isinstance(value, str):
                    out.append(value)
    return ' '.join(out)


@unittest.skipUnless(HAS_APPTEST, 'streamlit 未安装')
class UiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from streamlit.testing.v1 import AppTest
        from streamlit.util import calc_hash
        cls.AppTest = AppTest
        cls.calc_hash = staticmethod(calc_hash)
        cls.queue_db = ROOT / 'results' / 'queue.sqlite'
        cls.had_queue = cls.queue_db.exists()

    @classmethod
    def tearDownClass(cls):
        if not cls.had_queue and cls.queue_db.exists():
            cls.queue_db.unlink()
            for extra in ('-wal', '-shm'):
                candidate = Path(str(cls.queue_db) + extra)
                if candidate.exists():
                    candidate.unlink()

    def _open(self, page_hash=None):
        at = self.AppTest.from_file(str(ROOT / 'app.py'), default_timeout=180)
        at.run()
        if page_hash:
            at._page_hash = self.calc_hash(page_hash)
            at.run()
        return at

    def _assert_clean(self, at, page):
        errors = [f'{e.type}: {e.value}' for e in at.exception]
        self.assertEqual(errors, [], f'{page} 渲染出现异常：{errors}')
        return _texts(at)

    def test_overview_page(self):
        body = self._assert_clean(self._open('overview'), '概览')
        for keyword in ('概览', '数据集', '下一步'):
            self.assertIn(keyword, body)

    def test_data_page(self):
        body = self._assert_clean(self._open('data'), '数据')
        for keyword in ('数据', '检查结果', '方法适用性'):
            self.assertIn(keyword, body)

    def test_model_page(self):
        body = self._assert_clean(self._open('model'), '模型分析')
        for keyword in ('模型分析', '前置条件', '参数'):
            self.assertIn(keyword, body)

    def test_results_page(self):
        body = self._assert_clean(self._open('results'), '分析结果')
        self.assertTrue(body.strip(), '分析结果页没有渲染出任何文本')

    def test_reports_page(self):
        body = self._assert_clean(self._open('reports'), '报告')
        self.assertTrue(body.strip(), '报告页没有渲染出任何文本')

    def test_reading_pages_does_not_queue_jobs(self):
        """阅读页面绝不能产生任务。"""
        from cellflow.queue import list_jobs
        before = len(list_jobs())
        for page in PAGES:
            self._open(page)
        self.assertEqual(len(list_jobs()), before, '阅读页面时不应创建任务')
