# -*- coding: utf-8 -*-
"""2026-11-27 功能缺陷修复回归测试（以 PR #14 分层配额方案为基线）。

队友 PR #14（ef65e63）用「分层预算 + 覆盖率防劣化」独立修好了 crosscheck 饿死，
本文件只锁定他方案中**仍未覆盖**的缺陷，避免两套配额方案叠加冲突：
  1. 富化 pass 不检查 lou，模型漏字段后组件永久缺失；
  2. 增量刷新用条目池覆盖缓存，已富化字段整体回退；
  3. 核验信源未挡 IP / 伪协议 / 无 host URL；
  4. crosscheck 失败日志一律误报「web_search 配额耗尽」；
  5. healthz 缺逐字段覆盖计数与 web_search 配额池用量。
外加前端静态契约：封面 digest 上屏、印章非死按钮、状态同步、缺字段防护。

运行：python -m pytest tests/test_bugfix_2026_11_27.py -q（仓库根目录下）
"""
import json
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

LOU = {"angle": "没人提过的角度", "tips": ["开篇金句", "提纲二", "提纲三"],
       "personas": {"考研党": "视角一", "上班族": "视角二", "业内人士": "视角三"}}
CROSSCHECK_OUT = {"weibo": "微博情绪", "zhihu": "站内切面", "fact": "事实核验",
                  "sources": [{"name": "站外信源", "index": 1}]}


def healthy_item(rank=1, title="测试热点"):
    return {"rank": rank, "title": title, "type": "consensus",
            "background": "真实背景", "focus": "焦点", "gap": "缺口", "sides": []}


class _PassBase(AppStateTestCase):
    """公共桩：关掉真实网络，配额充足，捕获日志。"""

    def setUp(self):
        super().setUp()
        app.MOCK_MODE = False
        app.RETRY_TIMES = 1
        app.RETRY_WAIT = 0
        app.QUOTA_GUARD_DISABLED = False
        app._GEN_DEADLINE = 0.0
        app.CROSSCHECK_MAX = 10
        # 分层预算给足，避免队友 PR#14 的短路逻辑干扰本文件的单点断言
        app.ZHIDA_MAIN_BUDGET = 0
        app.ZHIDA_ENRICH_BUDGET = 0
        app.ZHIDA_CROSSCHECK_BUDGET = 0
        app._QUOTA = app.QuotaGuard(self.tmp_path / "quota.json")
        app.QUOTA_LIMITS.update({"hot_list": 100, "search": 2000, "zhida": 100,
                                 "model_alt": 500, "web_search": 50})
        self.logs = []
        app._log = self.logs.append
        self.web_calls = []

        def web(query, count=app.WEB_SEARCH_COUNT):
            self.web_calls.append(query)
            return [{"Title": "站外材料", "Url": "https://example.com/a",
                     "ContentText": "摘要", "AuthorName": "信源"}]

        app.api_web_search = web

        def chat(url, headers, payload, label):
            if "富化" in label:
                return json.dumps({"punch": "锐评", "lou": LOU}, ensure_ascii=False)
            return json.dumps(CROSSCHECK_OUT, ensure_ascii=False)

        app._chat = chat


class TestCrosscheckSourceUrlSafety(AppStateTestCase):
    def test_ip_and_pseudo_protocol_rejected_but_real_domain_kept(self):
        candidates = [
            {"Title": "a", "Url": "https://127.0.0.1/", "ContentText": "", "AuthorName": "n"},
            {"Title": "b", "Url": "javascript:alert(1)", "ContentText": "", "AuthorName": "n"},
            {"Title": "c", "Url": "https://weibo.com/abc", "ContentText": "", "AuthorName": "n"},
            {"Title": "d", "Url": "https://zhuanlan.zhihu.com/p/1", "ContentText": "", "AuthorName": "n"},
        ]
        raw = {"weibo": "情绪", "zhihu": "切面", "fact": "结论",
               "sources": [{"name": "a", "index": 1}, {"name": "b", "index": 2},
                           {"name": "c", "index": 3}, {"name": "d", "index": 4}]}
        got = app._sanitize_crosscheck(raw, candidates)
        self.assertIsNotNone(got)
        # IP 字面量 / 伪协议被挡，真实域名保留（知乎站内链接是既有契约）
        self.assertEqual([s["url"] for s in got["sources"]],
                         ["https://weibo.com/abc", "https://zhuanlan.zhihu.com/p/1"])

    def test_bare_helper_covers_edge_cases(self):
        self.assertTrue(app._is_safe_crosscheck_url("https://example.com/a"))
        self.assertTrue(app._is_safe_crosscheck_url("http://zhuanlan.zhihu.com/p/1"))
        for bad in ["", None, "ftp://a.com/x", "//example.com/x", "https://10.0.0.1/",
                    "https://[::1]/", "https://localhost/", "javascript:alert(1)"]:
            self.assertFalse(app._is_safe_crosscheck_url(bad), bad)


class TestEnrichmentLouCompleteness(_PassBase):
    def test_missing_lou_retried_once_then_persists(self):
        """模型漏 lou 时同一次 pass 内重试补齐；下次刷新不再重复消耗配额。"""
        responses = [
            json.dumps({"punch": "只有锐评"}, ensure_ascii=False),
            json.dumps({"punch": "锐评", "lou": LOU}, ensure_ascii=False),
        ]
        calls = []

        def flaky(url, headers, payload, label):
            calls.append(1)
            return responses[min(len(calls) - 1, 1)]

        app._chat = flaky
        item = healthy_item()
        app._run_enrichment_pass([item])
        self.assertTrue(item.get("lou"))
        self.assertEqual(len(calls), 2)
        app._run_enrichment_pass([item])
        self.assertEqual(len(calls), 2)  # 组件齐全后不再重采


class TestReuseFieldMerge(AppStateTestCase):
    def setUp(self):
        super().setUp()
        # AppStateTestCase 未保存 _pool_load / load_today_cache 全部状态，
        # 必须自行恢复，否则会污染后续 test_incremental_refresh 的条目池读取。
        self._saved_pool_load = app._pool_load
        self._saved_load_cache = app.load_today_cache

    def tearDown(self):
        app._pool_load = self._saved_pool_load
        app.load_today_cache = self._saved_load_cache
        super().tearDown()

    def test_pool_does_not_wipe_cached_enrichment(self):
        app.INCREMENTAL_REFRESH = True
        app.MOCK_MODE = False
        cached = {"generated_at": "2026-11-27 10:44", "items": [{
            "title": "同一热点", "type": "consensus", "background": "真实背景",
            "question_url": "", "punch": "锐评", "lou": LOU,
            "crosscheck": dict(CROSSCHECK_OUT),
        }]}
        pool = [{"title": "同一热点", "type": "consensus", "background": "真实背景",
                 "question_url": ""}]
        app.load_today_cache = lambda: cached
        app._pool_load = lambda: pool
        item = app._build_reuse_index()["t:同一热点"]
        self.assertEqual(item.get("punch"), "锐评")
        self.assertTrue(item.get("lou"))
        self.assertTrue(item.get("crosscheck"))


class TestCrosscheckLogAttribution(_PassBase):
    def test_web_search_exhaustion_is_reported_as_such(self):
        """全网搜索配额耗尽时日志写「全网搜索配额耗尽」，且不再误报为其它原因。"""
        app.QUOTA_LIMITS["web_search"] = 0

        def blocked(query, count=app.WEB_SEARCH_COUNT):
            self.web_calls.append(query)
            raise app.UpstreamError("全网搜索当日配额已达上限（配额守卫）")

        app.api_web_search = blocked
        items = [healthy_item(1), healthy_item(2)]
        app._run_crosscheck_pass(items)
        self.assertEqual(len(self.web_calls), 1)
        self.assertTrue(any("全网搜索配额耗尽" in m for m in self.logs))
        self.assertFalse(any("直答配额耗尽" in m for m in self.logs))
        self.assertNotIn("crosscheck", items[0])

    def test_zhida_exhaustion_is_not_blamed_on_web_search(self):
        """直答配额耗尽时不能再写成 web_search 耗尽（交接文档 §5.2 的排查误导）。"""
        app.QUOTA_LIMITS["zhida"] = 0
        app._QUOTA = app.QuotaGuard(self.tmp_path / "quota_zhida0.json")
        self.logs.clear()
        app._run_crosscheck_pass([healthy_item()])
        self.assertTrue(any("直答配额耗尽" in m for m in self.logs))
        self.assertFalse(any("web_search 配额耗尽" in m for m in self.logs))


class TestHealthzFieldCounters(AppStateTestCase):
    def test_verbose_reports_today_field_coverage(self):
        app.DATA_DIR = self.tmp_path
        app.FALLBACK_PATH = self.tmp_path / "fallback.json"
        app.ZHIHU_ACCESS_SECRET = "test-secret"
        os.environ["HEALTHZ_VERBOSE"] = "1"
        self.addCleanup(lambda: os.environ.pop("HEALTHZ_VERBOSE", None))
        app.cache_path().write_text(json.dumps({"items": [
            {"title": "t", "punch": "p", "lou": LOU,
             "crosscheck": dict(CROSSCHECK_OUT),
             "web_evidence": {"status": "found"}},
        ]}, ensure_ascii=False), encoding="utf-8")
        body = json.loads(app.app.test_client().get("/healthz").get_data(as_text=True))
        self.assertEqual(body["punch_count"], 1)
        self.assertEqual(body["lou_count"], 1)
        self.assertEqual(body["crosscheck_count"], 1)
        self.assertEqual(body["web_evidence_found_count"], 1)
        self.assertIn("web_search_daily_limit", body)
        self.assertIn("field_coverage", body)  # 队友 PR#14 的字段保留


class TestFrontendContract(unittest.TestCase):
    """前端修复的静态契约（无需浏览器）：改动若被回退，这里立刻红。"""

    def setUp(self):
        self.html = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")

    def test_digest_is_rendered(self):
        self.assertIn("data.digest", self.html)
        self.assertIn("dg.headline", self.html)
        self.assertIn("dg.insights", self.html)
        self.assertIn("id=\"digest-card\"", self.html)

    def test_stamp_is_not_a_dead_button(self):
        self.assertIn("btn.disabled = !cc", self.html)
        self.assertIn("本条暂无跨平台核验", self.html)
        self.assertIn("function toggleXp", self.html)

    def test_unsafe_and_missing_fields_guarded(self):
        self.assertIn("if (item.kanshan && item.kanshan.gif)", self.html)
        self.assertIn("goLink.href = safeHref(item.question_url)", self.html)
        self.assertNotIn("goLink.href = item.question_url", self.html)
        self.assertIn("String(cc[r[1]] || '')", self.html)
        self.assertIn("String(item.kanshan.line || '')", self.html)
        self.assertIn("u = String(u || '')", self.html)

    def test_local_preview_marked_as_demo(self):
        self.assertIn("indexOf('本地预览')", self.html)

    def test_pr11_xp_rendering_preserved(self):
        """队友 PR#11 的多平台聚合渲染必须保留，不能因整合被冲掉。"""
        for symbol in ["XP_TIER_LABEL", "renderXpTemp", "renderXpVerdict",
                       "renderXpEvidenceBlock"]:
            self.assertIn(symbol, self.html)


if __name__ == "__main__":
    unittest.main()
