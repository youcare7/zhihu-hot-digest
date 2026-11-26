# -*- coding: utf-8 -*-
"""人工整理快照的来源标注：站外链接必须如实标出，不能冒充「实时生成」。

运行：python tests/test_provenance.py（在仓库根目录下）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


def brief(question_url, reading_url, **extra):
    data = {
        "date": app.today_str(),
        "source": "cache",
        "generated_at": "2026-11-21 19:40",
        "notice": app.NOTICE_LIVE,
        "items": [{
            "rank": 1, "title": "热点", "question_url": question_url,
            "type": app.TYPE_VERIFY, "claim_type": "", "background": "有效背景",
            "focus": "焦点", "consensus": "", "sides": [], "gap": "可核验材料",
            "reading": [{"title": "延伸", "url": reading_url, "reason": "r"}],
        }],
    }
    data.update(extra)
    return data


ZH_Q = "https://www.zhihu.com/question/1"
ZH_A = "https://www.zhihu.com/question/1/answer/2"
OFF = "https://news.ifeng.com/c/8yCw4liRNSv"


class TestProvenance(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False
        app._log = lambda msg: None
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = None
            app._BRIEF_CACHE_DATA = None

    def _cache(self, data):
        app.atomic_write_json(app.cache_path(), data)
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = None
        return app.load_today_cache()

    def test_offsite_cache_is_labelled_and_notice_replaced(self):
        got = self._cache(brief(OFF, OFF))
        self.assertEqual(got["source"], "cache")          # source 四值契约不变
        self.assertEqual(got["provenance"], "curated")
        self.assertEqual(got["offsite_links"], 2)
        self.assertEqual(got["notice"], app.NOTICE_CURATED)
        self.assertEqual(app.load_today_cache()["provenance"], "curated")  # 内存缓存副本同样带标注

    def test_pipeline_cache_untouched(self):
        got = self._cache(brief(ZH_Q, ZH_A))
        self.assertNotIn("provenance", got)
        self.assertEqual(got["notice"], app.NOTICE_LIVE)

    def test_explicit_flag_is_honoured(self):
        got = self._cache(brief(ZH_Q, ZH_A, provenance="curated"))
        self.assertEqual(got["provenance"], "curated")
        self.assertEqual(got["offsite_links"], 0)

    def test_fallback_is_labelled(self):
        path = self.tmp_path / "fallback.json"
        path.write_text(json.dumps(brief(ZH_Q, OFF), ensure_ascii=False), encoding="utf-8")
        app.FALLBACK_PATH = path
        got = app.load_fallback()
        self.assertEqual(got["source"], "fallback")
        self.assertEqual(got["provenance"], "curated")
        self.assertEqual(got["offsite_links"], 1)

    def test_lookalike_and_pseudo_protocol_count_as_offsite(self):
        data = {"items": [
            {"question_url": "https://zhihu.com.evil.com/q",
             "reading": [{"url": "javascript:alert(1)"}, {"url": ZH_A}, "bad"]},
            None, "x",
        ]}
        self.assertEqual(app._offsite_link_count(data), 2)
        self.assertEqual(app._offsite_link_count({"items": "oops"}), 0)
        self.assertIsNone(app.annotate_provenance(None))

    def test_labelled_web_sources_are_not_counted(self):
        data = {"items": [{
            "question_url": ZH_Q, "reading": [],
            "web_evidence": {"status": "found", "url": "https://www.gov.cn/x"},
            "crosscheck": {"sources": [{"url": OFF}]},
        }]}
        self.assertEqual(app._offsite_link_count(data), 0)

    def test_mock_brief_has_no_offsite_links(self):
        app.MOCK_MODE = True
        self.assertEqual(app._offsite_link_count(app.build_brief()), 0)


if __name__ == "__main__":
    unittest.main()
