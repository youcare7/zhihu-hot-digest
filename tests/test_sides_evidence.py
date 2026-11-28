# -*- coding: utf-8 -*-
"""两派观点绑定原文：每派的 evidence 编号映射回候选回答的真实知乎链接，绑定不上即降级。

运行：python tests/test_sides_evidence.py（在仓库根目录下）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

CANDS = [
    {"Title": "胖东来搬离后原址商场几乎倒闭，怎么看？", "Url": "https://www.zhihu.com/question/1/answer/11",
     "ContentText": "客流跟着品牌走"},
    {"Title": "胖东来搬离后原址商场几乎倒闭，怎么看？", "Url": "https://www.zhihu.com/question/1/answer/12",
     "ContentText": "租金上涨是正常市场定价"},
    {"Title": "站外报道", "Url": "https://news.ifeng.com/c/x", "ContentText": "媒体报道"},
    {"Title": "仿冒域名", "Url": "https://zhihu.com.evil.com/q/1", "ContentText": "钓鱼"},
    {"Title": "胖东来搬离后原址商场几乎倒闭，怎么看？", "Url": "https://www.zhihu.com/question/1/answer/15",
     "ContentText": "两边都有道理"},
]


def sides(ev_a, ev_b):
    return [{"label": "品牌信任派", "claim": "客流跟着品牌走", "evidence": ev_a},
            {"label": "市场定价派", "claim": "涨租是正常市场定价", "evidence": ev_b}]


def urls(side):
    return [e["url"] for e in side["evidence"]]


class TestSanitizeSidesEvidence(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.SIDES_REQUIRE_EVIDENCE = True

    def test_maps_index_to_real_zhihu_answer(self):
        got = app.sanitize_sides(sides([1], [2]), app.TYPE_DISPUTE, CANDS)
        self.assertEqual(urls(got[0]), ["https://www.zhihu.com/question/1/answer/11"])
        self.assertEqual(urls(got[1]), ["https://www.zhihu.com/question/1/answer/12"])
        self.assertEqual(got[0]["evidence"][0]["title"], CANDS[0]["Title"])

    def test_invalid_indexes_are_dropped(self):
        # 越界 / 0 / bool / 字符串 / 站外 / 仿冒域名 都不算
        got = app.sanitize_sides(sides([9, 0, True, "1", 3, 4, 1], [2]), app.TYPE_DISPUTE, CANDS)
        self.assertEqual(urls(got[0]), ["https://www.zhihu.com/question/1/answer/11"])

    def test_side_without_valid_evidence_rejects_both_sides(self):
        self.assertEqual(app.sanitize_sides(sides([3, 4], [2]), app.TYPE_DISPUTE, CANDS), [])
        self.assertEqual(app.sanitize_sides(sides([], [2]), app.TYPE_DISPUTE, CANDS), [])
        no_key = [{"label": "甲", "claim": "一"}, {"label": "乙", "claim": "二", "evidence": [2]}]
        self.assertEqual(app.sanitize_sides(no_key, app.TYPE_DISPUTE, CANDS), [])

    def test_shared_answer_cannot_support_both_sides(self):
        # 第 5 条同时被两派引用 → 从两边都去掉；各自仍有独立依据时保留
        got = app.sanitize_sides(sides([1, 5], [2, 5]), app.TYPE_DISPUTE, CANDS)
        self.assertEqual(urls(got[0]), ["https://www.zhihu.com/question/1/answer/11"])
        self.assertEqual(urls(got[1]), ["https://www.zhihu.com/question/1/answer/12"])
        # 两派只引同一条 → 去掉后都为空 → 整体丢弃
        self.assertEqual(app.sanitize_sides(sides([5], [5]), app.TYPE_DISPUTE, CANDS), [])

    def test_dedupe_and_cap_two_per_side(self):
        got = app.sanitize_sides(sides([1, 1, 5, 2], [2]), app.TYPE_DISPUTE, CANDS)
        # 甲方：去重后取前 2 条（1、5），第 4 个编号因每派上限 2 条被截断
        self.assertEqual(urls(got[0]), ["https://www.zhihu.com/question/1/answer/11",
                                        "https://www.zhihu.com/question/1/answer/15"])
        self.assertLessEqual(len(got[0]["evidence"]), app.SIDE_EVIDENCE_MAX)

    def test_single_int_evidence_accepted(self):
        got = app.sanitize_sides(sides(1, 2), app.TYPE_DISPUTE, CANDS)
        self.assertEqual(len(got), 2)

    def test_lenient_mode_keeps_sides_without_evidence(self):
        app.SIDES_REQUIRE_EVIDENCE = False
        got = app.sanitize_sides(sides([3], [2]), app.TYPE_DISPUTE, CANDS)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["evidence"], [])
        self.assertEqual(len(got[1]["evidence"]), 1)

    def test_without_candidates_keeps_legacy_shape(self):
        got = app.sanitize_sides(sides([1], [2]), app.TYPE_DISPUTE)
        self.assertEqual(got, [{"label": "品牌信任派", "claim": "客流跟着品牌走"},
                               {"label": "市场定价派", "claim": "涨租是正常市场定价"}])

    def test_excerpt_taken_verbatim_from_search_text(self):
        long = dict(CANDS[0], ContentText="  客流跟着品牌走，\n不跟着地段走。" + "补充说明" * 30)
        got = app.sanitize_sides(sides([1], [2]), app.TYPE_DISPUTE, [long] + CANDS[1:])
        ex = got[0]["evidence"][0]["excerpt"]
        self.assertTrue(ex.startswith("客流跟着品牌走， 不跟着地段走。"))   # 只归一空白，不改写
        self.assertLessEqual(len(ex), app.SIDE_EXCERPT_MAX)
        self.assertEqual(got[1]["evidence"][0]["excerpt"], "租金上涨是正常市场定价")

    def test_missing_text_means_no_excerpt_key(self):
        bare = [dict(c, ContentText="") for c in CANDS]
        got = app.sanitize_sides(sides([1], [2]), app.TYPE_DISPUTE, bare)
        self.assertNotIn("excerpt", got[0]["evidence"][0])

    def test_non_dispute_still_returns_empty(self):
        self.assertEqual(app.sanitize_sides(sides([1], [2]), app.TYPE_VERIFY, CANDS), [])


class TestProcessItemSidesEvidence(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.SIDES_REQUIRE_EVIDENCE = True
        app.MOCK_MODE = False
        app.DATA_DIR = self.tmp_path
        self.logs = []
        app._log = self.logs.append
        app.fetch_search = lambda title, rank, count=10: [dict(c) for c in CANDS]

    def _gen(self, ev_a, ev_b):
        def g(title, text):
            return {"type": "dispute", "claim_type": "inference", "background": "背景",
                    "focus": "焦点", "consensus": "共识", "gap": "缺口",
                    "sides": sides(ev_a, ev_b), "reading": [{"index": 1, "reason": "r"}]}
        return g

    def test_bound_sides_kept_as_dispute(self):
        app.generate = self._gen([1], [2])
        item = app.process_item(1, {"Title": "胖东来", "Url": "https://www.zhihu.com/question/1"})
        self.assertEqual(item["type"], app.TYPE_DISPUTE)
        self.assertEqual(urls(item["sides"][1]), ["https://www.zhihu.com/question/1/answer/12"])
        self.assertEqual(item["viewpoints"]["support"], "客流跟着品牌走")   # 兼容字段照旧回填

    def test_unbound_side_downgrades_to_verify_with_reason_logged(self):
        app.generate = self._gen([1], [3])
        item = app.process_item(1, {"Title": "胖东来", "Url": "https://www.zhihu.com/question/1"})
        self.assertEqual(item["type"], app.TYPE_VERIFY)
        self.assertEqual(item["sides"], [])
        self.assertEqual(item["claim_type"], "")
        self.assertTrue(any("未能各自绑定候选回答" in m for m in self.logs))

    def test_prompt_asks_for_evidence(self):
        self.assertIn("evidence", app.PROMPT_TEMPLATE)
        self.assertIn("同一条候选不能同时作为两派的依据", app.PROMPT_TEMPLATE)

    def test_mock_dispute_items_carry_evidence(self):
        app.MOCK_MODE = True
        brief = app.build_brief()
        disputes = [it for it in brief["items"] if it["type"] == app.TYPE_DISPUTE]
        self.assertTrue(disputes)
        for it in disputes:
            self.assertTrue(all(s["evidence"] for s in it["sides"]))
            self.assertEqual(app._offsite_link_count(brief), 0)


if __name__ == "__main__":
    unittest.main()
