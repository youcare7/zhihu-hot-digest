# -*- coding: utf-8 -*-
"""冷启动耗时回归（2026-11-20）。

诊断结论（虚拟时钟，上游 mock 瞬时返回）：旧实现固定 sleep(RETRY_WAIT=30)，
10 条串行各遇一次 429 = 300 秒（5 分钟，与线上 3–5 分钟吻合）；全 429 含补跑
= 3030 秒。耗时全部来自本地等待，不是上游慢、也不是配额守卫开销。

本轮修复：
1. 429 有界指数退避（RETRY_BACKOFF_BASE 起步、RETRY_WAIT 封顶）；
2. 去掉补跑前无条件 sleep(RETRY_WAIT)；
3. 整批软预算 GEN_BUDGET_SECONDS：耗尽后停止新尝试/补跑，保留部分结果，不杀线程。

运行：python tests/test_cold_start_latency.py（在仓库根目录下）
"""
import json
import os
import pathlib
import sys
import tempfile
import time

os.environ.pop("MOCK_MODE", None)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix="cold_latency_"))
_saved = {
    "RETRY_WAIT": app.RETRY_WAIT,
    "RETRY_TIMES": app.RETRY_TIMES,
    "RETRY_BACKOFF_BASE": app.RETRY_BACKOFF_BASE,
    "GEN_BUDGET_SECONDS": app.GEN_BUDGET_SECONDS,
    "_GEN_DEADLINE": app._GEN_DEADLINE,
    "_QUOTA": app._QUOTA,
    "QUOTA_LIMITS": dict(app.QUOTA_LIMITS),
    "_chat": app._chat,
    "zhihu_get": app.zhihu_get,
    "MOCK_MODE": app.MOCK_MODE,
    "QUOTA_GUARD_DISABLED": app.QUOTA_GUARD_DISABLED,
    "_log": app._log,
}

# 虚拟时钟：sleep 不真睡，只推进虚拟时间并累计等待
vt = {"now": 1000.0}
clock = {"s": 0.0, "seq": []}
logs = []


def fake_monotonic():
    return vt["now"]


def fake_sleep(sec):
    clock["s"] += sec
    clock["seq"].append(sec)
    vt["now"] += sec


def quiet_log(msg):
    logs.append(str(msg))


def reset_clock():
    vt["now"] = 1000.0
    clock["s"] = 0.0
    clock["seq"] = []
    logs.clear()


_orig_sleep, _orig_monotonic = time.sleep, time.monotonic
time.sleep = fake_sleep
time.monotonic = fake_monotonic
app._log = quiet_log

app.MOCK_MODE = False
app.QUOTA_GUARD_DISABLED = False
app._QUOTA = app.QuotaGuard(TMP / "q.json")
app.QUOTA_LIMITS.update({"hot_list": 10000, "search": 10000, "zhida": 10000})
app.RETRY_WAIT, app.RETRY_TIMES, app.RETRY_BACKOFF_BASE = 30, 6, 2
app.GEN_BUDGET_SECONDS = 0

HOT = [{"Title": "热点%d" % i, "Url": "https://www.zhihu.com/question/%d" % i}
       for i in range(1, 11)]


def zhihu_get(path, params=None):
    if path.endswith("hot_list"):
        return {"Items": HOT}
    return {"Items": [{"Title": "候选", "Url": "https://www.zhihu.com/question/1/answer/1",
                       "ContentText": "摘要"}]}


app.zhihu_get = zhihu_get
OK = json.dumps({"type": "consensus", "background": "正常背景", "focus": "焦点",
                 "consensus": "结论", "gap": "缺口", "reading": []}, ensure_ascii=False)


def flaky_chat(url, headers, payload, label):
    """每条的第一次尝试 429，第二次成功。"""
    flaky_chat.n += 1
    if flaky_chat.n % 2 == 1:
        raise app.UpstreamError("429 限流")
    return OK


flaky_chat.n = 0


def always_429(url, headers, payload, label):
    raise app.UpstreamError("429 限流")


try:
    # ---------- 场景1：每条 1 次 429，整批等待从 300 秒降到 20 秒 ----------
    reset_clock()
    flaky_chat.n = 0
    app._chat = flaky_chat
    brief = app.build_brief()
    ok = sum(1 for it in brief["items"] if it["background"] != app.NO_BACKGROUND)
    assert ok == 10, f"轻度 429 应 10/10 成功，实际 {ok}"
    assert clock["s"] == 20, f"指数退避应为 10×2=20 秒，实际 {clock['s']}"
    assert clock["seq"] == [2] * 10, f"不应出现补跑等待，实际 {clock['seq']}"
    print("场景1 ✅ 每条 1 次 429：累计等待 300 秒 -> 20 秒，10/10 成功")

    # ---------- 场景2：有界指数退避序列与上限 ----------
    reset_clock()
    app._chat = always_429
    try:
        app._chat_with_retry("https://x", {}, {}, "知乎直答")
        raise AssertionError("持续 429 应抛 UpstreamError")
    except app.UpstreamError:
        pass
    assert clock["seq"] == [2, 4, 8, 16, 30], f"退避序列应为 2/4/8/16/30，实际 {clock['seq']}"
    assert clock["s"] == 60, f"单条最坏等待应为 60 秒（旧实现 150 秒），实际 {clock['s']}"
    print("场景2 ✅ 退避序列 2/4/8/16/30，RETRY_WAIT 封顶；单条最坏 150 -> 60 秒")

    # ---------- 场景3：RETRY_WAIT=0 时零等待（兼容既有测试） ----------
    reset_clock()
    app.RETRY_WAIT = 0
    try:
        app._chat_with_retry("https://x", {}, {}, "知乎直答")
        raise AssertionError("持续 429 应抛 UpstreamError")
    except app.UpstreamError:
        pass
    assert clock["s"] == 0 and clock["seq"] == [], "RETRY_WAIT=0 时不得等待"
    app.RETRY_WAIT = 30
    print("场景3 ✅ RETRY_WAIT=0 仍立即重试、零等待，既有测试语义不变")

    # ---------- 场景4：整批预算耗尽不杀线程、保留部分结果、跳过补跑 ----------
    reset_clock()
    app.GEN_BUDGET_SECONDS = 10
    app._chat = always_429
    brief = app.build_brief()
    assert len(brief["items"]) == 10, "预算耗尽也必须返回完整结构，不能抛异常/杀线程"
    assert clock["s"] <= 10, f"虚拟等待不得超过预算 10 秒，实际 {clock['s']}"
    assert any("预算已耗尽" in m or "跳过补跑" in m for m in logs), \
        "预算耗尽后应跳过补跑并留日志"
    assert app._GEN_DEADLINE == 0.0, "build_brief 结束必须清除全局 deadline"
    print("场景4 ✅ 预算耗尽：整批正常返回 10 条、等待 <=10 秒、跳过补跑、deadline 已清理")

    # ---------- 场景5：预算=0 关闭开关时行为不变 ----------
    reset_clock()
    app.GEN_BUDGET_SECONDS = 0
    flaky_chat.n = 0
    app._chat = flaky_chat
    brief = app.build_brief()
    ok = sum(1 for it in brief["items"] if it["background"] != app.NO_BACKGROUND)
    assert ok == 10 and clock["s"] == 20 and app._GEN_DEADLINE == 0.0
    print("场景5 ✅ GEN_BUDGET_SECONDS=0 关闭预算，退化回场景1 行为")

    # ---------- 场景6：补跑不再无条件 sleep(30) ----------
    reset_clock()
    app.GEN_BUDGET_SECONDS = 0
    app.RETRY_TIMES = 1          # 每条只尝试 1 次，重试路径本身不等待
    app._chat = always_429
    brief = app.build_brief()
    assert any("收尾补跑" in m for m in logs), "全降级应触发补跑"
    assert clock["s"] == 0, f"补跑不得再白等 30 秒，实际 {clock['s']}"
    print("场景6 ✅ 补跑仍会执行，但不再无条件等待 30 秒（旧实现固定 +30 秒）")

    print("全部通过")
finally:
    time.sleep, time.monotonic = _orig_sleep, _orig_monotonic
    app._log = _saved["_log"]
    for name, value in _saved.items():
        if name == "_log":
            continue
        if name == "QUOTA_LIMITS":
            app.QUOTA_LIMITS.clear()
            app.QUOTA_LIMITS.update(value)
        else:
            setattr(app, name, value)
