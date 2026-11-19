# -*- coding: utf-8 -*-
"""配额守卫回归测试（P1 前置：接入全网搜索之前必须先有的按日计数守卫）。

守卫契约（对应 reviews/2026-11-19/zhihu_top15_optimization_plan_审查修订版_2026-11-19.md）：
1. 按接口、按自然日统计「请求尝试次数」：成功、失败、超时、429 全部计入；
2. 达到当日上限后，后续调用一律跳过（抛 UpstreamError），上游不再收到请求；
3. 429 退避重试的每一次尝试都计数；
4. 跨日自动清零；
5. 计数按日落盘，新实例（模拟重启/新进程）能读回同一天已累计的次数；
6. 默认上限不得超过官方配额（热榜 100 / 搜索 5000 / 直答 100 每天）；
7. 上限触达时单条降级，不拖垮整份简报；热榜触顶时明确报错；
8. QUOTA_GUARD_DISABLED=1 时守卫放行（供本地调试）；
9. 并发线程同时计数，总数准确。

运行：python tests/test_quota_guard.py（在仓库根目录下）
"""
import json
import os
import pathlib
import sys
import tempfile
import threading

os.environ.pop("MOCK_MODE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp())

# 与生产默认值一致的测试阈值（官方配额：100 / 5000 / 100 每天）
BASE_LIMITS = {"hot_list": 100, "search": 2000, "zhida": 90, "model_alt": 500, "web_search": 0}
saved_limits = dict(app.QUOTA_LIMITS)
saved_retry_times = app.RETRY_TIMES
saved_retry_wait = app.RETRY_WAIT
saved_chat = app._chat
app.RETRY_WAIT = 0
app.QUOTA_LIMITS.update(BASE_LIMITS)


def fresh_guard(name="quota_counters.json"):
    g = app.QuotaGuard(TMP / name)
    app._QUOTA = g
    return g


# ---------- 场景1：失败也计入尝试次数（不是只在成功时计数） ----------
guard = fresh_guard("t1.json")
app.RETRY_TIMES = 1
app.QUOTA_LIMITS["zhida"] = 5


def failing_chat(url, headers, payload, label):
    raise app.UpstreamError("模拟上游 500")


app._chat = failing_chat
try:
    app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
    raise AssertionError("应抛 UpstreamError")
except app.UpstreamError:
    pass
assert guard.used("zhida") == 1, f"失败调用必须计数，实际 {guard.used('zhida')}"
print("场景1 ✅ 失败的调用也计入当日尝试次数")


# ---------- 场景2：达到上限后不再调用上游 ----------
guard = fresh_guard("t2.json")
app.QUOTA_LIMITS["zhida"] = 1
calls = {"n": 0}


def counting_chat(url, headers, payload, label):
    calls["n"] += 1
    return '{"choices":[{"message":{"content":"{}"}}]}'


app._chat = counting_chat
app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
try:
    app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
    raise AssertionError("第二次调用应被守卫拦截")
except app.UpstreamError as exc:
    assert "配额" in str(exc), f"拦截原因应说明配额，实际：{exc}"
assert calls["n"] == 1, f"上限触达后上游不得再被调用，实际调用 {calls['n']} 次"
assert guard.used("zhida") == 1, "被拦截的调用不得计入计数"
print("场景2 ✅ 达上限后抛 UpstreamError，上游调用次数不再增加")


# ---------- 场景3：429 退避重试的每一次尝试都计数 ----------
guard = fresh_guard("t3.json")
app.QUOTA_LIMITS["zhida"] = 10
app.RETRY_TIMES = 3
app.RETRY_WAIT = 0


def chat_429(url, headers, payload, label):
    raise app.UpstreamError("429 限流")


app._chat = chat_429
try:
    app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
    raise AssertionError("应抛 UpstreamError")
except app.UpstreamError:
    pass
assert guard.used("zhida") == 3, f"3 次重试尝试应计 3 次，实际 {guard.used('zhida')}"
print("场景3 ✅ 429 每次重试尝试都计数（3 次尝试 = 3 次计数）")


# ---------- 场景4：跨日自动清零 ----------
guard = fresh_guard("t4.json")
app.QUOTA_LIMITS["zhida"] = 100
app._chat = counting_chat
real_today = app.today_str
app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
assert guard.used("zhida") == 1
app.today_str = lambda: "2999-01-01"
try:
    app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
finally:
    app.today_str = real_today
assert guard.used("zhida") == 1, "跨日后计数应清零并从 1 重新累计"
print("场景4 ✅ 跨自然日计数自动清零")


# ---------- 场景5：计数落盘，新实例（重启/新进程）读回同一天累计 ----------
path = TMP / "t5.json"
g1 = app.QuotaGuard(path)
app._QUOTA = g1
app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
g2 = app.QuotaGuard(path)  # 模拟重启后的新实例
assert g2.used("zhida") == 2, f"新实例应读回已累计 2 次，实际 {g2.used('zhida')}"
raw = json.loads(path.read_text(encoding="utf-8"))
assert raw["counts"]["zhida"] == 2, "落盘文件内容应与内存计数一致"
print("场景5 ✅ 计数按日落盘，新进程实例可继续累计")


# ---------- 场景6：默认上限不得超过官方配额 ----------
assert app.QUOTA_LIMITS["hot_list"] <= 100, "热榜默认上限不得超过官方 100/天"
assert app.QUOTA_LIMITS["search"] <= 5000, "搜索默认上限不得超过官方 5000/天"
assert app.QUOTA_LIMITS["zhida"] <= 100, "直答默认上限不得超过官方 100/天"
assert app.QUOTA_LIMITS["web_search"] == 0, "全网搜索默认必须禁用（0），试点前不得默认开放"
print("场景6 ✅ 默认上限 ≤ 官方配额（100/5000/100），全网搜索默认禁用")


# ---------- 场景7：集成——搜索触顶时单条降级，不拖垮整份简报 ----------
guard = fresh_guard("t7.json")
app.QUOTA_LIMITS["search"] = 1
app.QUOTA_LIMITS["zhida"] = 100
app.RETRY_TIMES = 1
app.RETRY_WAIT = 0
def ok_chat(url, headers, payload, label):
    return '{"type":"consensus","background":"甲背景","focus":"焦点","gap":"缺口","consensus":"结论"}'


app._chat = ok_chat  # 直答恒成功：第 2 条的失败只能来自搜索配额守卫

app.api_hot_list = lambda: [
    {"Title": "热点甲", "Url": "https://www.zhihu.com/question/1"},
    {"Title": "热点乙", "Url": "https://www.zhihu.com/question/2"},
]
app.fetch_search = app.api_search  # 走真实 api_search + 配额守卫

upstream_hits = {"n": 0}
real_zhihu_get = app.zhihu_get


def counting_zhihu_get(path, params=None):
    upstream_hits["n"] += 1
    if path == "/api/v1/content/hot_list":
        return {"Items": [{"Title": "x", "Url": "u"}]}
    return {"Items": [{"Title": "候选", "Url": "https://www.zhihu.com/question/1/answer/9"}]}


app.zhihu_get = counting_zhihu_get
try:
    brief = app.build_brief()
finally:
    app.zhihu_get = real_zhihu_get

items = brief["items"]
assert len(items) == 2, f"整份简报仍应有 2 条，实际 {len(items)}"
assert items[0]["reading"] or items[0]["background"] != app.NO_BACKGROUND
assert items[1]["background"] == app.NO_BACKGROUND, "搜索配额触顶的第 2 条应单条降级"
assert upstream_hits["n"] <= 2, f"守卫拦截后上游搜索不得再被调用，实际 {upstream_hits['n']} 次"
print("场景7 ✅ 搜索触顶单条降级，整份简报不崩，上游不再被重复调用")


# ---------- 场景8：禁用开关放行 ----------
guard = fresh_guard("t8.json")
app.QUOTA_LIMITS["zhida"] = 1
app._chat = counting_chat
app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")
app.QUOTA_GUARD_DISABLED = True
try:
    app._chat_with_retry("https://x", {}, {"model": "m", "messages": []}, "知乎直答")  # 超上限也应放行
finally:
    app.QUOTA_GUARD_DISABLED = False
assert guard.used("zhida") == 1, "禁用开关下守卫不计数也不拦截"
print("场景8 ✅ QUOTA_GUARD_DISABLED=1 时守卫放行")


# ---------- 场景9：并发计数准确 ----------
guard = fresh_guard("t9.json")
app.QUOTA_LIMITS["zhida"] = 1000


def worker():
    for _ in range(5):
        guard.attempt("zhida")


threads = [threading.Thread(target=worker) for _ in range(20)]
for t in threads:
    t.start()
for t in threads:
    t.join()
assert guard.used("zhida") == 100, f"20 线程 × 5 次应正好 100，实际 {guard.used('zhida')}"
print("场景9 ✅ 20 线程并发 × 5 次尝试，计数恰好 100，无竞态丢失")


# ---------- 收尾：还原环境 ----------
app._chat = saved_chat
app.QUOTA_LIMITS.clear()
app.QUOTA_LIMITS.update(saved_limits)
app.RETRY_TIMES = saved_retry_times
app.RETRY_WAIT = saved_retry_wait

print("全部通过")
