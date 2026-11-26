# -*- coding: utf-8 -*-
"""038 合并缺口回归测试（2026-11-21）。

运行：python tests/test_merge_hardening_038.py（仓库根目录）
"""
import io
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


class TestMergeHardening038(AppStateTestCase):
    def test_g1_generate_brief_alt_model(self):
        app.MODEL_API_KEY, app.MODEL_BASE, app.MODEL_NAME = "k", "https://model.example.com", "m"
        app._QUOTA = app.QuotaGuard(self.tmp_path / "g1.json")
        app.QUOTA_LIMITS.update({"zhida": 90, "model_alt": 500})
        calls = {"n": 0}

        def chat(url, headers, payload, label):
            calls["n"] += 1
            return "不是 JSON"

        app._chat = chat
        app._chat_with_retry = lambda url, headers, payload, label: "不是 JSON"
        with self.assertRaises(app.UpstreamError) as ctx:
            app.generate_brief("标题", "候选素材")
        self.assertTrue("不是合法 JSON" in str(ctx.exception) or "不是 JSON 对象" in str(ctx.exception))
        self.assertEqual(calls["n"], 0)
        self.assertEqual(app._QUOTA.used("model_alt"), 0)

    def test_g2_web_url_ip_guard(self):
        for bad in ("https://127.0.0.1/x", "https://169.254.169.254/latest/meta-data",
                    "http://10.0.0.1/a", "http://192.168.1.1/a", "https://[::1]/a"):
            self.assertFalse(app._is_valid_web_url(bad), f"IP 字面量不得放行：{bad}")
        self.assertTrue(app._is_valid_web_url("https://news.example.com/a"))
        self.assertFalse(app._is_valid_web_url("https://www.zhihu.com/question/1"))

    def test_g3_items_type_guard(self):
        app._QUOTA = app.QuotaGuard(self.tmp_path / "g3.json")
        app.QUOTA_LIMITS.update({"hot_list": 100, "search": 2000, "zhida": 90,
                                 "model_alt": 500, "web_search": 20})
        for bad in ({"a": 1}, "oops", 123, None):
            app.zhihu_get = lambda path, params=None, _b=bad: {"Items": _b}
            self.assertEqual(app.api_hot_list(), [])
            self.assertEqual(app.api_search("q"), [])
            self.assertEqual(app.api_web_search("q"), [])
        app.zhihu_get = lambda path, params=None: {"Items": [None, "x", {"Title": "ok"}]}
        self.assertEqual(app.api_hot_list(), [{"Title": "ok"}])
        self.assertEqual(app.api_search("q"), [{"Title": "ok"}])
        app.zhihu_get = lambda path, params=None: {"Items": [{"Title": "a"}, {"Title": "b"}]}
        self.assertEqual(len(app.api_hot_list()), 2)

    def test_g4_clip_dirty_types(self):
        for bad in ({"a": 1}, ["x", "y"], 123, 1.5, True):
            self.assertEqual(app._clip(bad, 150), "")
        self.assertEqual(app._clip("正常文本", 150), "正常文本")
        self.assertTrue(app._clip("超长文本" * 40, 20))

        app.generate = lambda title, ct: {
            "type": "consensus", "background": {"a": 1}, "focus": ["x"],
            "gap": {"k": "v"}, "consensus": 123, "sides": None, "reading": [],
        }
        app.fetch_search = lambda title, rank: [
            {"Title": "候选", "ContentText": "摘要", "Url": "https://www.zhihu.com/question/1/answer/2"}
        ]
        item = app.process_item(1, {"Title": "脏类型热点", "Url": "https://www.zhihu.com/question/1"})
        self.assertEqual(item["background"], app.NO_BACKGROUND)
        self.assertEqual(item["focus"], app.EMPTY_FOCUS)
        self.assertEqual(item["gap"], app.EMPTY_GAP)
        for field in ("background", "focus", "gap"):
            self.assertNotIn("{", str(item[field]))
            self.assertNotIn("[", str(item[field]))

    def test_g5_edit_time(self):
        for bad in (1753632000000, 2 ** 63, 10 ** 30, -1, "1753632000", True, 1753632000):
            app._WEB_SEARCH_TRIED.clear()
            title = f"EditTime-{bad!r}"
            app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT, _b=bad: [
                {"Title": "站外原始标题", "Url": "https://news.example.com/a", "EditTime": _b}
            ]
            ev = app.build_web_evidence(title, "缺口词")
            self.assertIn(ev.get("status"), ("found", "no_result", "error", "skipped"))
            self.assertTrue(ev.get("query") and ev.get("checked_at"))
            self.assertIn(title, app._WEB_SEARCH_TRIED)

        app._WEB_SEARCH_TRIED.clear()
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [
            {"Title": "站外", "Url": "https://news.example.com/a", "EditTime": 1753632000}
        ]
        ev = app.build_web_evidence("合法时间戳", "缺口")
        self.assertEqual(ev.get("status"), "found")
        self.assertEqual(ev.get("published_at"), "2025-07-28")

    def test_g6_build_digest_dirty_item(self):
        d = app.build_digest([
            None, "oops", 42,
            {"type": "consensus", "gap": "正常缺口"},
            {"type": "dispute", "sides": [{"label": "a", "claim": "b"}]},
            {"type": "unknown_type", "gap": "未知类型"},
            {"type": "dispute", "sides": 5},
            {"type": "dispute", "sides": "ab"},
        ])
        self.assertEqual(d["total"], 8)
        self.assertEqual(d["dispute"] + d["consensus"] + d["verify"], d["total"])

    def test_g7_fallback_minimal_has_digest(self):
        empty = self.tmp_path / "g7_empty_data"
        empty.mkdir(exist_ok=True)
        app.FALLBACK_PATH = empty / "does_not_exist.json"
        app.MOCK_MODE = False
        app._start_background_refresh = lambda: False
        data = app.load_fallback()
        self.assertIsInstance(data.get("digest"), dict)
        self.assertIn("total", data["digest"])
        self.assertEqual(data["source"], "fallback")
        self.assertIsInstance(data.get("items"), list)
        with app.app.test_client() as client:
            resp = client.get("/data.js")
            self.assertEqual(resp.status_code, 200)
            body = resp.get_data(as_text=True)
            obj = json.loads(body[len("window.BRIEF_DATA = "):].rstrip().rstrip(";"))
            self.assertIsInstance(obj.get("digest"), dict)

    def test_g8_datajs_route_guard(self):
        def boom():
            raise RuntimeError("模拟 get_brief 未预期异常")

        app.get_brief = boom
        with app.app.test_client() as client:
            resp = client.get("/data.js")
            self.assertEqual(resp.status_code, 200)
            body = resp.get_data(as_text=True)
            obj = json.loads(body[len("window.BRIEF_DATA = "):].rstrip().rstrip(";"))
            self.assertEqual(obj.get("source"), "fallback")

    def test_k1_code_type(self):
        def call(code):
            body = json.dumps({"Code": code, "Data": {"Items": []}}).encode("utf-8")
            with mock.patch.object(app.urllib.request, "urlopen", return_value=io.BytesIO(body)):
                return app.zhihu_get("/x")

        self.assertEqual(call(0), {"Items": []})
        self.assertEqual(call("0"), {"Items": []})
        for bad in (False, True, 1, None, "1", "error"):
            with self.assertRaises(app.UpstreamError):
                call(bad)

    def test_k2_quota_load_bool(self):
        p = self.tmp_path / "k2.json"
        p.write_text(json.dumps({"date": app.today_str(),
                                 "counts": {"zhida": True, "hot_list": 3}}), encoding="utf-8")
        g = app.QuotaGuard(p)
        self.assertEqual(g.used("zhida"), 0)
        self.assertEqual(g.used("hot_list"), 3)

    def test_k3_quota_save_mkdir(self):
        p = self.tmp_path / "k3_nested" / "deep" / "q.json"
        g = app.QuotaGuard(p)
        app.QUOTA_LIMITS["zhida"] = 90
        self.assertTrue(g.attempt("zhida"))
        self.assertTrue(p.exists())

    def test_k4_js_safe_and_nosniff(self):
        src = pathlib.Path(app.__file__).read_text(encoding="utf-8")
        self.assertIn("_js_safe_json", src)
        self.assertIn("X-Content-Type-Options", src)
        self.assertIn("def _js_safe_json", src)
        with app.app.test_client() as client:
            app.get_brief = lambda: {
                "date": "2026-11-21", "source": "cache", "generated_at": "x",
                "notice": "</script><script>alert(1)</script>",
                "digest": {}, "items": [],
            }
            resp = client.get("/data.js")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
            body = resp.get_data(as_text=True)
            self.assertNotIn("</script><script>", body)


if __name__ == "__main__":
    unittest.main()
