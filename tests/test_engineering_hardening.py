# -*- coding: utf-8 -*-
"""生产工程加固：/healthz 只读、状态锁防重入、直答日配额硬熔断、
/data.js 转义与 nosniff 响应头。

运行：python tests/test_engineering_hardening.py（在仓库根目录下）
"""
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


class TestEngineeringHardening(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False
        self.client = app.app.test_client()

    def test_healthz_readonly_degraded_and_verbose(self):
        build_calls = {"n": 0}

        def _counting_build():
            build_calls["n"] += 1
            raise AssertionError("healthz 不应触发整批生成")

        app.build_brief = _counting_build

        # 1. 无密钥且非 MOCK_MODE：status="degraded"，隐藏详细计数器
        app.ZHIHU_ACCESS_SECRET = ""
        os.environ.pop("HEALTHZ_VERBOSE", None)
        resp = self.client.get("/healthz")
        body = json.loads(resp.get_data(as_text=True))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(build_calls["n"], 0)
        self.assertEqual(body["status"], "degraded")
        self.assertIs(body["secret_configured"], False)
        for hidden_key in (
            "date", "has_today_cache", "cache_age_sec",
            "refreshing", "cooldown_remaining",
            "zhida_calls_today", "zhida_daily_limit",
        ):
            self.assertNotIn(hidden_key, body)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("no-store", resp.headers.get("Cache-Control", ""))

        # 2. 有密钥 + HEALTHZ_VERBOSE=1：status="ok"，返回全部详细计数器
        app.ZHIHU_ACCESS_SECRET = "test-secret"
        os.environ["HEALTHZ_VERBOSE"] = "1"
        resp = self.client.get("/healthz")
        body = json.loads(resp.get_data(as_text=True))
        self.assertEqual(body["status"], "ok")
        self.assertIs(body["secret_configured"], True)
        for key in (
            "date", "has_today_cache", "cache_age_sec",
            "refreshing", "cooldown_remaining",
            "zhida_calls_today", "zhida_daily_limit",
        ):
            self.assertIn(key, body)
        self.assertIs(body["has_today_cache"], False)

        # 3. 有缓存时 age 为整数
        cache_file = app.cache_path()
        cache_file.write_text(json.dumps({"items": [{"title": "t"}]}, ensure_ascii=False), encoding="utf-8")
        body = json.loads(self.client.get("/healthz").get_data(as_text=True))
        self.assertIs(body["has_today_cache"], True)
        self.assertIsInstance(body["cache_age_sec"], int)

    def test_state_lock_and_cooldown(self):
        app._refresh_release()
        self.assertFalse(app._refresh_active())
        self.assertTrue(app._refresh_claim())
        self.assertFalse(app._refresh_claim())
        self.assertTrue(app._REFRESH_LOCK.locked())
        app._refresh_release()
        self.assertTrue(app._refresh_claim())
        app._refresh_release()

        old_cooldown = app.REFRESH_COOLDOWN_SECONDS
        app.REFRESH_COOLDOWN_SECONDS = 60
        try:
            app._set_refresh_cooldown()
            self.assertFalse(app._refresh_claim())
            self.assertGreater(app._refresh_retry_after(), 0)
        finally:
            app.REFRESH_COOLDOWN_SECONDS = old_cooldown
            with app._STATE_LOCK:
                app._REFRESH_NOT_BEFORE = 0.0

        claimed = []

        def try_claim():
            if app._refresh_claim():
                claimed.append(threading.current_thread().name)

        threads = [threading.Thread(target=try_claim, name=f"t{i}") for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(claimed), 1)
        app._refresh_release()

    def test_unified_zhida_quota_guard(self):
        guard = app.QuotaGuard(self.tmp_path / "quota.json")
        app._QUOTA = guard
        app.QUOTA_GUARD_DISABLED = False
        app.QUOTA_LIMITS["zhida"] = 3

        allowed = [app._QUOTA.attempt("zhida") for _ in range(4)]
        self.assertEqual(allowed, [True, True, True, False])
        self.assertEqual(app._QUOTA.used("zhida"), 3)
        self.assertTrue(app._QUOTA.exhausted("zhida"))
        self.assertFalse(app._start_background_refresh())
        self.assertFalse(app._refresh_active())

        # 跨日自动归零（单源 QuotaGuard）
        app.QUOTA_LIMITS["zhida"] = 2
        real_today = app.today_str
        app.today_str = lambda: "2999-01-01"
        try:
            self.assertEqual(app._QUOTA.used("zhida"), 0)
            self.assertFalse(app._QUOTA.exhausted("zhida"))
            self.assertTrue(app._QUOTA.attempt("zhida"))
            self.assertEqual(app._QUOTA.used("zhida"), 1)
        finally:
            app.today_str = real_today

    def test_data_js_escaping_and_nosniff(self):
        app.get_brief = lambda: {
            "date": app.today_str(),
            "source": "live",
            "generated_at": app.now_str(),
            "notice": "</script><script>alert(1)</script>",
            "items": [{"title": "含\u2028分隔与\u2029段落", "background": "a<b>c"}],
        }
        resp = self.client.get("/data.js")
        text = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("<script>", text)
        self.assertIn("\\u003c", text)
        self.assertIn("\\u003e", text)
        self.assertIn("\\u2028", text)
        self.assertNotIn("\u2028", text)
        self.assertIn("\\u2029", text)
        self.assertNotIn("\u2029", text)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertTrue(text.startswith("window.BRIEF_DATA = {"))
        restored = text[len("window.BRIEF_DATA = "):].rstrip(";")
        self.assertEqual(json.loads(restored)["notice"], "</script><script>alert(1)</script>")


if __name__ == "__main__":
    unittest.main()
