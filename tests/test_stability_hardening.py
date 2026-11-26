# -*- coding: utf-8 -*-
"""线上稳定性加固回归测试（2026-11-20）。

运行：python tests/test_stability_hardening.py（在仓库根目录下）
"""
import json
import os
import pathlib
import subprocess
import sys
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


class TestStabilityHardening(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.RETRY_WAIT = 0
        app.RETRY_TIMES = 1

    def test_invalid_env_vars_do_not_crash_import(self):
        env = dict(os.environ)
        env.pop("MOCK_MODE", None)
        env.update({
            "QUOTA_WEB_SEARCH_DAILY": "",
            "QUOTA_SEARCH_DAILY": " 20 ",
            "RETRY_TIMES": "abc",
            "CACHE_TTL_HOURS": "三点",
            "REFRESH_COOLDOWN_SECONDS": "-5",
        })
        probe = (
            "import app, json; "
            "print(json.dumps({'web': app.QUOTA_LIMITS['web_search'], "
            "'search': app.QUOTA_LIMITS['search'], 'retry': app.RETRY_TIMES, "
            "'ttl': app.CACHE_TTL_SECONDS, 'cooldown': app.REFRESH_COOLDOWN_SECONDS}))"
        )
        res = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT), env=env,
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        vals = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(vals["web"], 0)
        self.assertEqual(vals["search"], 20)
        self.assertEqual(vals["retry"], 6)
        self.assertEqual(vals["ttl"], 10800)
        self.assertEqual(vals["cooldown"], 0)

        os.environ["STABILITY_TEST_INT"] = "0"
        self.assertEqual(app._env_int("STABILITY_TEST_INT", 6, minimum=1), 1)
        os.environ["STABILITY_TEST_INT"] = " 7 "
        self.assertEqual(app._env_int("STABILITY_TEST_INT", 6, minimum=1), 7)
        os.environ["STABILITY_TEST_FLOAT"] = "1.5"
        self.assertEqual(app._env_float("STABILITY_TEST_FLOAT", 3.0, minimum=0), 1.5)
        os.environ["STABILITY_TEST_FLOAT"] = "nan"
        self.assertEqual(app._env_float("STABILITY_TEST_FLOAT", 3.0, minimum=0), 3.0)
        os.environ["STABILITY_TEST_FLOAT"] = "inf"
        self.assertEqual(app._env_float("STABILITY_TEST_FLOAT", 3.0, minimum=0), 3.0)
        del os.environ["STABILITY_TEST_INT"], os.environ["STABILITY_TEST_FLOAT"]

    def test_quota_save_failure_does_not_break_generation(self):
        g = app.QuotaGuard(self.tmp_path / "q2.json")

        def _save_boom():
            raise OSError("read-only fs / disk full")

        g._save = _save_boom
        app._QUOTA = g
        app.QUOTA_LIMITS["hot_list"] = 100
        app.zhihu_get = lambda path, params=None: {"Items": [{"Title": "x", "Url": "u"}]}
        self.assertTrue(app.api_hot_list())
        self.assertEqual(g.used("hot_list"), 1)

        bad_parent = self.tmp_path / "not_a_dir"
        bad_parent.write_text("x", encoding="utf-8")
        g_io = app.QuotaGuard(bad_parent / "q.json")
        self.assertTrue(g_io.attempt("search"))
        self.assertEqual(g_io.used("search"), 1)

        def fake_zhihu_get(path, params=None):
            if path.endswith("hot_list"):
                return {"Items": [{"Title": "热点甲", "Url": "https://www.zhihu.com/question/1"}]}
            return {"Items": [{
                "Title": "候选回答", "Url": "https://www.zhihu.com/question/1/answer/1",
                "ContentText": "摘要",
            }]}

        def fake_chat(url, headers, payload, label):
            return json.dumps({
                "type": "consensus", "background": "这是一条正常生成的背景", "focus": "焦点",
                "consensus": "结论", "gap": "缺口", "reading": [],
            }, ensure_ascii=False)

        app._QUOTA = g
        app.QUOTA_LIMITS.update({"hot_list": 100, "search": 100, "zhida": 100})
        app.zhihu_get = fake_zhihu_get
        app._chat = fake_chat
        brief = app.build_brief()
        self.assertEqual(len(brief["items"]), 1)
        self.assertNotEqual(brief["items"][0]["background"], app.NO_BACKGROUND)

    def test_background_refresh_exception_keeps_healthy_cache(self):
        healthy = {
            "source": "cache", "items": [{"title": "健康缓存"}], "date": app.today_str(),
            "notice": "ok", "generated_at": "2026-11-20 19:00",
        }
        app.load_today_cache = lambda: dict(healthy)

        def stat_boom():
            raise OSError("stat failed")

        app.cache_age_seconds = stat_boom
        out = app.get_brief()
        self.assertEqual(out["source"], "cache")

    def test_missing_fallback_and_main_error_still_returns_200(self):
        app.FALLBACK_PATH = self.tmp_path / "missing_fallback.json"
        fb = app.load_fallback()
        self.assertEqual(fb["source"], "fallback")
        self.assertIsInstance(fb["items"], list)
        app.load_today_cache = lambda: (_ for _ in ()).throw(RuntimeError("主链路异常"))
        resp = app.app.test_client().get("/data.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"window.BRIEF_DATA", resp.data)

    def test_quota_load_rejects_dirty_values(self):
        p5 = self.tmp_path / "q5.json"
        p5.write_text(json.dumps({"date": app.today_str(), "counts": {
            "search": True,
            "zhida": 5,
            "hot_list": "99",
            "model_alt": -1,
        }}), encoding="utf-8")
        g5 = app.QuotaGuard(p5)
        self.assertEqual(g5.used("search"), 0)
        self.assertIs(type(g5.used("search")), int)
        self.assertEqual(g5.used("zhida"), 5)
        self.assertEqual(g5.used("hot_list"), 0)
        self.assertEqual(g5.used("model_alt"), 0)
        app.QUOTA_LIMITS["search"] = 1
        app._QUOTA = g5
        self.assertTrue(g5.attempt("search"))

    def test_retry_times_zero_raises_upstream_error(self):
        app.RETRY_TIMES = 0
        with self.assertRaises(app.UpstreamError) as ctx:
            app._chat_with_retry("https://x", {}, {}, "知乎直答")
        self.assertIn("RETRY_TIMES", str(ctx.exception))

    def test_zhihu_get_code_string_zero_and_error(self):
        class FakeResp:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        orig_urlopen = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *a, **k: FakeResp('{"Code":"0","Data":{"Items":[1,2]}}')
            self.assertEqual(app.zhihu_get("/x")["Items"], [1, 2])
            urllib.request.urlopen = lambda *a, **k: FakeResp('{"Code":429,"Message":"限流"}')
            with self.assertRaises(app.UpstreamError) as ctx:
                app.zhihu_get("/x")
            self.assertIn("429", str(ctx.exception))
        finally:
            urllib.request.urlopen = orig_urlopen


if __name__ == "__main__":
    unittest.main()
