# -*- coding: utf-8 -*-
"""全网证据核验单条试点回归测试（任务书 2026-11-20 §6，共 7 项）。

核验契约（对应 reviews/2026-11-20/全网证据核验试点任务书_2026-11-20.md）：
1. 接口返回 1 条有效结果 → web_evidence.status=found 且字段齐全（标题/链接原样）；
2. 接口返回 0 条 → status=no_result，渲染文案严格等于任务书 §4 句式；
3. 接口 403 / 429 / 超时 → status=error，页面结构不变、该条站内内容不降级；
4. 返回结构缺 url 或 title → 丢弃该条，status=error，不渲染；
5. 首轮失败后的收尾补跑不重复发起全网检索（守卫计数证明）；
6. 全网搜索失败时，卡片原有站内 gap 与降级文案原样保留；
7. QUOTA_WEB_SEARCH_DAILY=0（或计数达上限）时自动跳过，status=skipped，无任何上游请求。

运行：python tests/test_web_evidence.py（在仓库根目录下）
"""
import datetime
import json
import os
import pathlib
import sys
import tempfile

os.environ.pop("MOCK_MODE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp())
HTML = (pathlib.Path(__file__).resolve().parent.parent / "demo" / "index.html").read_text(encoding="utf-8")

saved_limits = dict(app.QUOTA_LIMITS)
saved_retry_times = app.RETRY_TIMES
saved_retry_wait = app.RETRY_WAIT
saved_chat = app._chat
saved_fetch_search = app.fetch_search
saved_api_web_search = app.api_web_search
saved_zhihu_get = app.zhihu_get
saved_api_hot_list = app.api_hot_list
app.RETRY_WAIT = 0
app.RETRY_TIMES = 1


def fresh_guard(name):
    g = app.QuotaGuard(TMP / name)
    app._QUOTA = g
    return g


def reset_web_state():
    """每个场景前重置试点内存状态：同一热点只检索一次 + 每份简报 1 条配额。"""
    app._WEB_SEARCH_TRIED.clear()
    app._WEB_EVIDENCE_BUDGET = 1


def base_limits(guard_name):
    fresh_guard(guard_name)
    app.QUOTA_LIMITS.update({"hot_list": 100, "search": 2000, "zhida": 90,
                             "model_alt": 500, "web_search": 20})


def verify_chat(url, headers, payload, label):
    return ('{"type":"verify","background":"甲背景","focus":"焦点",'
            '"gap":"关键缺口","consensus":""}')


app._chat = verify_chat
app.fetch_search = lambda title, rank: [
    {"Title": "候选回答", "ContentText": "候选摘要", "Url": "https://www.zhihu.com/question/1/answer/9"},
]

OUT_ITEM = {"Title": "站外原始标题", "Url": "https://news.example.com/report/1",
            "AuthorName": "记者甲", "EditTime": 1753632000}  # 2025-07-28 00:00 北京时间


# ---------- 场景1：接口返回 1 条有效结果 → found，字段齐全；每份简报只核验 1 条 ----------
base_limits("w1.json")
reset_web_state()
web_calls = {"n": 0}


def counting_web_search(query, count=app.WEB_SEARCH_COUNT):
    web_calls["n"] += 1
    return [dict(OUT_ITEM)]


app.api_web_search = counting_web_search
app.api_hot_list = lambda: [
    {"Title": "热点甲", "Url": "https://www.zhihu.com/question/1"},
    {"Title": "热点乙", "Url": "https://www.zhihu.com/question/2"},
]
brief = app.build_brief()
items = brief["items"]
ev = items[0].get("web_evidence") or {}
assert ev.get("status") == "found", f"第 1 条 verify 应核验成功，实际 {ev}"
assert ev["title"] == "站外原始标题", "标题必须是接口原始标题，不得改写"
assert ev["url"] == "https://news.example.com/report/1", "链接必须是接口原始链接"
assert ev["source"] == "news.example.com", f"source 应为链接主机名，实际 {ev.get('source')}"
assert ev["published_at"] == "2025-07-28", f"published_at 应由 EditTime 换算，实际 {ev.get('published_at')}"
assert "热点甲" in ev["query"] and "关键缺口" in ev["query"], \
    f"检索词应由标题 + gap 关键词拼装，实际 {ev['query']}"
assert ev["checked_at"], "checked_at 必须落盘"
assert "web_evidence" not in items[1], "每份简报只选 1 条 verify 热点做核验（不循环不补选）"
assert web_calls["n"] == 1, f"全网检索应只发起 1 次，实际 {web_calls['n']} 次"
print("场景1 ✅ 单条有效结果 → found，字段齐全原样；全简报仅核验第 1 条 verify")


# ---------- 场景2：接口返回 0 条 → no_result；渲染文案严格等于任务书 §4 句式 ----------
base_limits("w2.json")
reset_web_state()
app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: []
ev = app.build_web_evidence("热点丙", "缺口词")
assert ev["status"] == "no_result", f"0 条结果应为 no_result，实际 {ev['status']}"
assert ev["query"] and ev["checked_at"], "no_result 也必须落盘 query 与 checked_at"
assert "本次全网检索未返回可用的一手材料｜核验截至 " in HTML, \
    "前端必须包含任务书 §4 规定的无结果句式（逐字）"
assert "检索词：" in HTML, "前端角落必须标注检索词，保证可复现"
for banned in ("全网没有", "不存在相关材料", "权威媒体均未披露", "全网核验无公开材料"):
    assert banned not in HTML, f"前端不得出现任务书 §4 禁语：{banned}"
print("场景2 ✅ 0 条 → no_result；无结果句式逐字一致，禁语零出现")


# ---------- 场景3：403 / 429 / 超时 → error；该条站内内容不降级、页面结构不变 ----------
base_limits("w3.json")
reset_web_state()
for label, exc in [
    ("403", app.UpstreamError("知乎接口请求失败 /api/v1/content/global_search：HTTP Error 403")),
    ("429", app.UpstreamError("知乎接口返回错误码 30001（Rate limit）：/api/v1/content/global_search")),
    ("超时", app.UpstreamError("知乎接口请求失败 /api/v1/content/global_search：timed out")),
]:
    app._WEB_SEARCH_TRIED.clear()
    def failing_web_search(query, count=app.WEB_SEARCH_COUNT, _e=exc):
        raise _e
    app.api_web_search = failing_web_search
    ev = app.build_web_evidence(f"热点{label}", "缺口词")
    assert ev["status"] == "error", f"{label} 应记 error，实际 {ev['status']}"

reset_web_state()
def failing_web_search_30001(query, count=app.WEB_SEARCH_COUNT):
    raise app.UpstreamError("知乎接口返回错误码 30001（Rate limit）")
app.api_web_search = failing_web_search_30001
item = app.process_item(1, {"Title": "热点丁", "Url": "https://www.zhihu.com/question/9"})
assert item["web_evidence"]["status"] == "error"
assert item["type"] == "verify" and item["gap"] == "关键缺口", \
    "核验失败绝不影响该条站内字段（核验是增强不是依赖）"
assert item["background"] == "甲背景"
# 页面结构不变：error 状态前端不渲染区块（renderWebEvidence 返回 null）
assert "ev.status === 'found'" in HTML and "ev.status === 'no_result'" in HTML
assert "error / skipped" in HTML, "前端必须显式跳过 error/skipped 状态"
print("场景3 ✅ 403/429/超时 → error；站内字段原样，error 状态前端不渲染")


# ---------- 场景4：返回结构缺 url 或 title → 丢弃该条，status=error，不渲染 ----------
base_limits("w4.json")
reset_web_state()
app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [{"Title": "缺链接的条目"}]
ev = app.build_web_evidence("热点戊", "缺口词")
assert ev["status"] == "error", f"缺 url 应记 error，实际 {ev['status']}"

app._WEB_SEARCH_TRIED.clear()
app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [{"Url": "https://x.example.com/a"}]
ev = app.build_web_evidence("热点戊", "缺口词")
assert ev["status"] == "error", f"缺 title 应记 error，实际 {ev['status']}"

app._WEB_SEARCH_TRIED.clear()
app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [
    {"Title": "知乎站内结果", "Url": "https://www.zhihu.com/question/9"}]
ev = app.build_web_evidence("热点戊", "缺口词")
assert ev["status"] == "error", "知乎域名链接未过独立校验，不得当作站外材料"

# 前一条不可用、后一条可用：丢弃坏条目，取到可用的那一条
app._WEB_SEARCH_TRIED.clear()
app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [
    {"Title": "缺链接"}, {"Title": "可用站外标题", "Url": "https://ok.example.com/x"}]
ev = app.build_web_evidence("热点戊", "缺口词")
assert ev["status"] == "found" and ev["title"] == "可用站外标题", \
    f"应丢弃坏条目并取到可用条目，实际 {ev}"
print("场景4 ✅ 缺 url/title/知乎域名 → 丢弃该条记 error；可用条目正常入选")


# ---------- 场景5：收尾补跑不重复发起全网检索（守卫计数 + 上游调用次数证明） ----------
base_limits("w5.json")
reset_web_state()
upstream_hits = {"n": 0}


def counting_zhihu_get(path, params=None):
    upstream_hits["n"] += 1
    return {"Items": [dict(OUT_ITEM)]}


app.zhihu_get = counting_zhihu_get
app.api_web_search = saved_api_web_search  # 用真实 api_web_search（内部过配额守卫）

app.build_web_evidence("热点己", "缺口词")
app.build_web_evidence("热点己", "缺口词")  # 同标题第二次：必须复用结论
assert upstream_hits["n"] == 1, f"同一热点只应检索 1 次，实际 {upstream_hits['n']} 次"
assert app._QUOTA.used("web_search") == 1, "守卫计数应证明只发起 1 次"

# 模拟首轮 + 收尾补跑：process_item 跑两轮，第二轮复用首轮结论
app._WEB_EVIDENCE_BUDGET = 1
hot = {"Title": "热点庚", "Url": "https://www.zhihu.com/question/10"}
first = app.process_item(1, hot)
second = app.process_item(1, hot)  # 补跑重建该条
assert first["web_evidence"]["status"] == "found"
assert second["web_evidence"]["status"] == "found"
assert second["web_evidence"]["title"] == first["web_evidence"]["title"], \
    "补跑必须复用首轮核验结论，不得重新检索出不同结果"
assert upstream_hits["n"] == 2, f"两轮 process_item 只应共检索 2 次，实际 {upstream_hits['n']} 次"
assert app._QUOTA.used("web_search") == 2, f"守卫计数应等于 2，实际 {app._QUOTA.used('web_search')}"
print("场景5 ✅ 补跑复用首轮结论，守卫计数证明全网检索未重复发起")


# ---------- 场景6：全网搜索失败时，站内 gap 与降级文案原样保留 ----------
base_limits("w6.json")
reset_web_state()
app.api_web_search = failing_web_search_30001
item = app.process_item(1, {"Title": "热点辛", "Url": "https://www.zhihu.com/question/11"})
assert item["gap"] == "关键缺口", "核验失败时站内 gap 必须原样保留"
assert item["focus"] == "焦点" and item["background"] == "甲背景"
# 模型没给出 gap（空串）→ 回落 EMPTY_GAP 哨兵，不满足条件直接跳过、不产生核验字段
app._chat = lambda url, headers, payload, label: \
    '{"type":"verify","background":"甲背景","focus":"焦点","gap":"","consensus":""}'
item = app.process_item(2, {"Title": "热点壬", "Url": "https://www.zhihu.com/question/12"})
assert item["gap"] == app.EMPTY_GAP, "空 gap 应回落降级哨兵"
assert "web_evidence" not in item, "gap 无效（EMPTY_GAP）时不得发起核验"
app._chat = verify_chat
print("场景6 ✅ 核验失败站内字段原样；gap 无效时直接跳过不产生 web_evidence")


# ---------- 场景7：QUOTA_WEB_SEARCH_DAILY=0（或达上限）→ skipped，无任何上游请求 ----------
base_limits("w7.json")
reset_web_state()
app.api_web_search = saved_api_web_search  # 用真实入口：守卫在 api_web_search 内拦截
app.QUOTA_LIMITS["web_search"] = 0  # 完全禁用（部署前的默认态）
upstream_hits["n"] = 0
ev = app.build_web_evidence("热点癸", "缺口词")
assert ev["status"] == "skipped", f"配额 0 应为 skipped，实际 {ev['status']}"
assert upstream_hits["n"] == 0, "被守卫拦截时上游不得收到任何请求"
assert app._QUOTA.used("web_search") == 0, "被拦截的调用不得计入计数"

app.QUOTA_LIMITS["web_search"] = 1
app._WEB_SEARCH_TRIED.clear()
app.build_web_evidence("热点癸", "缺口词")  # 第 1 次放行（上游返回 0 条 → no_result，计 1 次）
ev = app.build_web_evidence("热点子", "缺口词")  # 第 2 次达上限
assert ev["status"] == "skipped", f"达上限应为 skipped，实际 {ev['status']}"
assert upstream_hits["n"] == 1, f"达上限后上游不得再被调用，实际 {upstream_hits['n']} 次"
print("场景7 ✅ 配额 0 / 达上限 → skipped，守卫拦截时零上游请求、零计数")


# ---------- 收尾：还原环境 ----------
app._chat = saved_chat
app.fetch_search = saved_fetch_search
app.api_web_search = saved_api_web_search
app.zhihu_get = saved_zhihu_get
app.api_hot_list = saved_api_hot_list
app.QUOTA_LIMITS.clear()
app.QUOTA_LIMITS.update(saved_limits)
app.RETRY_TIMES = saved_retry_times
app.RETRY_WAIT = saved_retry_wait

print("全部通过")
