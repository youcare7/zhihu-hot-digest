# -*- coding: utf-8 -*-
"""增量刷新：同一问题当日只生成一次，新上榜/失败条目才调用直答。

运行：python -m unittest tests.test_incremental_refresh（在仓库根目录下）
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


def hot(ids, title_fmt="热点{}"):
    return [{"Title": title_fmt.replace("{}", str(i)), "Url": f"https://www.zhihu.com/question/{1000 + i}"}
            for i in ids]


class TestIncrementalRefresh(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False
        app.INCREMENTAL_REFRESH = True
        app.QUOTA_GUARD_DISABLED = True
        app._run_enrichment_pass_orig = app._run_enrichment_pass
        app._run_enrichment_pass = lambda items: None
        app._run_crosscheck_pass_orig = app._run_crosscheck_pass
        app._run_crosscheck_pass = lambda items: None
        app._log = lambda msg: None
        self.gen_calls = []
        self.fail = set()
        app.fetch_search = lambda title, rank, count=10: [
            {"Title": "候选", "Url": "https://www.zhihu.com/question/1/answer/9", "ContentText": "x"}
        ]

        def fake_generate(title, text):
            self.gen_calls.append(title)
            if title in self.fail:
                raise RuntimeError("429 模拟限流")
            return {"type": "consensus", "background": f"背景-{title}", "focus": "焦点",
                    "consensus": "共识", "gap": "目前无公开可核验材料",
                    "reading": [{"index": 1, "reason": "值得看"}]}

        app.generate = fake_generate

    def tearDown(self):
        app._run_enrichment_pass = app._run_enrichment_pass_orig
        del app._run_enrichment_pass_orig
        app._run_crosscheck_pass = app._run_crosscheck_pass_orig
        del app._run_crosscheck_pass_orig
        super().tearDown()

    def _write_cache(self, brief):
        app.atomic_write_json(app.cache_path(), brief)
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = None

    def test_only_new_items_are_generated_and_ranks_follow_hot_list(self):
        app.api_hot_list = lambda: hot(range(1, 11))
        self._write_cache(app.build_brief())
        self.assertEqual(len(self.gen_calls), 10)

        self.gen_calls.clear()
        app.api_hot_list = lambda: hot([3, 1, 2, 4, 5, 6, 7, 11, 12, 13])
        brief = app.build_brief()
        self.assertEqual(sorted(self.gen_calls), ["热点11", "热点12", "热点13"])
        self.assertEqual([it["rank"] for it in brief["items"]], list(range(1, 11)))
        self.assertEqual(brief["items"][0]["title"], "热点3")
        self.assertEqual(brief["items"][0]["background"], "背景-热点3")

    def test_same_question_id_survives_title_edit(self):
        app.api_hot_list = lambda: hot([1])
        self._write_cache(app.build_brief())
        self.gen_calls.clear()
        app.api_hot_list = lambda: hot([1], "热点{}（标题已编辑）")
        brief = app.build_brief()
        self.assertEqual(self.gen_calls, [])
        self.assertEqual(brief["items"][0]["title"], "热点1（标题已编辑）")

    def test_degraded_items_are_regenerated(self):
        app.api_hot_list = lambda: hot([1, 2])
        self.fail = {"热点2"}
        self._write_cache(app.build_brief())
        self.gen_calls.clear()
        self.fail = set()
        app.build_brief()
        self.assertEqual(self.gen_calls, ["热点2"])

    def test_pool_salvages_items_when_batch_is_discarded(self):
        """整批被防劣化门丢弃后，已成功的新条目留在条目池，下次不再重复调用。"""
        app.api_hot_list = lambda: hot(range(1, 11))
        self._write_cache(app.build_brief())
        app.api_hot_list = lambda: hot([1, 2, 3, 4, 5, 6, 7, 11, 12, 13])
        self.fail = {"热点12"}
        discarded = app.build_brief()
        self.assertLess(app._count_ok(discarded), 10)  # 9 < 10，真实链路会丢弃

        self.gen_calls.clear()
        self.fail = set()
        app.build_brief()
        self.assertEqual(self.gen_calls, ["热点12"])

    def test_max_age_forces_regeneration(self):
        app.api_hot_list = lambda: hot([1])
        self._write_cache(app.build_brief())
        for path in (app.item_pool_path(), app.cache_path()):
            data = json.loads(path.read_text(encoding="utf-8"))
            for it in data["items"]:
                it["item_generated_at"] = "2000-01-01 00:00"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = None
        app.ITEM_REUSE_MAX_HOURS = 2
        self.gen_calls.clear()
        app.build_brief()
        self.assertEqual(self.gen_calls, ["热点1"])

    def test_curated_items_with_offsite_links_are_never_reused(self):
        """人工整理条目（站外原问题 / 延伸阅读链接）不得混进管线生成的简报。"""
        app.api_hot_list = lambda: hot([1, 2])
        brief = app.build_brief()
        brief["items"][0]["reading"] = [{"title": "媒体报道", "url": "https://news.ifeng.com/c/x"}]
        self._write_cache(brief)
        # 条目池里同一问题的干净版本也要清掉，确保只考验缓存这一路
        app.item_pool_path().unlink()
        self.gen_calls.clear()
        out = app.build_brief()
        self.assertEqual(self.gen_calls, ["热点1"])
        self.assertTrue(all("ifeng" not in str(r.get("url")) for it in out["items"] for r in it["reading"]))

    def test_switch_off_restores_full_rebuild(self):
        app.api_hot_list = lambda: hot([1, 2])
        self._write_cache(app.build_brief())
        app.INCREMENTAL_REFRESH = False
        self.gen_calls.clear()
        app.build_brief()
        self.assertEqual(len(self.gen_calls), 2)

    def test_mock_mode_never_writes_pool(self):
        app.MOCK_MODE = True
        app.build_brief()
        self.assertFalse(app.item_pool_path().exists())

    def test_corrupt_pool_is_ignored_and_logged(self):
        app.item_pool_path().write_text("{", encoding="utf-8")
        messages = []
        app._log = messages.append

        self.assertEqual(app._pool_load(), [])
        self.assertTrue(any("条目池 JSON 无效" in message for message in messages))

    def test_reused_found_evidence_consumes_budget(self):
        web_calls = []
        app.api_web_search = lambda q, count=5: (
            web_calls.append(q) or [{"Title": "官方公告", "Url": "https://www.gov.cn/x"}])

        def verify_generate(title, text):
            self.gen_calls.append(title)
            return {"type": "verify", "background": "b", "focus": "f",
                    "consensus": "", "gap": "某机构 11 月公告", "reading": []}

        app.generate = verify_generate
        app.api_hot_list = lambda: hot([1, 2, 3])
        self._write_cache(app.build_brief())
        self.assertEqual(len(web_calls), 3)

        web_calls.clear()
        app.api_hot_list = lambda: hot([1, 2, 3, 4])
        brief = app.build_brief()
        self.assertEqual(web_calls, [])  # 复用的 3 条 found 已占满每份简报 3 条的核验额度
        self.assertEqual(brief["items"][0]["web_evidence"]["status"], "found")
        self.assertNotIn("web_evidence", brief["items"][3])


if __name__ == "__main__":
    unittest.main()
