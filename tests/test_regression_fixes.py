# -*- coding: utf-8 -*-
"""Regression tests for 2026-11-25 audit fixes."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


class TestRegressionFixes(AppStateTestCase):
    def test_load_fallback_synthesizes_digest_when_file_exists_without_digest(self):
        fb_path = self.tmp_path / "fallback.json"
        fb_path.write_text(
            json.dumps({
                "date": "2026-11-25",
                "items": [
                    {"rank": 1, "title": "热点1", "type": "dispute",
                     "sides": [{"label": "A", "claim": "1"}, {"label": "B", "claim": "2"}], "gap": "缺口1"},
                    {"rank": 2, "title": "热点2", "type": "consensus", "gap": "缺口2"},
                ],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        app.FALLBACK_PATH = fb_path
        data = app.load_fallback()
        self.assertEqual(data["source"], "fallback")
        self.assertIsInstance(data.get("digest"), dict)
        self.assertEqual(data["digest"]["total"], 2)
        self.assertEqual(data["digest"]["dispute"], 1)
        self.assertEqual(data["digest"]["consensus"], 1)

        # Invalid digest (missing int total) is also re-synthesized in the normal try branch.
        fb_path.write_text(
            json.dumps({
                "date": "2026-11-25",
                "digest": {"total": "bad"},
                "items": [{"rank": 1, "title": "热点1", "type": "verify", "gap": "缺口"}],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        data2 = app.load_fallback()
        self.assertEqual(data2["digest"]["total"], 1)
        self.assertEqual(data2["digest"]["verify"], 1)

    def test_unified_zhida_quota_no_double_counting(self):
        self.assertFalse(hasattr(app, "_zhida_quota_take"))
        self.assertFalse(hasattr(app, "DAILY_ZHIDA_LIMIT"))

        guard = app.QuotaGuard(self.tmp_path / "quota.json")
        app._QUOTA = guard
        app.QUOTA_GUARD_DISABLED = False
        app.QUOTA_LIMITS["zhida"] = 2
        app.RETRY_TIMES = 1
        app._chat = lambda url, headers, payload, label: '{"ok": true}'

        app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        self.assertEqual(guard.used("zhida"), 1)
        self.assertFalse(guard.exhausted("zhida"))

        app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
        self.assertEqual(guard.used("zhida"), 2)
        self.assertTrue(guard.exhausted("zhida"))
        self.assertFalse(app._start_background_refresh())

    def test_base_item_validates_question_url_with_is_zhihu_url(self):
        valid = app._base_item(1, {
            "Title": "合法热点",
            "Url": "https://www.zhihu.com/question/123456",
        })
        self.assertEqual(valid["question_url"], "https://www.zhihu.com/question/123456")

        for bad_url in (
            "javascript:alert(1)",
            "https://evil.example.com/question/1",
            "http://127.0.0.1/question/1",
            "ftp://www.zhihu.com/question/1",
            "",
        ):
            item = app._base_item(2, {"Title": "非法链接热点", "Url": bad_url})
            self.assertEqual(item["question_url"], "", f"非法 URL 应被清空：{bad_url!r}")

    def test_healthz_degraded_and_verbose_gate(self):
        client = app.app.test_client()
        app.DATA_DIR = self.tmp_path

        # Secret unset and not mock => degraded, minimal payload
        app.ZHIHU_ACCESS_SECRET = ""
        app.MOCK_MODE = False
        os.environ.pop("HEALTHZ_VERBOSE", None)
        body = json.loads(client.get("/healthz").get_data(as_text=True))
        self.assertEqual(body, {"status": "degraded", "secret_configured": False})

        # Secret set, verbose off => ok, minimal payload
        app.ZHIHU_ACCESS_SECRET = "secret-xyz"
        body_ok = json.loads(client.get("/healthz").get_data(as_text=True))
        self.assertEqual(body_ok, {"status": "ok", "secret_configured": True})

        # HEALTHZ_VERBOSE=1 => includes operational counters
        os.environ["HEALTHZ_VERBOSE"] = "1"
        body_v = json.loads(client.get("/healthz").get_data(as_text=True))
        self.assertEqual(body_v["status"], "ok")
        for key in ("date", "has_today_cache", "cache_age_sec", "refreshing",
                    "cooldown_remaining", "zhida_calls_today", "zhida_daily_limit"):
            self.assertIn(key, body_v)

    def test_timeline_js_remote_url_ttl_cache_and_fallback(self):
        import urllib.request

        client = app.app.test_client()
        app.DATA_DIR = self.tmp_path
        (self.tmp_path / "topic_timeline.json").write_text(
            json.dumps({"topics": [{"id": "local-topic"}]}),
            encoding="utf-8",
        )
        app.TIMELINE_REMOTE_URL = "https://remote.example.com/topic_timeline.json"
        app.TIMELINE_CACHE_TTL_SECONDS = 600
        with app._STATE_LOCK:
            app._TIMELINE_CACHE.update({"data": None, "fetched_at": 0.0, "url": ""})

        calls = {"n": 0}
        orig_urlopen = urllib.request.urlopen

        class FakeResp:
            def __init__(self, payload: dict):
                self._raw = json.dumps(payload).encode("utf-8")

            def read(self):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=0):
            calls["n"] += 1
            return FakeResp({"topics": [{"id": "remote-topic"}]})

        urllib.request.urlopen = fake_urlopen
        try:
            body1 = client.get("/timeline.js").get_data(as_text=True)
            body2 = client.get("/timeline.js").get_data(as_text=True)
            self.assertIn("remote-topic", body1)
            self.assertIn("remote-topic", body2)
            self.assertEqual(calls["n"], 1)

            # Cache cleared + remote error => falls back to local topic_timeline.json
            with app._STATE_LOCK:
                app._TIMELINE_CACHE.update({"data": None, "fetched_at": 0.0, "url": ""})

            def failing_urlopen(req, timeout=0):
                raise OSError("network down")

            urllib.request.urlopen = failing_urlopen
            body_fb = client.get("/timeline.js").get_data(as_text=True)
            self.assertIn("local-topic", body_fb)

            # Invalid scheme => falls back to local without calling urlopen
            app.TIMELINE_REMOTE_URL = "file:///etc/passwd"
            body_bad = client.get("/timeline.js").get_data(as_text=True)
            self.assertIn("local-topic", body_bad)
        finally:
            urllib.request.urlopen = orig_urlopen


if __name__ == "__main__":
    unittest.main()
