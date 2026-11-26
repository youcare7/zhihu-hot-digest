# -*- coding: utf-8 -*-
"""互动组件富化 pass 回归测试（锐评/站队/吃瓜/楼上导读，2026-11-26）。

运行：python tests/test_enrichment_pass.py（在仓库根目录下）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


def dispute_item():
    return {
        "rank": 1, "title": "测试热点", "type": "dispute", "claim_type": "fact",
        "background": "背景说明", "focus": "争论焦点", "consensus": "双方共识",
        "sides": [{"label": "甲方", "claim": "口径一"}, {"label": "乙方", "claim": "口径二"}],
        "gap": "缺官方附表",
    }


def consensus_item():
    return {
        "rank": 2, "title": "共识热点", "type": "consensus", "background": "背景说明",
        "focus": "焦点", "consensus": "站内结论", "sides": [], "gap": "缺口",
    }


ENRICH_OUT = {
    "punch": "一句足够锋利的锐评",
    "vote": {"blue": "甲派说法", "orange": "乙派说法", "melon": "旁观吃瓜"},
    "quotes": ["吃瓜金句一", "吃瓜金句二"],
    "lou": {
        "angle": "没人提过的切入角度",
        "tips": ["开篇金句", "提纲二", "提纲三"],
        "personas": {"考研党": "视角一", "上班族": "视角二", "业内人士": "视角三"},
    },
}


class TestEnrichmentPass(AppStateTestCase):
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
                                 "model_alt": 500, "web_search": 0})
        self.calls = []

    def _chat_once(self, url, headers, payload, label):
        self.calls.append({"label": label, "prompt": payload["messages"][0]["content"]})
        return json.dumps(ENRICH_OUT, ensure_ascii=False)

    def test_dispute_item_gets_all_components(self):
        app._chat = self._chat_once
        item = dispute_item()
        app._run_enrichment_pass([item])
        self.assertEqual(item["punch"], "一句足够锋利的锐评")
        self.assertEqual(item["vote"]["blue"], "甲派说法")
        self.assertEqual(item["quotes"], ["吃瓜金句一", "吃瓜金句二"])
        self.assertEqual(item["lou"]["angle"], "没人提过的切入角度")
        self.assertEqual(len(item["lou"]["tips"]), 3)
        self.assertEqual(set(item["lou"]["personas"]), set(app.LOU_PERSONAS))
        # 恰好 1 次直答调用（无重试）
        self.assertEqual(len(self.calls), 1)
        # prompt 必须带上双方立场与「不编造/不出 URL」红线
        self.assertIn("【甲方·甲方】", self.calls[0]["prompt"])
        self.assertIn("不得输出任何 URL", self.calls[0]["prompt"])

    def test_consensus_item_no_vote_or_quotes(self):
        app._chat = self._chat_once
        item = consensus_item()
        app._run_enrichment_pass([item])
        self.assertEqual(item["punch"], "一句足够锋利的锐评")
        self.assertNotIn("vote", item)
        self.assertNotIn("quotes", item)
        self.assertIn("lou", item)
        self.assertNotIn('"vote"', self.calls[0]["prompt"])

    def test_bad_json_retried_once_then_success(self):
        responses = ["这不是 JSON", json.dumps(ENRICH_OUT, ensure_ascii=False)]

        def flaky(url, headers, payload, label):
            self.calls.append(1)
            return responses[len(self.calls) - 1]

        app._chat = flaky
        item = dispute_item()
        app._run_enrichment_pass([item])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(item["punch"], "一句足够锋利的锐评")

    def test_persistent_bad_json_keeps_item_healthy(self):
        def broken(url, headers, payload, label):
            self.calls.append(1)
            return "{broken"

        app._chat = broken
        item = dispute_item()
        app._run_enrichment_pass([item])
        self.assertEqual(len(self.calls), 2)  # 重试一次后放弃
        self.assertNotIn("punch", item)
        self.assertEqual(item["background"], "背景说明")  # 条目本身绝不降级
        self.assertEqual(item["type"], "dispute")

    def test_quota_exhaustion_stops_pass_early(self):
        app._QUOTA = app.QuotaGuard(self.tmp_path / "quota0.json")
        app.QUOTA_LIMITS.update({"zhida": 0})
        app._chat = self._chat_once
        first, second = dispute_item(), consensus_item()
        second["rank"] = 3
        app._run_enrichment_pass([first, second])
        self.assertEqual(self.calls, [])  # 配额守卫在调用前拦截
        self.assertNotIn("punch", first)
        self.assertNotIn("punch", second)

    def test_degraded_item_skipped_without_call(self):
        app._chat = self._chat_once
        item = dispute_item()
        item["background"] = app.NO_BACKGROUND
        app._run_enrichment_pass([item])
        self.assertEqual(self.calls, [])
        self.assertNotIn("punch", item)

    def test_existing_components_not_clobbered_when_model_omits(self):
        def no_vote(url, headers, payload, label):
            self.calls.append(1)
            out = {"punch": "新锐评", "lou": ENRICH_OUT["lou"]}  # 模型漏了 vote/quotes
            return json.dumps(out, ensure_ascii=False)

        app._chat = no_vote
        item = dispute_item()
        item["vote"] = {"blue": "旧蓝", "orange": "旧橙", "melon": "旧瓜"}
        item["quotes"] = ["旧金句一", "旧金句二"]
        app._run_enrichment_pass([item])
        self.assertEqual(item["punch"], "新锐评")          # 缺的补上
        self.assertEqual(item["vote"]["blue"], "旧蓝")      # 已有的不被空覆盖
        self.assertEqual(item["quotes"], ["旧金句一", "旧金句二"])

    def test_mock_mode_skips_pass(self):
        app.MOCK_MODE = True
        app._chat = self._chat_once
        item = dispute_item()
        app._run_enrichment_pass([item])
        self.assertEqual(self.calls, [])
        self.assertNotIn("punch", item)

    def test_sanitize_drops_dirty_types(self):
        raw = {
            "punch": ["不是字符串"],
            "vote": "不是对象",
            "quotes": ["只有一条"],
            "lou": {"angle": "角度", "tips": "不是列表",
                    "personas": {"考研党": "只有一个人"}},
        }
        out = app._sanitize_enrichment(raw, dispute_item())
        self.assertEqual(out, {})

        raw2 = {
            "punch": "锐评",
            "vote": {"blue": "蓝", "orange": "橙", "melon": None},
            "quotes": ["一", "二", "三"],
        }
        out2 = app._sanitize_enrichment(raw2, dispute_item())
        self.assertNotIn("vote", out2)          # melon 缺失 → 整组丢弃
        self.assertEqual(out2["quotes"], ["一", "二"])  # 只保留 2 条
        self.assertEqual(out2["punch"], "锐评")

    def test_long_text_clipped_to_limits(self):
        raw = {
            "punch": "长" * 100,
            "lou": {"angle": "角" * 100,
                    "tips": ["提" * 100],
                    "personas": {n: "人" * 100 for n in app.LOU_PERSONAS}},
        }
        out = app._sanitize_enrichment(raw, consensus_item())
        self.assertLessEqual(len(out["punch"]), app.PUNCH_MAX)
        self.assertLessEqual(len(out["lou"]["angle"]), app.LOU_ANGLE_MAX)
        self.assertLessEqual(len(out["lou"]["tips"][0]), app.LOU_TIP_MAX)
        for v in out["lou"]["personas"].values():
            self.assertLessEqual(len(v), app.LOU_PERSONA_MAX)

    def test_enrich_prompt_only_includes_present_material(self):
        item = consensus_item()
        item["consensus"] = ""
        item["gap"] = app.EMPTY_GAP
        prompt = app._build_enrich_prompt(item, False)
        self.assertNotIn("【共识底座】", prompt)
        self.assertNotIn("【待补拼图】", prompt)
        self.assertNotIn("【甲方·", prompt)


if __name__ == "__main__":
    unittest.main()
