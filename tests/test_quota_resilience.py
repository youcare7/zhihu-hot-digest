# -*- coding: utf-8 -*-
"""配额韧性：防劣化、榜单签名短路、分层预算与独立核验时间预算。"""
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


def make_item(rank, qid, *, title=None, background=None, full=False):
    item = {
        "rank": rank,
        "title": title or f"热点{qid}",
        "question_url": f"https://www.zhihu.com/question/{qid}",
        "type": "consensus",
        "background": background or f"背景-{qid}",
        "focus": "焦点",
        "consensus": "共识",
        "gap": "目前无公开可核验材料",
        "reading": [],
        "viewpoints": {},
    }
    if full:
        item.update({
            "punch": "锐评",
            "vote": {"options": ["甲", "乙"]},
            "quotes": [{"text": "评论"}],
            "lou": {"items": [{"text": "导读"}]},
            "crosscheck": {"fact": "核验"},
            "cross_platform": {"multi": True},
            "kanshan": {"gif": "assets/ks-pc.webp"},
        })
    return item


def make_brief(items):
    return {
        "date": app.today_str(),
        "source": "live",
        "generated_at": app.now_str(),
        "notice": "",
        "items": items,
    }


def hot(*qids):
    return [{"Title": f"热点{qid}", "Url": f"https://www.zhihu.com/question/{qid}"}
            for qid in qids]


class TestQuotaResilience(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False
        app.CACHE_TTL_SECONDS = 3600
        app.REFRESH_COOLDOWN_SECONDS = 0
        app._REFRESH_NOT_BEFORE = 0.0
        app.COVERAGE_TOLERANCE = 4
        app.COVERAGE_SKIP_REGEN_RATIO = 0.6
        app.QUOTA_GUARD_DISABLED = False
        app._QUOTA = app.QuotaGuard(self.tmp_path / "quota.json")
        app.QUOTA_LIMITS.update({"hot_list": 100, "search": 2000, "zhida": 80,
                                 "model_alt": 500, "web_search": 50})
        self.logs = []
        app._log = self.logs.append

    def _write_stale_cache(self, brief):
        path = app.cache_path()
        app.atomic_write_json(path, brief)
        old_time = time.time() - 2 * 3600
        os.utime(path, (old_time, old_time))
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = None
            app._BRIEF_CACHE_DATA = None
        return path

    def _run_refresh(self):
        self.assertTrue(app._refresh_claim())
        app._background_refresh()

    def test_field_coverage_uses_feature_weights(self):
        data = make_brief([
            make_item(1, 101, full=True),
            dict(make_item(2, 102), punch="锐评", crosscheck={"fact": "核验"}),
        ])
        self.assertEqual(app._field_coverage(data), 13)  # 9 + 2 + 2
        self.assertEqual(app._coverage_max(data), 18)
        self.assertEqual(app._field_coverage({"items": [None, {}]}), 0)

    def test_same_list_lower_coverage_keeps_old_cache(self):
        path = self._write_stale_cache(make_brief([make_item(1, 101, background="旧完整背景", full=True)]))
        before = path.read_text(encoding="utf-8")

        def hot_query_fails():
            raise RuntimeError("模拟签名查询失败")

        app.api_hot_list = hot_query_fails
        app.build_brief = lambda: make_brief([make_item(1, 101, background="新残缺背景")])
        self._run_refresh()

        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertTrue(any("字段覆盖显著劣化" in message for message in self.logs))

    def test_changed_list_accepts_fresh_items_despite_lower_coverage(self):
        path = self._write_stale_cache(make_brief([make_item(1, 101, background="旧背景", full=True)]))
        app.api_hot_list = lambda: hot(202)
        app.build_brief = lambda: make_brief([make_item(1, 202, background="新话题背景")])
        self._run_refresh()

        current = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(current["items"][0]["question_url"], "https://www.zhihu.com/question/202")
        self.assertEqual(current["items"][0]["background"], "新话题背景")
        self.assertFalse(any("字段覆盖显著劣化" in message for message in self.logs))

    def test_full_cache_and_unchanged_hot_list_skip_regeneration(self):
        path = self._write_stale_cache(make_brief([make_item(1, 101, full=True)]))
        before = path.read_text(encoding="utf-8")
        calls = {"hot": 0, "build": 0}

        def current_hot():
            calls["hot"] += 1
            return hot(101)

        def should_not_build():
            calls["build"] += 1
            return make_brief([])

        app.api_hot_list = current_hot
        app.build_brief = should_not_build
        self._run_refresh()

        self.assertEqual(calls, {"hot": 1, "build": 0})
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertTrue(any("跳过重生成" in message for message in self.logs))

    def test_partial_cache_with_same_list_still_regenerates(self):
        path = self._write_stale_cache(make_brief([
            dict(make_item(1, 101, background="旧残缺背景"), punch="仅锐评")
        ]))
        hot_calls = []
        build_calls = []
        app.api_hot_list = lambda: hot_calls.append(1) or hot(101)

        def rebuild():
            build_calls.append(1)
            return make_brief([make_item(1, 101, background="补齐后的背景", full=True)])

        app.build_brief = rebuild
        self._run_refresh()

        self.assertEqual(build_calls, [1])
        self.assertEqual(hot_calls, [])  # 未达到短路覆盖阈值，不额外查询签名
        current = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(current["items"][0]["background"], "补齐后的背景")

    def test_main_generation_budget_reserves_quota_for_later_passes(self):
        app.ZHIDA_MAIN_BUDGET = 1
        app.GEN_BUDGET_SECONDS = 0
        app.api_hot_list = lambda: hot(101, 102, 103)
        app.fetch_search = lambda title, rank: [
            {"Title": "候选", "Url": "https://www.zhihu.com/question/1/answer/9", "ContentText": "材料"}
        ]
        generate_calls = []

        def counted_generate(title, text):
            generate_calls.append(title)
            self.assertTrue(app._QUOTA.attempt("zhida"))
            return {"type": "consensus", "background": f"背景-{title}", "focus": "焦点",
                    "consensus": "共识", "gap": "目前无公开可核验材料", "reading": []}

        app.generate = counted_generate
        with mock.patch.object(app, "_run_enrichment_pass", lambda items: None), \
                mock.patch.object(app, "_run_crosscheck_pass", lambda items: None):
            brief = app.build_brief()

        self.assertEqual(generate_calls, ["热点101"])
        self.assertEqual(app._QUOTA.used("zhida"), 1)
        self.assertEqual([it["background"] for it in brief["items"]],
                         ["背景-热点101", app.NO_BACKGROUND, app.NO_BACKGROUND])
        self.assertTrue(any("主生成分层预算耗尽" in message for message in self.logs))

    def test_main_respects_daily_reserve_when_quota_partly_used(self):
        app.ZHIDA_MAIN_BUDGET = 45
        app.ZHIDA_ENRICH_BUDGET = 20
        app.ZHIDA_CROSSCHECK_BUDGET = 15
        for _ in range(50):
            self.assertTrue(app._QUOTA.attempt("zhida"))
        app.GEN_BUDGET_SECONDS = 0
        app.api_hot_list = lambda: hot(101)
        generate_calls = []

        def unexpected_generate(title, text):
            generate_calls.append(title)
            self.assertTrue(app._QUOTA.attempt("zhida"))
            return {"type": "consensus", "background": "不应生成", "reading": []}

        app.generate = unexpected_generate
        with mock.patch.object(app, "_run_enrichment_pass", lambda items: None), \
                mock.patch.object(app, "_run_crosscheck_pass", lambda items: None):
            brief = app.build_brief()

        self.assertEqual(generate_calls, [])
        self.assertEqual(app._QUOTA.used("zhida"), 50)
        self.assertEqual(brief["items"][0]["background"], app.NO_BACKGROUND)

    def test_enrichment_keeps_crosscheck_daily_reserve(self):
        app.ZHIDA_ENRICH_BUDGET = 20
        app.ZHIDA_CROSSCHECK_BUDGET = 15
        for _ in range(60):
            self.assertTrue(app._QUOTA.attempt("zhida"))
        enrich_calls = []

        def counted_enrich(item):
            enrich_calls.append(item["rank"])
            self.assertTrue(app._QUOTA.attempt("zhida"))
            item["punch"] = "锐评"
            return True

        items = [make_item(rank, 100 + rank) for rank in range(1, 7)]
        with mock.patch.object(app, "_enrich_item", counted_enrich):
            app._run_enrichment_pass(items)

        self.assertEqual(enrich_calls, [1, 2, 3, 4, 5])
        self.assertEqual(app._QUOTA.used("zhida"), 65)  # 给 crosscheck 留下完整 15 次

    def test_enrichment_budget_stops_before_second_item(self):
        app.ZHIDA_ENRICH_BUDGET = 1
        enrich_calls = []

        def counted_enrich(item):
            enrich_calls.append(item["rank"])
            self.assertTrue(app._QUOTA.attempt("zhida"))
            item["punch"] = "锐评"
            return True

        items = [make_item(1, 101), make_item(2, 102)]
        with mock.patch.object(app, "_enrich_item", counted_enrich):
            app._run_enrichment_pass(items)

        self.assertEqual(enrich_calls, [1])
        self.assertIn("punch", items[0])
        self.assertNotIn("punch", items[1])

    def test_crosscheck_budget_stops_before_second_item(self):
        app.ZHIDA_CROSSCHECK_BUDGET = 1
        app.CROSSCHECK_MAX = 10
        check_calls = []

        def counted_check(item):
            check_calls.append(item["rank"])
            self.assertTrue(app._QUOTA.attempt("zhida"))
            item["crosscheck"] = {"fact": "核验"}
            return True

        items = [make_item(1, 101), make_item(2, 102)]
        with mock.patch.object(app, "_crosscheck_item", counted_check):
            app._run_crosscheck_pass(items)

        self.assertEqual(check_calls, [1])
        self.assertIn("crosscheck", items[0])
        self.assertNotIn("crosscheck", items[1])
        self.assertTrue(any("crosscheck 分层预算耗尽" in message for message in self.logs))

    def test_crosscheck_deadline_gets_independent_budget(self):
        app.CROSSCHECK_BUDGET_SECONDS = 90
        app._GEN_DEADLINE = time.monotonic() - 10
        app._extend_deadline_for_crosscheck()
        self.assertGreater(app._GEN_DEADLINE, time.monotonic())
        self.assertLessEqual(app._GEN_DEADLINE - time.monotonic(), 91)

        app._GEN_DEADLINE = 0.0
        app._extend_deadline_for_crosscheck()
        self.assertEqual(app._GEN_DEADLINE, 0.0)


if __name__ == "__main__":
    unittest.main()
