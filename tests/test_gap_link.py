# -*- coding: utf-8 -*-
"""缺口的站内相关讨论：代码按关键词打分，不调模型；只认知乎链接；配额不足静默跳过。

运行：python tests/test_gap_link.py（在仓库根目录下）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

GAP = "GB 55038-2025中65分贝撞击声限值的条文说明与配套检测方法（住房和城乡建设部标准定额司发布）。"


def item(**kw):
    base = {"rank": 1, "title": "商品房楼板国标普遍仅10-12cm，为什么不统一再加厚15cm？",
            "type": "dispute", "claim_type": "fact", "background": "背景", "gap": GAP,
            "gap_query": "GB 55038-2025 撞击声 检测方法",
            "reading": [{"url": "https://www.zhihu.com/question/1/answer/11"}],
            "sides": [{"label": "甲", "claim": "一", "evidence": [{"url": "https://www.zhihu.com/question/1/answer/12"}]}]}
    base.update(kw)
    return base


HIT = {"Title": "GB 55038-2025 楼板撞击声怎么测", "Url": "https://zhuanlan.zhihu.com/p/2104568979272495698",
       "ContentText": "第 6.1.2 条规定撞击声不大于 65 分贝，配套检测方法见 GB/T 19889.7", "VoteUpCount": 120,
       "AuthorName": "沐峰"}
TOPIC_ONLY = {"Title": "楼板到底该多厚", "Url": "https://www.zhihu.com/question/9/answer/91",
              "ContentText": "商品房楼板太薄，住着吵", "VoteUpCount": 9999}


class TestGapLink(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.MOCK_MODE = False
        app.QUOTA_GUARD_DISABLED = True
        app._log = lambda m: None
        self._orig_search = app.api_search

    def tearDown(self):
        app.api_search = self._orig_search
        super().tearDown()

    def test_sanitize_gap_query(self):
        self.assertEqual(app.sanitize_gap_query("  GB 55038  https://x.com/a  撞击声 "), "GB 55038 撞击声")
        self.assertEqual(app.sanitize_gap_query("目前无公开可核验材料"), "")
        self.assertEqual(app.sanitize_gap_query("、、"), "")
        self.assertLessEqual(len(app.sanitize_gap_query("长" * 99)), app.GAP_QUERY_MAX)

    def test_terms_rules(self):
        strong, weak = app._gap_terms("都江堰法院(2026)川0181执3173号案件；《人类遗传资源管理条例实施细则》第三十六条；FIFA 与 VAR 复核")
        self.assertIn("(2026)川0181执3173号", strong)
        self.assertIn("《人类遗传资源管理条例实施细则》", strong)
        self.assertIn("第三十六条", strong)
        self.assertNotIn("VAR", strong, "短英文缩写只算 weak")
        self.assertIn("VAR", weak)

    def test_pick_prefers_material_over_topic(self):
        got = app._pick_gap_link(item(), [TOPIC_ONLY, HIT])
        self.assertEqual(got["url"], HIT["Url"], "只讨论话题、不提材料的高赞回答不能入选")
        self.assertIn("GB 55038-2025", got["matched"])
        self.assertEqual(got["author"], "沐峰")

    def test_pick_rejects_offsite_and_already_cited(self):
        offsite = dict(HIT, Url="https://news.ifeng.com/c/x")
        cited = dict(HIT, Url="https://www.zhihu.com/question/1/answer/11")
        lookalike = dict(HIT, Url="https://zhihu.com.evil.cn/p/1")
        self.assertIsNone(app._pick_gap_link(item(), [offsite, cited, lookalike, TOPIC_ONLY]))

    def test_title_words_do_not_count(self):
        it = item(gap="商品房楼板的厚度标准", gap_query="商品房楼板 厚度")
        self.assertIsNone(app._pick_gap_link(it, [TOPIC_ONLY]))

    def test_pass_searches_and_records(self):
        calls = []
        app.api_search = lambda q, count=10: calls.append(q) or [TOPIC_ONLY, HIT]
        items = [item(), item(rank=2, gap=app.EMPTY_GAP), item(rank=3, gap="目前无公开可核验材料"),
                 item(rank=4, gap_link={"url": "https://www.zhihu.com/p/1", "title": "t"})]
        app._run_gap_link_pass(items)
        self.assertEqual(calls, ["GB 55038-2025 撞击声 检测方法"], "无缺口 / 无材料 / 已有链接的条目不搜")
        self.assertEqual(items[0]["gap_link"]["url"], HIT["Url"])
        self.assertIn("gap_link_checked", items[0])

    def test_recheck_cooldown(self):
        calls = []
        app.api_search = lambda q, count=10: calls.append(q) or [TOPIC_ONLY]
        it = item()
        app._run_gap_link_pass([it])
        app._run_gap_link_pass([it])
        self.assertEqual(len(calls), 1, "没找到的条目在冷却期内不重复搜索")
        self.assertNotIn("gap_link", it)

    def test_max_and_quota(self):
        app.GAP_LINK_MAX, old = 2, app.GAP_LINK_MAX
        try:
            calls = []
            app.api_search = lambda q, count=10: calls.append(q) or []
            app._run_gap_link_pass([item(rank=i) for i in range(1, 6)])
            self.assertEqual(len(calls), 2)
        finally:
            app.GAP_LINK_MAX = old

        def boom(q, count=10):
            raise app.UpstreamError("搜索接口当日配额已达上限（配额守卫）")
        app.api_search = boom
        items = [item(rank=i) for i in range(1, 4)]
        app._run_gap_link_pass(items)  # 不抛异常
        self.assertTrue(all("gap_link" not in it for it in items))

    def test_mock_mode_skips(self):
        app.MOCK_MODE = True
        app.api_search = lambda q, count=10: self.fail("mock 模式不应搜索")
        app._run_gap_link_pass([item()])

    def test_year_in_gap_must_match(self):
        it = item(title="创业板指数大涨", gap="创业板2026年三季报业绩兑现数据，见深交所披露。",
                  gap_query="创业板 三季报 业绩")
        old = {"Title": "年报季报预告快报披露规则大全", "Url": "https://zhuanlan.zhihu.com/p/353380021",
               "ContentText": "创业板 三季报 业绩预告 披露规则", "EditTime": "2021-03-01"}
        self.assertIsNone(app._pick_gap_link(it, [old]), "旧年份的同类文章不能入选")
        new = dict(old, Url="https://zhuanlan.zhihu.com/p/2",
                  ContentText="创业板2026三季报业绩汇总，深交所披露数据")  # 补第 3 个弱词命中（披露），凑够新门槛
        self.assertEqual(app._pick_gap_link(it, [old, new])["url"], new["Url"])

    def test_previous_day_brief_used_as_fallback(self):
        import datetime
        app.DATA_DIR = self.tmp_path
        app.FALLBACK_PREV_MAX_DAYS = 2
        today = datetime.date.fromisoformat(app.today_str())
        prev = (today - datetime.timedelta(days=1)).isoformat()
        (self.tmp_path / f"brief_{prev}.json").write_text(json.dumps(
            {"source": "live", "date": prev, "items": [{"rank": 1, "title": "t"}],
             "guard_stats": {"x": 1}}, ensure_ascii=False), encoding="utf-8")
        data = app.load_fallback()
        self.assertEqual(data["date"], prev)
        self.assertEqual(data["source"], "fallback")
        self.assertNotIn("guard_stats", data)
        self.assertIn(prev, data["notice"])
        old = (today - datetime.timedelta(days=app.FALLBACK_PREV_MAX_DAYS + 1)).isoformat()
        (self.tmp_path / f"brief_{prev}.json").unlink()
        (self.tmp_path / f"brief_{old}.json").write_text(json.dumps(
            {"source": "live", "items": [{"rank": 1}]}), encoding="utf-8")
        self.assertIsNone(app._load_previous_brief(), "太旧的不用")
        (self.tmp_path / f"brief_{prev}.json").write_text(json.dumps(
            {"source": "demo", "items": [{"rank": 1}]}), encoding="utf-8")
        self.assertIsNone(app._load_previous_brief(), "模拟数据不用")

    def test_weak_match_needs_three_hits_now(self):
        it = item(gap="需要 SEC 8-K/DEF 14A 披露文件核实该交易细节。", gap_query="SEC 8-K DEF 14A")
        two_hit = {"Title": "特朗普赦免马斯克事件始末", "Url": "https://zhuanlan.zhihu.com/p/1",
                   "ContentText": "SEC 要求披露 8-K 文件，总统特朗普赦免引发争议", "VoteUpCount": 500}
        self.assertIsNone(app._pick_gap_link(it, [two_hit]), "只命中 2 个弱词、沾边不对题的文章不能入选")
        three_hit = dict(two_hit, Url="https://zhuanlan.zhihu.com/p/2",
                         ContentText="该交易按 SEC 8-K 与 DEF 14A 披露要求提交文件")
        self.assertEqual(app._pick_gap_link(it, [two_hit, three_hit])["url"], three_hit["Url"])

    def test_draft_stage_rejected_when_gap_wants_after_effect(self):
        it = item(gap="需要新规正式实施后的利率与备案额度实测数据。", gap_query="新规施行后 利率 备案额度")
        draft = {"Title": "新规征求意见稿都说了什么", "Url": "https://zhuanlan.zhihu.com/p/3",
                 "ContentText": "征求意见稿草案里利率备案额度这样写", "VoteUpCount": 300}
        self.assertIsNone(app._pick_gap_link(it, [draft]), "缺口要施行后结果，候选还在讲草案阶段不能入选")
        after = {"Title": "新规正式施行，利率与备案额度新口径", "Url": "https://zhuanlan.zhihu.com/p/4",
                "ContentText": "新规施行后，利率与备案额度按新口径执行", "VoteUpCount": 500}
        self.assertEqual(app._pick_gap_link(it, [draft, after])["url"], after["Url"])

    def test_pass_failure_never_breaks_build(self):
        src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        i = src.find("            _run_gap_link_pass(items)")
        self.assertGreater(i, 0)
        self.assertIn("try:", src[i - 80:i], "build_brief 必须用 try 包住缺口站内搜索")

    def test_prompt_asks_for_gap_query(self):
        src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
        with open(src_path, encoding="utf-8") as f:
            self.assertIn('"gap_query":"..."', f.read())


if __name__ == "__main__":
    unittest.main()
