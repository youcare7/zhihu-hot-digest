# -*- coding: utf-8 -*-
"""合订本 v3 实体匹配与 Route B 回归测试。"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import build_timeline  # noqa: E402


def archive_item(title, state="verify", qid=""):
    return {
        "rank": 1,
        "title": title,
        "question_url": f"https://www.zhihu.com/question/{qid}" if qid else "https://www.ifeng.com/c/real-news",
        "type": state,
        "background": "有效解读",
        "focus": "可核验材料",
        "gap": "公开原始材料",
        "reading": [{"title": "代表回答", "url": "https://www.zhihu.com/question/1/answer/1"}],
        "web_evidence": None,
    }


def build_with(day1, day2, min_days=2):
    with tempfile.TemporaryDirectory(prefix="entity-match-") as temp:
        root = pathlib.Path(temp)
        archive_dir = root / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        aliases = root / "topic_aliases.json"
        aliases.write_text(
            json.dumps({"aliases": {}, "splits": [], "hidden_topics": []}),
            encoding="utf-8",
        )
        for date, items in (("2026-11-20", day1), ("2026-11-21", day2)):
            (archive_dir / f"{date}.json").write_text(
                json.dumps({"date": date, "source": "cache", "items": items}, ensure_ascii=False),
                encoding="utf-8",
            )
        old_archive, old_aliases = build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH
        build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH = archive_dir, aliases
        try:
            return build_timeline.build_timeline(min_days)
        finally:
            build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH = old_archive, old_aliases


class TestTimelineEntityMatching(unittest.TestCase):
    def test_1_same_event_different_phrasings_merge(self):
        hit = build_with(
            [archive_item("DeepSeek 完成第二轮融资 1050 亿元", "verify")],
            [archive_item("如何看待 DeepSeek 第二轮融资 1050 亿落地？", "consensus")],
        )
        self.assertEqual(len(hit["topics"]), 1)
        topic = hit["topics"][0]
        self.assertIn("deepseek", topic.get("entities", []))
        self.assertEqual(topic.get("status_flow"), ["verify", "consensus"])
        self.assertEqual([n["date"] for n in topic["nodes"]], ["2026-11-20", "2026-11-21"])
        self.assertIn(
            topic["title"],
            ("DeepSeek 完成第二轮融资 1050 亿元", "如何看待 DeepSeek 第二轮融资 1050 亿落地？"),
        )
        self.assertTrue(build_timeline.denoise_title("如何看待 DeepSeek 第二轮融资？").startswith("deepseek"))
        self.assertIn("deepseek", build_timeline.hard_entities("DeepSeek 完成融资"))
        self.assertIn("幼儿园托班新政", build_timeline.hard_entities("《幼儿园托班新政》落地"))
        self.assertNotIn("官方", build_timeline.event_keywords(build_timeline.denoise_title("官方最新通报")))

    def test_2_same_template_different_entities_blocked(self):
        blocked = build_with(
            [archive_item("如何看待 Apple 官方最新通报？", "verify", "301")],
            [archive_item("如何看待 Tesla 官方最新通报？", "consensus", "302")],
        )
        self.assertEqual(blocked["topics"], [])
        self.assertFalse(
            build_timeline.cross_day_match(
                {"date": "2026-11-20", "entities": {"apple"}, "keywords": set(), "stem": "apple官方最新通报"},
                {"date": "2026-11-21", "entities": {"tesla"}, "keywords": set(), "stem": "tesla官方最新通报"},
            )
        )
        self.assertFalse(
            build_timeline.cross_day_match(
                {"date": "2026-11-01", "entities": {"deepseek"}, "keywords": set(), "stem": "deepseek融资"},
                {"date": "2026-11-21", "entities": {"deepseek"}, "keywords": set(), "stem": "deepseek融资"},
            )
        )
        self.assertFalse(
            build_timeline.cross_day_match(
                {"date": "2026-11-21", "entities": {"deepseek"}, "keywords": set(), "stem": "deepseek融资"},
                {"date": "2026-11-21", "entities": {"deepseek"}, "keywords": set(), "stem": "deepseek融资"},
            )
        )

    def test_3_disjoint_fallback_contract(self):
        empty = build_with(
            [archive_item("胖东来搬离后原址所开新商场几乎倒闭", "verify")],
            [archive_item("神舟二十四号载人飞船瞄准发射", "consensus")],
        )
        self.assertEqual(empty["topics"], [])
        self.assertEqual(len(empty["archives"]), 2)
        self.assertEqual(empty["archive_files"], 2)
        self.assertEqual([a["date"] for a in empty["archives"]], ["2026-11-21", "2026-11-20"])
        first = empty["archives"][0]
        self.assertTrue({"date", "item_count", "status_counts", "items"} <= set(first))
        self.assertEqual(set(first["status_counts"]), {"dispute", "consensus", "verify"})
        self.assertEqual(first["items"][0]["title"], "神舟二十四号载人飞船瞄准发射")
        self.assertEqual(first["items"][0]["status"], "consensus")
        payload = json.dumps(empty, ensure_ascii=False)
        self.assertNotIn("curated_seed", payload)
        self.assertNotIn("tag-seed", payload)
        self.assertTrue(all(t.get("origin") == "auto_archive" for t in empty["topics"]))

    def test_4_cross_day_match_normalized_score_without_shared_entities(self):
        rec_a = {
            "date": "2026-11-20",
            "entities": set(),
            "keywords": {"芯片", "出口", "管制", "升级"},
            "stem": "芯片出口管制升级",
        }
        rec_b = {
            "date": "2026-11-22",
            "entities": set(),
            "keywords": {"芯片", "出口", "管制", "新规"},
            "stem": "芯片出口管制新规",
        }
        self.assertTrue(build_timeline.cross_day_match(rec_a, rec_b))

    def test_5_union_find_14_day_cluster_span_bound(self):
        uf = build_timeline.UnionFind(3, dates=["2026-11-01", "2026-11-10", "2026-11-20"], max_span_days=14)
        self.assertTrue(uf.union(0, 1))
        self.assertEqual(uf.find(0), uf.find(1))
        self.assertFalse(uf.union(1, 2))
        self.assertNotEqual(uf.find(0), uf.find(2))

    def test_6_link_topics_with_llm_mocked_success_and_fallback(self):
        records = [
            {"date": "2026-11-20", "title": "事件甲第一天", "split": False},
            {"date": "2026-11-21", "title": "事件甲第二天", "split": False},
            {"date": "2026-11-21", "title": "同日无关条目", "split": True},
        ]
        saved_env = {k: os.environ.get(k) for k in ("MODEL_API_KEY", "MODEL_BASE", "MODEL_NAME")}
        orig_urlopen = urllib.request.urlopen
        try:
            os.environ.pop("MODEL_API_KEY", None)
            self.assertEqual(build_timeline.link_topics_with_llm(records), [])

            os.environ["MODEL_API_KEY"] = "test-key"
            os.environ["MODEL_BASE"] = "https://llm.example.com"
            os.environ["MODEL_NAME"] = "test-model"

            class FakeResp:
                def __init__(self, content: str):
                    self._raw = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

                def read(self):
                    return self._raw

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            urllib.request.urlopen = lambda req, timeout=0: FakeResp(
                '```json\n{"groups": [[0, 1, 99, "bad"], [1, 2]]}\n```'
            )
            self.assertEqual(build_timeline.link_topics_with_llm(records), [[0, 1]])

            def broken_urlopen(req, timeout=0):
                raise RuntimeError("llm timeout")

            urllib.request.urlopen = broken_urlopen
            self.assertEqual(build_timeline.link_topics_with_llm(records), [])
        finally:
            urllib.request.urlopen = orig_urlopen
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_7_related_past_dual_track_isolation_and_contract(self):
        app_mod = build_timeline.app
        topic = {
            "title": "为什么神舟二十四号外籍航天员名额给了巴基斯坦？",
            "question_id": "2108916871718073510",
            "question_ids": ["2108916871718073510"],
            "entities": ["神舟二十四号"],
        }
        orig_search = app_mod.api_search
        try:
            app_mod.api_search = lambda q, count=10: [
                {
                    "Title": "当前已上榜同题应被排除",
                    "Url": "https://www.zhihu.com/question/2108916871718073510/answer/1",
                    "EditTime": 1795700000,
                },
                {
                    "Title": "神舟二十四号站外链接应被拦截",
                    "Url": "https://evil.example.com/news/1",
                    "EditTime": 1795600000,
                },
                {
                    "Title": "无关热点不应该混入神舟时间线",
                    "Url": "https://www.zhihu.com/question/9990001",
                    "EditTime": 1795650000,
                },
                {
                    "Title": "神舟二十四号早期发射窗口讨论",
                    "Url": "https://www.zhihu.com/question/1000001/answer/11",
                    "EditTime": 1795400000,
                },
                {
                    "Title": "神舟二十四号早期发射窗口讨论重复问题",
                    "Url": "https://www.zhihu.com/question/1000001/answer/22",
                    "EditTime": 1795300000,
                },
                {
                    "Title": "神舟二十四号乘组选拔机制解读（毫秒时间戳）",
                    "Url": "https://www.zhihu.com/question/1000002/answer/33",
                    "EditTime": 1795500000000,
                },
            ]
            rows = build_timeline.fetch_related_past(topic, limit=3)
            self.assertEqual([r["question_id"] for r in rows], ["1000002", "1000001"])
            self.assertEqual(rows[0]["edit_time"], 1795500000)
            self.assertRegex(rows[0]["edit_date"], r"^\d{4}-\d{2}-\d{2}$")
            for row in rows:
                self.assertNotIn("state", row)
                self.assertNotIn("status", row)
                self.assertNotIn("state_label", row)

            def boom(*args, **kwargs):
                raise app_mod.UpstreamError("quota")

            app_mod.api_search = boom
            self.assertEqual(build_timeline.fetch_related_past(topic), [])
        finally:
            app_mod.api_search = orig_search

        html = app_mod.INDEX_PATH.read_text(encoding="utf-8")
        self.assertIn("related_past", html)
        self.assertIn("按编辑时间排序，不代表事件进程", html)


if __name__ == "__main__":
    unittest.main()
