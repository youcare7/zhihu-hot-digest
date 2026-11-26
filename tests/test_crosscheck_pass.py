# -*- coding: utf-8 -*-
"""跨平台核验 crosscheck pass 回归测试（2026-11-26）。

运行：python tests/test_crosscheck_pass.py（在仓库根目录下）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

CANDIDATES = [
    {"Title": "微博热议：网友吵翻", "Url": "https://weibo.com/abc", "AuthorName": "微博", "ContentText": "摘要一"},
    {"Title": "知乎讨论：理性分析", "Url": "https://zhuanlan.zhihu.com/p/1", "AuthorName": "知乎", "ContentText": "摘要二"},
]

CROSSCHECK_OUT = {
    "weibo": "微博网友情绪偏向支持",
    "zhihu": "知乎站内偏理性拆数据",
    "fact": "两边材料都未给出官方口径",
    "sources": [{"name": "微博热议", "index": 1}, {"name": "知乎讨论", "index": 2}],
}


def live_item():
    return {"rank": 1, "title": "测试热点", "type": "dispute",
            "background": "背景说明", "focus": "焦点", "consensus": "共识",
            "sides": [{"label": "甲", "claim": "一"}, {"label": "乙", "claim": "二"}],
            "gap": "缺口"}


class TestCrosscheckPass(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.RETRY_WAIT = 0
        app.RETRY_TIMES = 1
        app.QUOTA_GUARD_DISABLED = False
        app.MOCK_MODE = False
        app.GEN_BUDGET_SECONDS = 0
        app._GEN_DEADLINE = 0.0
        app._QUOTA = app.QuotaGuard(self.tmp_path / "quota.json")
        app.QUOTA_LIMITS.update({"hot_list": 100, "search": 2000, "zhida": 50,
                                 "model_alt": 500, "web_search": 50})
        app.CROSSCHECK_MAX = 10
        self.web_calls = []
        self.chat_calls = []

    def _web_search(self, query, count=app.WEB_SEARCH_COUNT):
        self.web_calls.append(query)
        return [dict(c) for c in CANDIDATES]

    def _chat_ok(self, url, headers, payload, label):
        self.chat_calls.append(payload["messages"][0]["content"])
        return json.dumps(CROSSCHECK_OUT, ensure_ascii=False)

    def test_valid_flow_maps_whitelist_urls(self):
        app.api_web_search = self._web_search
        app._chat = self._chat_ok
        item = live_item()
        app._run_crosscheck_pass([item])
        cc = item.get("crosscheck") or {}
        self.assertEqual(cc.get("weibo"), "微博网友情绪偏向支持")
        self.assertEqual([s["url"] for s in cc.get("sources", [])],
                         ["https://weibo.com/abc", "https://zhuanlan.zhihu.com/p/1"])
        self.assertEqual(len(self.chat_calls), 1)

    def test_source_index_outside_candidates_rejected(self):
        app.api_web_search = self._web_search
        out = dict(CROSSCHECK_OUT, sources=[{"name": "幽灵信源", "index": 9}])

        def chat(url, headers, payload, label):
            self.chat_calls.append(1)
            return json.dumps(out, ensure_ascii=False)

        app._chat = chat
        item = live_item()
        app._run_crosscheck_pass([item])
        self.assertNotIn("crosscheck", item)  # 无白名单信源 → 整体丢弃
        self.assertEqual(len(self.chat_calls), 2)  # 重试一次后放弃

    def test_dirty_source_types_dropped_and_dedup(self):
        app.api_web_search = self._web_search
        out = {"weibo": "情绪", "zhihu": "切面", "fact": "结论",
               "sources": [{"name": "a", "index": 1}, {"name": "b", "index": 1},
                           "junk", {"name": None, "index": 2}]}
        got = app._sanitize_crosscheck(out, CANDIDATES)
        self.assertEqual(len(got["sources"]), 1)  # index=1 去重后只留 1 条
        self.assertEqual(got["sources"][0]["url"], "https://weibo.com/abc")

    def test_web_search_quota_exhaustion_stops_pass(self):
        attempts = {"n": 0}

        def quota_blocked(query, count=app.WEB_SEARCH_COUNT):
            attempts["n"] += 1
            raise app.UpstreamError("全网搜索当日配额已达上限（配额守卫）")

        app.QUOTA_LIMITS.update({"web_search": 0})
        app.api_web_search = quota_blocked
        app._chat = self._chat_ok
        first, second = live_item(), live_item()
        second["rank"] = 3
        app._run_crosscheck_pass([first, second])
        self.assertEqual(attempts["n"], 1)  # 配额错误第一次出现即停止整个 pass
        self.assertEqual(self.chat_calls, [])
        self.assertNotIn("crosscheck", first)
        self.assertNotIn("crosscheck", second)

    def test_search_error_skips_item_but_pass_continues(self):
        calls = {"n": 0}

        def flaky_search(query, count=app.WEB_SEARCH_COUNT):
            calls["n"] += 1
            if calls["n"] == 1:
                raise app.UpstreamError("网络抖动")
            return [dict(c) for c in CANDIDATES]

        app.api_web_search = flaky_search
        app._chat = self._chat_ok
        first, second = live_item(), live_item()
        second["rank"] = 3
        app._run_crosscheck_pass([first, second])
        self.assertNotIn("crosscheck", first)
        self.assertIn("crosscheck", second)  # 非配额错误不中断 pass

    def test_no_candidates_no_zhida_call(self):
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: []
        app._chat = self._chat_ok
        item = live_item()
        app._run_crosscheck_pass([item])
        self.assertEqual(self.chat_calls, [])
        self.assertNotIn("crosscheck", item)

    def test_bad_json_retried_once_then_healthy(self):
        app.api_web_search = self._web_search

        def broken(url, headers, payload, label):
            self.chat_calls.append(1)
            return "{broken"

        app._chat = broken
        item = live_item()
        app._run_crosscheck_pass([item])
        self.assertEqual(len(self.chat_calls), 2)
        self.assertNotIn("crosscheck", item)
        self.assertEqual(item["background"], "背景说明")  # 条目绝不降级

    def test_max_zero_and_mock_skip(self):
        app.CROSSCHECK_MAX = 0
        app.api_web_search = self._web_search
        app._chat = self._chat_ok
        item = live_item()
        app._run_crosscheck_pass([item])
        self.assertEqual(self.web_calls, [])

        app.CROSSCHECK_MAX = 10
        app.MOCK_MODE = True
        app._run_crosscheck_pass([item])
        self.assertEqual(self.web_calls, [])

    def test_existing_crosscheck_and_degraded_not_touched(self):
        app.api_web_search = self._web_search
        app._chat = self._chat_ok
        kept = live_item()
        kept["crosscheck"] = {"weibo": "旧", "zhihu": "旧", "fact": "旧",
                            "sources": [{"name": "旧", "url": "https://x.example"}]}
        degraded = live_item()
        degraded["background"] = app.NO_BACKGROUND
        app._run_crosscheck_pass([kept, degraded])
        self.assertEqual(kept["crosscheck"]["weibo"], "旧")
        self.assertEqual(self.web_calls, [])  # 两条都未发起检索
        self.assertNotIn("crosscheck", degraded)

    def test_max_caps_number_of_checks(self):
        app.CROSSCHECK_MAX = 1
        app.api_web_search = self._web_search
        app._chat = self._chat_ok
        items = [live_item()] + [dict(live_item(), rank=r) for r in (2, 3)]
        app._run_crosscheck_pass(items)
        self.assertEqual(len(self.web_calls), 1)  # 只核验了第一条

    def test_prompt_red_lines_present(self):
        prompt = app._build_crosscheck_prompt(live_item(), CANDIDATES)
        self.assertIn("不得新增任何事实", prompt)
        self.assertIn("1 | 微博热议", prompt)


if __name__ == "__main__":
    unittest.main()
