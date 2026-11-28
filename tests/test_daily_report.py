# -*- coding: utf-8 -*-
"""今日战报（guard_summary）与 /healthz 站内相关讨论命中率可观测性。

运行：python tests/test_daily_report.py（在仓库根目录下）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


def item(**kw):
    base = {"rank": 1, "title": "t", "type": "consensus", "claim_type": "",
            "gap": "缺口材料", "background": "背景"}
    base.update(kw)
    return base


class TestDailyReportGuardSummary(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app._reset_guard_stats()

    def tearDown(self):
        app._reset_guard_stats()
        super().tearDown()

    def test_no_interception_no_summary(self):
        digest = app.build_digest([item()])
        self.assertNotIn("guard_summary", digest)

    def test_interceptions_are_summarized_in_chinese(self):
        app._guard("reading_index_dropped", 2)
        app._guard("dispute_no_evidence", 1)
        digest = app.build_digest([item()])
        self.assertIn("guard_summary", digest)
        self.assertIn("延伸阅读编号越界 2 次", digest["guard_summary"])
        self.assertIn("两派绑不上证据降级 1 次", digest["guard_summary"])
        self.assertIn("共 3 处", digest["guard_summary"])

    def test_all_guard_keys_have_labels(self):
        for key in app.GUARD_STAT_KEYS:
            self.assertIn(key, app.GUARD_STAT_LABELS, f"{key} 缺少人话标签")


class TestHealthzGapLinkCounters(AppStateTestCase):
    def test_verbose_reports_gap_link_hit_rate(self):
        app.DATA_DIR = self.tmp_path
        app.FALLBACK_PATH = self.tmp_path / "fallback.json"
        app.ZHIHU_ACCESS_SECRET = "x"
        os.environ["HEALTHZ_VERBOSE"] = "1"
        self.addCleanup(lambda: os.environ.pop("HEALTHZ_VERBOSE", None))
        import json
        app.cache_path().write_text(json.dumps({"items": [
            {"title": "a", "gap": "缺口A", "background": "背景",
             "gap_link": {"url": "https://zhihu.com/x", "title": "t"}},
            {"title": "b", "gap": "缺口B", "background": "背景"},  # 该搜但没找到
            {"title": "c", "gap": app.EMPTY_GAP, "background": "背景"},  # 没有缺口，不计分母
            {"title": "d", "gap": "目前无公开可核验材料", "background": "背景"},  # 不计分母
            {"title": "e", "gap": "缺口E", "background": app.NO_BACKGROUND},  # 降级条目不计分母
        ]}, ensure_ascii=False), encoding="utf-8")
        body = json.loads(app.app.test_client().get("/healthz").get_data(as_text=True))
        self.assertEqual(body["gap_link_found_count"], 1)
        self.assertEqual(body["gap_link_eligible_count"], 2)


if __name__ == "__main__":
    unittest.main()
