# -*- coding: utf-8 -*-
"""全网证据核验单条试点回归测试（任务书 2026-11-20 §6，共 7 项）。

运行：python tests/test_web_evidence.py（在仓库根目录下）
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

HTML = (pathlib.Path(__file__).resolve().parent.parent / "demo" / "index.html").read_text(encoding="utf-8")
OUT_ITEM = {"Title": "站外原始标题", "Url": "https://news.example.com/report/1",
            "AuthorName": "记者甲", "EditTime": 1753632000}


def verify_chat(url, headers, payload, label):
    return ('{"type":"verify","background":"甲背景","focus":"焦点",'
            '"gap":"关键缺口","consensus":""}')


class TestWebEvidence(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.RETRY_WAIT = 0
        app.RETRY_TIMES = 1
        app.QUOTA_GUARD_DISABLED = False
        app._chat = verify_chat
        app.fetch_search = lambda title, rank: [
            {"Title": "候选回答", "ContentText": "候选摘要", "Url": "https://www.zhihu.com/question/1/answer/9"},
        ]

    def _reset_web(self, name):
        app._QUOTA = app.QuotaGuard(self.tmp_path / name)
        app.QUOTA_LIMITS.update({"hot_list": 100, "search": 2000, "zhida": 90,
                                 "model_alt": 500, "web_search": 20})
        app._WEB_SEARCH_TRIED.clear()
        app._WEB_EVIDENCE_BUDGET = 1

    def test_1_valid_result_found_and_single_budget(self):
        self._reset_web("w1.json")
        web_calls = {"n": 0}

        def counting_web_search(query, count=app.WEB_SEARCH_COUNT):
            web_calls["n"] += 1
            return [dict(OUT_ITEM)]

        app.api_web_search = counting_web_search
        app.api_hot_list = lambda: [
            {"Title": "热点甲", "Url": "https://www.zhihu.com/question/1"},
            {"Title": "热点乙", "Url": "https://www.zhihu.com/question/2"},
        ]
        brief = app.build_brief()
        items = brief["items"]
        ev = items[0].get("web_evidence") or {}
        self.assertEqual(ev.get("status"), "found")
        self.assertEqual(ev["title"], "站外原始标题")
        self.assertEqual(ev["url"], "https://news.example.com/report/1")
        self.assertEqual(ev["source"], "news.example.com")
        self.assertEqual(ev["published_at"], "2025-07-28")
        self.assertIn("热点甲", ev["query"])
        self.assertIn("关键缺口", ev["query"])
        self.assertTrue(ev["checked_at"])
        self.assertNotIn("web_evidence", items[1])
        self.assertEqual(web_calls["n"], 1)

    def test_2_no_result_and_frontend_copy(self):
        self._reset_web("w2.json")
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: []
        ev = app.build_web_evidence("热点丙", "缺口词")
        self.assertEqual(ev["status"], "no_result")
        self.assertTrue(ev["query"] and ev["checked_at"])
        self.assertIn("本次全网检索未返回可用的一手材料｜核验截至 ", HTML)
        self.assertIn("检索词：", HTML)
        for banned in ("全网没有", "不存在相关材料", "权威媒体均未披露", "全网核验无公开材料"):
            self.assertNotIn(banned, HTML)

    def test_3_upstream_errors_preserve_zhihu_fields(self):
        self._reset_web("w3.json")
        for label, exc in [
            ("403", app.UpstreamError("知乎接口请求失败 /api/v1/content/global_search：HTTP Error 403")),
            ("429", app.UpstreamError("知乎接口返回错误码 30001（Rate limit）：/api/v1/content/global_search")),
            ("超时", app.UpstreamError("知乎接口请求失败 /api/v1/content/global_search：timed out")),
        ]:
            app._WEB_SEARCH_TRIED.clear()
            app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT, _e=exc: (_ for _ in ()).throw(_e)
            ev = app.build_web_evidence(f"热点{label}", "缺口词")
            self.assertEqual(ev["status"], "error")

        self._reset_web("w3b.json")
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: (_ for _ in ()).throw(
            app.UpstreamError("知乎接口返回错误码 30001（Rate limit）")
        )
        item = app.process_item(1, {"Title": "热点丁", "Url": "https://www.zhihu.com/question/9"})
        self.assertEqual(item["web_evidence"]["status"], "error")
        self.assertEqual(item["type"], "verify")
        self.assertEqual(item["gap"], "关键缺口")
        self.assertEqual(item["background"], "甲背景")
        self.assertIn("ev.status === 'found'", HTML)
        self.assertIn("ev.status === 'no_result'", HTML)
        self.assertIn("error / skipped", HTML)

    def test_4_missing_url_or_title_or_zhihu_domain_dropped(self):
        self._reset_web("w4.json")
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [{"Title": "缺链接的条目"}]
        self.assertEqual(app.build_web_evidence("热点戊", "缺口词")["status"], "error")

        app._WEB_SEARCH_TRIED.clear()
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [{"Url": "https://x.example.com/a"}]
        self.assertEqual(app.build_web_evidence("热点戊", "缺口词")["status"], "error")

        app._WEB_SEARCH_TRIED.clear()
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [
            {"Title": "知乎站内结果", "Url": "https://www.zhihu.com/question/9"}
        ]
        self.assertEqual(app.build_web_evidence("热点戊", "缺口词")["status"], "error")

        app._WEB_SEARCH_TRIED.clear()
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [
            {"Title": "缺链接"}, {"Title": "可用站外标题", "Url": "https://ok.example.com/x"}
        ]
        ev = app.build_web_evidence("热点戊", "缺口词")
        self.assertEqual(ev["status"], "found")
        self.assertEqual(ev["title"], "可用站外标题")

    def test_5_retry_pass_reuses_web_evidence(self):
        self._reset_web("w5.json")
        upstream_hits = {"n": 0}

        def counting_zhihu_get(path, params=None):
            upstream_hits["n"] += 1
            return {"Items": [dict(OUT_ITEM)]}

        app.zhihu_get = counting_zhihu_get
        app.build_web_evidence("热点己", "缺口词")
        app.build_web_evidence("热点己", "缺口词")
        self.assertEqual(upstream_hits["n"], 1)
        self.assertEqual(app._QUOTA.used("web_search"), 1)

        app._WEB_EVIDENCE_BUDGET = 1
        hot = {"Title": "热点庚", "Url": "https://www.zhihu.com/question/10"}
        first = app.process_item(1, hot)
        second = app.process_item(1, hot)
        self.assertEqual(first["web_evidence"]["status"], "found")
        self.assertEqual(second["web_evidence"]["status"], "found")
        self.assertEqual(second["web_evidence"]["title"], first["web_evidence"]["title"])
        self.assertEqual(upstream_hits["n"], 2)
        self.assertEqual(app._QUOTA.used("web_search"), 2)

    def test_6_empty_gap_skips_web_evidence(self):
        self._reset_web("w6.json")
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: (_ for _ in ()).throw(
            app.UpstreamError("知乎接口返回错误码 30001（Rate limit）")
        )
        item = app.process_item(1, {"Title": "热点辛", "Url": "https://www.zhihu.com/question/11"})
        self.assertEqual(item["gap"], "关键缺口")
        self.assertEqual(item["focus"], "焦点")
        self.assertEqual(item["background"], "甲背景")

        app._chat = lambda url, headers, payload, label: (
            '{"type":"verify","background":"甲背景","focus":"焦点","gap":"","consensus":""}'
        )
        item = app.process_item(2, {"Title": "热点壬", "Url": "https://www.zhihu.com/question/12"})
        self.assertEqual(item["gap"], app.EMPTY_GAP)
        self.assertNotIn("web_evidence", item)

    def test_7_quota_zero_or_exhausted_skips_without_upstream_call(self):
        self._reset_web("w7.json")
        upstream_hits = {"n": 0}

        def counting_zhihu_get(path, params=None):
            upstream_hits["n"] += 1
            return {"Items": []}

        app.zhihu_get = counting_zhihu_get
        app.QUOTA_LIMITS["web_search"] = 0
        ev = app.build_web_evidence("热点癸", "缺口词")
        self.assertEqual(ev["status"], "skipped")
        self.assertEqual(upstream_hits["n"], 0)
        self.assertEqual(app._QUOTA.used("web_search"), 0)

        app.QUOTA_LIMITS["web_search"] = 1
        app._WEB_SEARCH_TRIED.clear()
        app.build_web_evidence("热点癸", "缺口词")
        ev = app.build_web_evidence("热点子", "缺口词")
        self.assertEqual(ev["status"], "skipped")
        self.assertEqual(upstream_hits["n"], 1)


if __name__ == "__main__":
    unittest.main()
