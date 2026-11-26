# -*- coding: utf-8 -*-
"""配额守卫回归测试（P1 前置：接入全网搜索之前必须先有的按日计数守卫）。

运行：python tests/test_quota_guard.py（在仓库根目录下）
"""
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

BASE_LIMITS = {"hot_list": 100, "search": 2000, "zhida": 90, "model_alt": 500, "web_search": 0}


class TestQuotaGuard(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.RETRY_WAIT = 0
        app.QUOTA_GUARD_DISABLED = False
        app.QUOTA_LIMITS.update(BASE_LIMITS)

    def _fresh_guard(self, name="quota_counters.json"):
        g = app.QuotaGuard(self.tmp_path / name)
        app._QUOTA = g
        return g

    def test_failed_attempt_is_counted(self):
        guard = self._fresh_guard("t1.json")
        app.RETRY_TIMES = 1
        app.QUOTA_LIMITS["zhida"] = 5
        app._chat = lambda url, headers, payload, label: (_ for _ in ()).throw(app.UpstreamError("模拟上游 500"))
        with self.assertRaises(app.UpstreamError):
            app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        self.assertEqual(guard.used("zhida"), 1)

    def test_limit_blocks_upstream_calls(self):
        guard = self._fresh_guard("t2.json")
        app.QUOTA_LIMITS["zhida"] = 1
        calls = {"n": 0}

        def counting_chat(url, headers, payload, label):
            calls["n"] += 1
            return '{"choices":[{"message":{"content":"{}"}}]}'

        app._chat = counting_chat
        app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        with self.assertRaises(app.UpstreamError) as ctx:
            app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        self.assertIn("配额", str(ctx.exception))
        self.assertEqual(calls["n"], 1)
        self.assertEqual(guard.used("zhida"), 1)

    def test_429_retries_count_each_attempt(self):
        guard = self._fresh_guard("t3.json")
        app.QUOTA_LIMITS["zhida"] = 10
        app.RETRY_TIMES = 3
        app.RETRY_WAIT = 0
        app._chat = lambda url, headers, payload, label: (_ for _ in ()).throw(app.UpstreamError("429 限流"))
        with self.assertRaises(app.UpstreamError):
            app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        self.assertEqual(guard.used("zhida"), 3)

    def test_day_rollover_resets_counter(self):
        guard = self._fresh_guard("t4.json")
        app.QUOTA_LIMITS["zhida"] = 100
        app._chat = lambda url, headers, payload, label: '{"choices":[{"message":{"content":"{}"}}]}'
        app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        self.assertEqual(guard.used("zhida"), 1)
        app.today_str = lambda: "2999-01-01"
        self.assertEqual(guard.used("zhida"), 0, "跨日后 used() 应立即视为 0")
        app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        self.assertEqual(guard.used("zhida"), 1, "跨日后计数应清零并从 1 重新累计")

    def test_persistence_across_instances(self):
        path = self.tmp_path / "t5.json"
        app._QUOTA = app.QuotaGuard(path)
        app._chat = lambda url, headers, payload, label: '{"choices":[{"message":{"content":"{}"}}]}'
        app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        g2 = app.QuotaGuard(path)
        self.assertEqual(g2.used("zhida"), 2)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["counts"]["zhida"], 2)

    def test_default_limits_within_official_quotas(self):
        self.assertLessEqual(self._saved_quota_limits["hot_list"], 100)
        self.assertLessEqual(self._saved_quota_limits["search"], 5000)
        self.assertLessEqual(self._saved_quota_limits["zhida"], 100)
        self.assertEqual(self._saved_quota_limits["web_search"], 0)

    def test_search_quota_degrades_single_item(self):
        self._fresh_guard("t7.json")
        app.QUOTA_LIMITS["search"] = 1
        app.QUOTA_LIMITS["zhida"] = 100
        app.RETRY_TIMES = 1
        app.RETRY_WAIT = 0
        app._chat = lambda url, headers, payload, label: (
            '{"type":"consensus","background":"甲背景","focus":"焦点","gap":"缺口","consensus":"结论"}'
        )
        app.api_hot_list = lambda: [
            {"Title": "热点甲", "Url": "https://www.zhihu.com/question/1"},
            {"Title": "热点乙", "Url": "https://www.zhihu.com/question/2"},
        ]
        app.fetch_search = app.api_search
        upstream_hits = {"n": 0}

        def counting_zhihu_get(path, params=None):
            upstream_hits["n"] += 1
            if path == "/api/v1/content/hot_list":
                return {"Items": [{"Title": "x", "Url": "u"}]}
            return {"Items": [{"Title": "候选", "Url": "https://www.zhihu.com/question/1/answer/9"}]}

        app.zhihu_get = counting_zhihu_get
        brief = app.build_brief()
        items = brief["items"]
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0]["reading"] or items[0]["background"] != app.NO_BACKGROUND)
        self.assertEqual(items[1]["background"], app.NO_BACKGROUND)
        self.assertLessEqual(upstream_hits["n"], 2)

    def test_quota_guard_disabled_allows_calls(self):
        guard = self._fresh_guard("t8.json")
        app.QUOTA_LIMITS["zhida"] = 1
        app._chat = lambda url, headers, payload, label: '{"choices":[{"message":{"content":"{}"}}]}'
        app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        app.QUOTA_GUARD_DISABLED = True
        app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        self.assertEqual(guard.used("zhida"), 1)

    def test_concurrent_attempts_are_thread_safe(self):
        guard = self._fresh_guard("t9.json")
        app.QUOTA_LIMITS["zhida"] = 1000

        def worker():
            for _ in range(5):
                guard.attempt("zhida")

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(guard.used("zhida"), 100)


if __name__ == "__main__":
    unittest.main()
