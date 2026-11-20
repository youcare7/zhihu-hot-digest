# -*- coding: utf-8 -*-
"""生产工程加固：/healthz 只读、状态锁防重入、直答日配额硬熔断、
/data.js 转义与 nosniff 响应头。

整批耗时预算由 GEN_BUDGET_SECONDS 承担，覆盖见 test_cold_start_latency.py。

运行：python tests/test_engineering_hardening.py（在仓库根目录下）
"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import time

os.environ.pop("MOCK_MODE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

app.DATA_DIR = pathlib.Path(tempfile.mkdtemp())
app.MOCK_MODE = False
client = app.app.test_client()

checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    if not cond:
        raise AssertionError(f"{name} 失败：{detail}")


# ---------------------------------------------------------------------------
# 1. /healthz：只读、零副作用、字段齐全
# ---------------------------------------------------------------------------
build_calls = {"n": 0}
_orig_build = app.build_brief


def _counting_build():
    build_calls["n"] += 1
    raise AssertionError("healthz 不应触发整批生成")


app.build_brief = _counting_build
try:
    resp = client.get("/healthz")
    body = json.loads(resp.get_data(as_text=True))
    check("/healthz 返回 200", resp.status_code == 200, f"status={resp.status_code}")
    check("/healthz 不触发生成", build_calls["n"] == 0, f"build_calls={build_calls['n']}")
    for key in (
        "status", "date", "has_today_cache", "cache_age_sec",
        "refreshing", "cooldown_remaining",
        "zhida_calls_today", "zhida_daily_limit", "secret_configured",
    ):
        check(f"/healthz 含字段 {key}", key in body, f"实际={sorted(body)}")
    check("/healthz 密钥状态为布尔值", isinstance(body["secret_configured"], bool), str(body))
    check("/healthz 无当日缓存", body["has_today_cache"] is False, str(body))
    check("/healthz 状态 ok", body["status"] == "ok", str(body))
    check("/healthz 带 nosniff", resp.headers.get("X-Content-Type-Options") == "nosniff",
          str(dict(resp.headers)))
    check("/healthz 不缓存", "no-store" in resp.headers.get("Cache-Control", ""),
          resp.headers.get("Cache-Control", ""))
finally:
    app.build_brief = _orig_build

# 有缓存时 age 应为整数且 refreshing 为 False
cache_file = app.cache_path()
cache_file.write_text(json.dumps({"items": [{"title": "t"}]}, ensure_ascii=False), encoding="utf-8")
body = json.loads(client.get("/healthz").get_data(as_text=True))
check("/healthz 识别当日缓存", body["has_today_cache"] is True, str(body))
check("/healthz cache_age_sec 为整数", isinstance(body["cache_age_sec"], int), str(body))
cache_file.unlink()

# ---------------------------------------------------------------------------
# 2. 状态锁：短临界区防重入，且兼容旧 .locked() 视图
# ---------------------------------------------------------------------------
app._refresh_release()
check("初始状态未在刷新", app._refresh_active() is False)
check("首次抢占成功", app._refresh_claim() is True)
check("并发抢占被拒", app._refresh_claim() is False, "第二个线程不应同时拿到刷新权")
check("locked() 视图同步为 True", app._REFRESH_LOCK.locked() is True)
app._refresh_release()
check("释放后可再次抢占", app._refresh_claim() is True)
app._refresh_release()

# 冷却期内不允许抢占
_old_cooldown = app.REFRESH_COOLDOWN_SECONDS
app.REFRESH_COOLDOWN_SECONDS = 60
try:
    app._set_refresh_cooldown()
    check("冷却期拒绝抢占", app._refresh_claim() is False, "冷却期不应启动刷新")
    check("冷却剩余秒数为正", app._refresh_retry_after() > 0, str(app._refresh_retry_after()))
finally:
    app.REFRESH_COOLDOWN_SECONDS = _old_cooldown
    with app._STATE_LOCK:
        app._REFRESH_NOT_BEFORE = 0.0

# 跨线程：同一时刻只有一个线程能抢到
claimed = []


def try_claim():
    if app._refresh_claim():
        claimed.append(threading.current_thread().name)


threads = [threading.Thread(target=try_claim, name=f"t{i}") for i in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("多线程仅一个抢占成功", len(claimed) == 1, f"claimed={claimed}")
app._refresh_release()

# ---------------------------------------------------------------------------
# 3. 当日直答配额：硬熔断且触顶后跳过后台刷新
# ---------------------------------------------------------------------------
_old_limit = app.DAILY_ZHIDA_LIMIT
_old_used = app._ZHIDA_USED
app.DAILY_ZHIDA_LIMIT = 3
app._ZHIDA_USED = 0
try:
    allowed = [app._zhida_quota_take() for _ in range(4)]
    check("配额前 3 次放行", allowed[:3] == [True, True, True], str(allowed))
    check("第 4 次被硬熔断", allowed[3] is False, str(allowed))
    check("已用次数停在 3", app._zhida_used_today() == 3, str(app._zhida_used_today()))
    check("触顶状态可判定", app._zhida_quota_exhausted() is True)
    started = app._start_background_refresh()
    check("触顶后跳过后台刷新", started is False, "配额触顶仍启动了刷新线程")
    check("触顶后未残留刷新标志", app._refresh_active() is False)
finally:
    app.DAILY_ZHIDA_LIMIT = _old_limit
    app._ZHIDA_USED = _old_used

# 跨日自动归零
app.DAILY_ZHIDA_LIMIT = 2
app._ZHIDA_USED = 2
app._ZHIDA_USED_DAY = "2000-01-01"
try:
    check("跨日计数归零并放行", app._zhida_quota_take() is True)
    check("跨日后已用次数为 1", app._zhida_used_today() == 1)
finally:
    app.DAILY_ZHIDA_LIMIT = _old_limit
    app._ZHIDA_USED = _old_used
    app._ZHIDA_USED_DAY = app.today_str()

# ---------------------------------------------------------------------------
# 4. /data.js：特殊字符转义 + nosniff
# ---------------------------------------------------------------------------
_orig_get = app.get_brief
app.get_brief = lambda: {
    "date": app.today_str(),
    "source": "live",
    "generated_at": app.now_str(),
    "notice": "</script><script>alert(1)</script>",
    "items": [{"title": "含\u2028分隔与\u2029段落", "background": "a<b>c"}],
}
try:
    resp = client.get("/data.js")
    text = resp.get_data(as_text=True)
    check("/data.js 返回 200", resp.status_code == 200, str(resp.status_code))
    check("/data.js 无裸 <script>", "<script>" not in text, "存在可提前闭合的裸标签")
    check("/data.js 左尖括号已转义", "\\u003c" in text, "未发现 \\u003c")
    check("/data.js 右尖括号已转义", "\\u003e" in text, "未发现 \\u003e")
    check("/data.js U+2028 已转义", "\\u2028" in text and "\u2028" not in text)
    check("/data.js U+2029 已转义", "\\u2029" in text and "\u2029" not in text)
    check("/data.js 带 nosniff", resp.headers.get("X-Content-Type-Options") == "nosniff",
          str(dict(resp.headers)))
    check("/data.js 前缀正确", text.startswith("window.BRIEF_DATA = {"), text[:30])
    # 转义后仍是合法 JS：把 \\uXXXX 还原后应能解析回原始 notice
    restored = text[len("window.BRIEF_DATA = "):].rstrip(";")
    check("/data.js 负载仍是合法 JSON",
          json.loads(restored)["notice"] == "</script><script>alert(1)</script>",
          restored[:60])
finally:
    app.get_brief = _orig_get

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
failed = [name for name, ok, _ in checks if not ok]
print(f"共 {len(checks)} 项检查，通过 {len(checks) - len(failed)} 项")
for name, ok, detail in checks:
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else f" · {detail}"))
if failed:
    raise SystemExit(1)
print("工程加固测试：全部通过")
