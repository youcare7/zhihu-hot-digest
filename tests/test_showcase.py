# -*- coding: utf-8 -*-
"""功能示例页与兜底快照刷新：人工整理内容只出现在 /showcase/，兜底只用程序生成的数据。

运行：python tests/test_showcase.py（在仓库根目录下）
"""
import json
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import app  # noqa: E402
import daily_archive  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

OFF = "https://news.ifeng.com/c/8yCw4liRNSv"


def item(rank, question_url=None, background="有效背景"):
    q = f"https://www.zhihu.com/question/{1000 + rank}"
    return {
        "rank": rank, "title": f"热点{rank}", "question_url": question_url or q,
        "type": app.TYPE_VERIFY, "claim_type": "", "background": background,
        "focus": "焦点", "consensus": "", "sides": [], "gap": "可核验材料",
        "reading": [{"title": "回答", "url": q + "/answer/1", "reason": "r"}],
    }


def brief(n=10, **over):
    data = {
        "date": "2026-11-26", "source": "live", "generated_at": "2026-11-26 21:00",
        "notice": app.NOTICE_LIVE, "items": [item(i) for i in range(1, n + 1)],
    }
    data.update(over)
    return data


def parse_js(text):
    return json.loads(text[len("window.BRIEF_DATA = "):].rstrip().rstrip(";"))


class TestShowcasePage(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False
        app._log = lambda msg: None
        self.client = app.app.test_client()

    def test_showcase_data_is_labelled(self):
        data = brief()
        data["items"][0]["question_url"] = OFF
        (self.tmp_path / "showcase.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        resp = self.client.get("/showcase/data.js")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        got = parse_js(resp.get_data(as_text=True))
        self.assertIs(got["showcase"], True)
        self.assertEqual(got["source"], "cache")          # 不触发前端恢复轮询
        self.assertEqual(got["provenance"], "curated")
        self.assertEqual(got["offsite_links"], 1)
        self.assertEqual(got["notice"], app.NOTICE_SHOWCASE)

    def test_legacy_snapshot_adapts_latest_cross_platform_ui(self):
        data = brief(n=1)
        data["items"][0]["crosscheck"] = {
            "weibo": "大众情绪",
            "zhihu": "站内切面",
            "fact": "人工整理快照中的既有核验结论",
            "sources": [
                {"name": "凤凰报道", "url": OFF},
                {"name": "知乎讨论", "url": "https://www.zhihu.com/question/1001"},
            ],
        }
        (self.tmp_path / "showcase.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        got = parse_js(self.client.get("/showcase/data.js").get_data(as_text=True))
        item = got["items"][0]
        self.assertEqual(item["crosscheck"]["verdict"], "人工整理快照中的既有核验结论")
        xp = item["cross_platform"]
        self.assertTrue(xp["multi"])
        self.assertEqual(xp["temp"][0], "全网 2 平台在榜")
        self.assertEqual([ev["author"] for ev in xp["evidence"]], ["凤凰报道", "知乎讨论"])
        self.assertTrue(all(ev["excerpt"] == "" for ev in xp["evidence"]))
        self.assertTrue(all(ev["published_at"] == "" for ev in xp["evidence"]))

    def test_missing_showcase_never_falls_back_to_pipeline_data(self):
        resp = self.client.get("/showcase/data.js")
        self.assertEqual(resp.get_data(as_text=True), "window.BRIEF_DATA = null;")

    def test_showcase_page_reuses_index_and_static_routes(self):
        resp = self.client.get("/showcase/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("data.js?v=", resp.get_data(as_text=True))
        resp.close()
        resp = self.client.get("/showcase/assets/ks-idle.webp")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers.get("Content-Type", "").startswith("image/webp"))
        resp.close()
        resp = self.client.get("/showcase/timeline.js")
        self.assertTrue(resp.get_data(as_text=True).startswith("window.TIMELINE_DATA = "))

    def test_showcase_without_slash_redirects(self):
        resp = self.client.get("/showcase")
        self.assertIn(resp.status_code, (301, 302, 308))

    def test_home_data_has_no_showcase_flag(self):
        app.atomic_write_json(app.cache_path(), brief(date=app.today_str()))
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = None
        got = parse_js(self.client.get("/data.js").get_data(as_text=True))
        self.assertNotIn("showcase", got)
        self.assertNotIn("provenance", got)


class TestFallbackRefresh(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.DATA_DIR = self.tmp_path
        self.demo = self.tmp_path / "demo"
        self.demo.mkdir()
        self._saved_demo_dir = app.DEMO_DIR
        app.DEMO_DIR = self.demo
        self.fallback = self.tmp_path / "fallback.json"
        self.fallback.write_text('{"sentinel": true}', encoding="utf-8")

    def tearDown(self):
        app.DEMO_DIR = self._saved_demo_dir
        super().tearDown()

    def _refresh(self, data):
        return daily_archive.refresh_fallback(data, self.tmp_path, self.demo)

    def _unchanged(self):
        return self.fallback.read_text(encoding="utf-8") == '{"sentinel": true}'

    def test_healthy_pipeline_brief_writes_fallback_and_local_preview(self):
        self.assertTrue(self._refresh(brief()))
        fb = json.loads(self.fallback.read_text(encoding="utf-8"))
        self.assertEqual(fb["source"], "fallback")
        self.assertIn("2026-11-26", fb["notice"])
        self.assertEqual(fb["generated_at"], "2026-11-26 21:00")
        self.assertEqual(fb["digest"]["total"], 10)
        self.assertTrue(all(isinstance(it.get("kanshan"), dict) for it in fb["items"]))
        self.assertNotIn("provenance", fb)
        demo = parse_js((self.demo / "data.js").read_text(encoding="utf-8"))
        self.assertEqual(demo["source"], "cache")      # file:// 下不能进入恢复轮询
        self.assertEqual(len(demo["items"]), 10)

    def test_rejects_offsite_links(self):
        data = brief()
        data["items"][3]["reading"][0]["url"] = OFF
        self.assertFalse(self._refresh(data))
        self.assertTrue(self._unchanged())

    def test_rejects_curated_flag_degraded_short_and_mock(self):
        degraded = brief()
        degraded["items"][0]["background"] = app.NO_BACKGROUND
        for data in (brief(provenance="curated"), degraded, brief(n=5),
                     brief(notice="【模拟数据】本地示例")):
            self.assertFalse(self._refresh(data))
        self.assertTrue(self._unchanged())

    def test_main_updates_fallback_only_with_flag(self):
        cached = brief(source="cache")
        (self.tmp_path / "brief_2026-11-26.json").write_text(
            json.dumps(cached, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(daily_archive.main(["--date", "2026-11-26"]), 0)
        self.assertTrue(self._unchanged())
        self.assertEqual(daily_archive.main(["--date", "2026-11-26", "--update-fallback"]), 0)
        self.assertEqual(json.loads(self.fallback.read_text(encoding="utf-8"))["source"], "fallback")


if __name__ == "__main__":
    unittest.main()
