# -*- coding: utf-8 -*-
"""多平台聚合（cross_platform）回归测试（2026-11-27）。

覆盖：URL→平台/tier 确定性归类、verdict/role 清洗、温差标签规则生成、
单平台诚实降级、证据字段取自检索结果原值、sanitize 失败不产出 cross_platform。
运行：python tests/test_crossplatform_aggregation.py（在仓库根目录下）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

CANDIDATES = [
    {"Title": "微博热议：网友吵翻", "Url": "https://weibo.com/abc",
     "AuthorName": "财经杂志微博", "ContentText": "摘要一", "EditTime": "2026-11-27 10:40"},
    {"Title": "知乎讨论：理性分析", "Url": "https://zhuanlan.zhihu.com/p/1",
     "AuthorName": "注册结构工程师", "ContentText": "摘要二", "EditTime": "2026-11-27 11:05"},
    {"Title": "B站实测：楼板对比", "Url": "https://www.bilibili.com/video/BV1",
     "AuthorName": "工程实测UP主", "ContentText": "摘要三", "EditTime": "2026-11-27 14:22"},
]

THREE_PLATFORM_OUT = {
    "weibo": "微博网友情绪偏向支持",
    "zhihu": "知乎站内偏理性拆数据",
    "fact": "两边材料都未给出官方口径",
    "verdict": "三个平台吵的不是同一件事",
    "sources": [
        {"name": "微博热议", "index": 1, "role": "民间体感"},
        {"name": "知乎讨论", "index": 2, "role": "深度归因"},
    ],
}

SINGLE_PLATFORM_OUT = {
    "weibo": "情绪", "zhihu": "切面", "fact": "结论",
    "sources": [{"name": "知乎讨论", "index": 2}],
}


def live_item():
    return {"rank": 1, "title": "测试热点", "type": "dispute",
            "background": "背景说明", "focus": "焦点", "consensus": "共识",
            "sides": [{"label": "甲", "claim": "一"}, {"label": "乙", "claim": "二"}],
            "gap": "缺口"}


class TestXpClassifyUrl(unittest.TestCase):
    def test_known_platforms(self):
        self.assertEqual(app._xp_classify_url("https://weibo.com/abc"), ("微博", 3))
        self.assertEqual(app._xp_classify_url("https://zhuanlan.zhihu.com/p/1"), ("知乎", 2))
        self.assertEqual(app._xp_classify_url("https://www.bilibili.com/video/BV1"), ("B站", 2))
        self.assertEqual(app._xp_classify_url("https://www.gov.cn/x.htm"), ("权威媒体", 1))
        self.assertEqual(app._xp_classify_url("https://www.thepaper.cn/123"), ("主流媒体", 2))
        self.assertEqual(app._xp_classify_url("https://news.sina.com.cn/a"), ("门户站点", 2))
        self.assertEqual(app._xp_classify_url("https://x.com/a/status/1"), ("海外社媒", 3))

    def test_unknown_and_dirty(self):
        self.assertEqual(app._xp_classify_url("https://some-random-blog.io/post"), ("其他站点", 3))
        self.assertEqual(app._xp_classify_url(""), ("其他站点", 3))
        self.assertEqual(app._xp_classify_url(None), ("其他站点", 3))


class TestCrossPlatformAggregation(AppStateTestCase):
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
                                 "model_alt": 500, "web_search": 50})
        app.CROSSCHECK_MAX = 10

    def _run(self, out, candidates=None):
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [
            dict(c) for c in (candidates or CANDIDATES)]
        app._chat = lambda url, headers, payload, label: json.dumps(out, ensure_ascii=False)
        item = live_item()
        app._run_crosscheck_pass([item])
        return item

    def test_multi_platform_temp_and_evidence(self):
        item = self._run(THREE_PLATFORM_OUT)
        xp = item.get("cross_platform") or {}
        self.assertTrue(xp.get("multi"))
        self.assertEqual(xp["temp"][0], "全网 2 平台在榜")
        self.assertIn("暂缺 T1 权威信源", xp["temp"])
        ev = xp.get("evidence") or []
        self.assertEqual(len(ev), 2)
        self.assertEqual((ev[0]["platform"], ev[0]["tier"]), ("微博", 3))
        self.assertEqual((ev[1]["platform"], ev[1]["tier"]), ("知乎", 2))
        # 作者/摘要/时间取自检索结果原值，URL 走白名单映射
        self.assertEqual(ev[0]["author"], "财经杂志微博")
        self.assertEqual(ev[0]["excerpt"], "摘要一")
        self.assertEqual(ev[0]["published_at"], "2026-11-27 10:40")
        self.assertEqual(ev[0]["url"], "https://weibo.com/abc")
        self.assertEqual(ev[0]["role"], "民间体感")
        # verdict 落入 crosscheck，长度受限
        self.assertEqual(item["crosscheck"]["verdict"], "三个平台吵的不是同一件事")

    def test_t1_source_flagged_in_temp(self):
        out = dict(THREE_PLATFORM_OUT,
                   sources=[{"name": "官媒报道", "index": 4, "role": "时间线锚点"},
                            {"name": "知乎讨论", "index": 2}])
        cands = CANDIDATES + [
            {"Title": "新华社通稿", "Url": "https://www.news.cn/2026/a.htm",
             "AuthorName": "新华社", "ContentText": "官方口径", "EditTime": "2026-11-27 09:00"},
            {"Title": "占位", "Url": "https://unused.example/x"}]
        item = self._run(out, candidates=cands)
        xp = item.get("cross_platform") or {}
        self.assertIn("含 T1 权威信源", xp.get("temp", []))
        self.assertEqual(xp["evidence"][0]["platform"], "权威媒体")
        self.assertEqual(xp["evidence"][0]["tier"], 1)

    def test_single_platform_honest_degradation(self):
        item = self._run(SINGLE_PLATFORM_OUT)
        xp = item.get("cross_platform") or {}
        self.assertFalse(xp.get("multi"))
        self.assertEqual(xp.get("temp"), ["仅 知乎 单平台命中 · 无跨平台温差"])
        self.assertEqual(len(xp.get("evidence") or []), 1)

    def test_sanitize_failure_no_cross_platform(self):
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [dict(c) for c in CANDIDATES]
        out = dict(THREE_PLATFORM_OUT, sources=[{"name": "幽灵", "index": 9}])
        app._chat = lambda url, headers, payload, label: json.dumps(out, ensure_ascii=False)
        item = live_item()
        app._run_crosscheck_pass([item])
        self.assertNotIn("crosscheck", item)
        self.assertNotIn("cross_platform", item)

    def test_verdict_and_role_clipped(self):
        out = dict(THREE_PLATFORM_OUT,
                   verdict="长" * 99,
                   sources=[{"name": "微博热议", "index": 1, "role": "角" * 30}])
        got = app._sanitize_crosscheck(out, CANDIDATES)
        # _clip 无标点时保留 limit-1 字符 + 省略号
        self.assertTrue(got["verdict"].startswith("长" * (app.CROSSCHECK_VERDICT_MAX - 1)))
        self.assertTrue(got["verdict"].endswith("…"))
        self.assertTrue(got["sources"][0]["role"].startswith("角" * (app.XP_ROLE_MAX - 1)))

    def test_verdict_optional_keeps_crosscheck(self):
        out = {k: v for k, v in THREE_PLATFORM_OUT.items() if k != "verdict"}
        got = app._sanitize_crosscheck(out, CANDIDATES)
        self.assertNotIn("verdict", got)
        xp = app._build_cross_platform(got, CANDIDATES)
        self.assertIsNotNone(xp)

    def test_build_cross_platform_ignores_unknown_url(self):
        cc = {"weibo": "a", "zhihu": "b", "fact": "c",
              "sources": [{"name": "幽灵", "url": "https://not-in-candidates.example"}]}
        self.assertIsNone(app._build_cross_platform(cc, CANDIDATES))

    def test_prompt_mentions_verdict_and_role(self):
        prompt = app._build_crosscheck_prompt(live_item(), CANDIDATES)
        self.assertIn("verdict", prompt)
        self.assertIn("role", prompt)
        self.assertIn("不得新增任何事实", prompt)


if __name__ == "__main__":
    unittest.main()
