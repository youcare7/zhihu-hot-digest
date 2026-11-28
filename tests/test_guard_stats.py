# -*- coding: utf-8 -*-
"""防幻觉透明度：guard_stats 只由代码计数，如实反映本次生成拦下了什么。

运行：python tests/test_guard_stats.py（在仓库根目录下）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

CANDS = [
    {"Title": "问题", "Url": "https://www.zhihu.com/question/1/answer/11", "ContentText": "第一条回答原文"},
    {"Title": "问题", "Url": "https://www.zhihu.com/question/1/answer/12", "ContentText": "第二条回答原文"},
    {"Title": "站外", "Url": "https://news.ifeng.com/c/x", "ContentText": "媒体报道"},
]


def hot(n):
    return [{"Title": f"热点{i}", "Url": f"https://www.zhihu.com/question/{100 + i}"} for i in range(1, n + 1)]


class TestGuardStats(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False
        app.SIDES_REQUIRE_EVIDENCE = True
        app.QUOTA_GUARD_DISABLED = True
        app._log = lambda m: None
        app._run_enrichment_pass_orig = app._run_enrichment_pass
        app._run_crosscheck_pass_orig = app._run_crosscheck_pass
        app._run_enrichment_pass = lambda items: None
        app._run_crosscheck_pass = lambda items: None
        app.fetch_search = lambda title, rank, count=10: [dict(c) for c in CANDS]

    def tearDown(self):
        app._run_enrichment_pass = app._run_enrichment_pass_orig
        app._run_crosscheck_pass = app._run_crosscheck_pass_orig
        del app._run_enrichment_pass_orig
        del app._run_crosscheck_pass_orig
        super().tearDown()

    def _gen(self, title, text):
        n = int(title.replace("热点", ""))
        base = {"background": "背景", "focus": "焦点", "consensus": "", "gap": "缺口"}
        if n == 1:   # 合规真分歧；延伸阅读含越界 / 站外 / 重复
            return dict(base, type="dispute", claim_type="value",
                        sides=[{"label": "甲", "claim": "一", "evidence": [1]},
                               {"label": "乙", "claim": "二", "evidence": [2, 9]}],
                        reading=[{"index": 9}, {"index": 3}, {"index": 1}, {"index": 1}])
        if n == 2:   # 两派只引同一条 → 共用移除 → 找不到依据 → 降级
            return dict(base, type="dispute", claim_type="fact",
                        sides=[{"label": "甲", "claim": "一", "evidence": [1]},
                               {"label": "乙", "claim": "二", "evidence": [1]}],
                        reading=[])
        if n == 3:   # 声称分歧却只给一派 → 降级
            return dict(base, type="dispute", sides=[{"label": "甲", "claim": "一"}], reading=[])
        return dict(base, type="consensus", reading=[{"index": True}])

    def test_counts_match_what_was_blocked(self):
        app.generate = self._gen
        app.api_hot_list = lambda: hot(4)
        gs = app.build_brief()["guard_stats"]
        self.assertEqual(gs["reading_index_dropped"], 2)    # #1 的 9、#4 的 True
        self.assertEqual(gs["reading_link_dropped"], 1)     # #1 的站外链接
        self.assertEqual(gs["reading_dup_dropped"], 1)      # #1 第二次引用 index=1
        self.assertEqual(gs["sides_evidence_dropped"], 1)   # #1 乙方的 9
        self.assertEqual(gs["sides_shared_removed"], 1)     # #2 两派共用第 1 条
        self.assertEqual(gs["dispute_no_evidence"], 1)      # #2
        self.assertEqual(gs["dispute_downgraded"], 1)       # #3
        self.assertEqual(gs["items_generated"], 4)
        self.assertEqual(gs["items_reused"], 0)

    def test_stats_reset_between_briefs(self):
        app.generate = self._gen
        app.api_hot_list = lambda: hot(4)
        app.build_brief()
        app.api_hot_list = lambda: [{"Title": "热点4", "Url": "https://www.zhihu.com/question/104"}]
        gs = app.build_brief()["guard_stats"]
        self.assertEqual(gs["sides_shared_removed"], 0)
        self.assertEqual(gs["reading_index_dropped"], 1)

    def test_reused_items_not_recounted(self):
        app.INCREMENTAL_REFRESH = True
        app.generate = self._gen
        app.api_hot_list = lambda: hot(4)
        app.atomic_write_json(app.cache_path(), app.build_brief())
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = None
        gs = app.build_brief()["guard_stats"]
        # #1、#4 健康可复用；#2、#3 被降级后是待验证（有背景）也可复用
        self.assertEqual(gs["items_reused"], 4)
        self.assertEqual(gs["items_generated"], 0)
        self.assertEqual(sum(gs[k] for k in app.GUARD_STAT_KEYS), 0)

    def test_fallback_and_showcase_hide_stats(self):
        fb = self.tmp_path / "fallback.json"
        fb.write_text(json.dumps({"date": "2026-11-26", "items": [], "guard_stats": {"reading_index_dropped": 3}}),
                      encoding="utf-8")
        app.FALLBACK_PATH = fb
        self.assertNotIn("guard_stats", app.load_fallback())

    def test_mock_brief_has_stats(self):
        app.MOCK_MODE = True
        gs = app.build_brief()["guard_stats"]
        self.assertEqual(set(app.GUARD_STAT_KEYS) | {"items_generated", "items_reused"}, set(gs))


if __name__ == "__main__":
    unittest.main()
