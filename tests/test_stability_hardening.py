# -*- coding: utf-8 -*-
"""线上稳定性加固回归测试（2026-11-20）。

对应本轮修复的 7 个已复现缺陷：
1. 环境变量空值/非法值不再让模块导入崩溃（配置错误降级，不变成线上 5xx）；
2. 配额计数落盘失败不打断生成链路，内存计数仍准确；
3. 后台刷新触发异常时仍返回健康缓存，不误降为兜底；
4. fallback.json 损坏/缺失时兜底链不抛异常，/data.js 仍返回 200；
5. 计数文件里的 bool / 字符串 / 负数不被当成已用次数（与 11-19 编号守卫同类）；
6. RETRY_TIMES<=0 抛明确 UpstreamError，而不是静默返回 None；
7. 上游 Code 为字符串 "0" 视为成功，非 0 仍按错误处理。

运行：python tests/test_stability_hardening.py（在仓库根目录下）
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

os.environ.pop("MOCK_MODE", None)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix="stability_"))

_saved = {
    "_QUOTA": app._QUOTA,
    "QUOTA_LIMITS": dict(app.QUOTA_LIMITS),
    "RETRY_TIMES": app.RETRY_TIMES,
    "RETRY_WAIT": app.RETRY_WAIT,
    "_chat": app._chat,
    "zhihu_get": app.zhihu_get,
    "load_today_cache": app.load_today_cache,
    "cache_age_seconds": app.cache_age_seconds,
    "FALLBACK_PATH": app.FALLBACK_PATH,
    "QUOTA_GUARD_DISABLED": app.QUOTA_GUARD_DISABLED,
}
app.RETRY_WAIT = 0
app.RETRY_TIMES = 1


def _restore():
    for name, value in _saved.items():
        if name == "QUOTA_LIMITS":
            app.QUOTA_LIMITS.clear()
            app.QUOTA_LIMITS.update(value)
        else:
            setattr(app, name, value)


# ---------- 场景1：非法环境变量不再拖垮启动 ----------
env = dict(os.environ)
env.update({
    "QUOTA_WEB_SEARCH_DAILY": "",     # 云控制台常见：留空
    "QUOTA_SEARCH_DAILY": " 20 ",    # 合法但带空格
    "RETRY_TIMES": "abc",            # 误填非数字
    "CACHE_TTL_HOURS": "三点",        # 误填中文
    "REFRESH_COOLDOWN_SECONDS": "-5"  # 负数，按 0 处理
})
probe = (
    "import app, json; "
    "print(json.dumps({'web': app.QUOTA_LIMITS['web_search'], "
    "'search': app.QUOTA_LIMITS['search'], 'retry': app.RETRY_TIMES, "
    "'ttl': app.CACHE_TTL_SECONDS, 'cooldown': app.REFRESH_COOLDOWN_SECONDS}))"
)
res = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT), env=env,
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
assert res.returncode == 0, f"模块导入不应崩溃：{res.stderr}"
vals = json.loads(res.stdout.strip().splitlines()[-1])
assert vals["web"] == 0, f"空值应回退默认 0，实际 {vals['web']}"
assert vals["search"] == 20, f"带空格的合法值应解析为 20，实际 {vals['search']}"
assert vals["retry"] == 6, f"非法整数应回退默认 6，实际 {vals['retry']}"
assert vals["ttl"] == 10800, f"非法浮点应回退默认 3 小时，实际 {vals['ttl']}"
assert vals["cooldown"] == 0, f"负数冷却应收敛到 0，实际 {vals['cooldown']}"

os.environ["STABILITY_TEST_INT"] = "0"
assert app._env_int("STABILITY_TEST_INT", 6, minimum=1) == 1, "RETRY_TIMES=0 必须被抬到至少 1"
os.environ["STABILITY_TEST_INT"] = " 7 "
assert app._env_int("STABILITY_TEST_INT", 6, minimum=1) == 7
os.environ["STABILITY_TEST_FLOAT"] = "1.5"
assert app._env_float("STABILITY_TEST_FLOAT", 3.0, minimum=0) == 1.5
os.environ["STABILITY_TEST_FLOAT"] = "nan"
assert app._env_float("STABILITY_TEST_FLOAT", 3.0, minimum=0) == 3.0, "nan 必须回退默认值"
os.environ["STABILITY_TEST_FLOAT"] = "inf"
assert app._env_float("STABILITY_TEST_FLOAT", 3.0, minimum=0) == 3.0, "inf 必须回退默认值"
del os.environ["STABILITY_TEST_INT"], os.environ["STABILITY_TEST_FLOAT"]
print("场景1 ✅ 空值/非法/越界环境变量一律降级，模块正常导入")


# ---------- 场景2：配额计数落盘失败不打断生成链路 ----------
g = app.QuotaGuard(TMP / "q2.json")


def _save_boom():
    raise OSError("read-only fs / disk full")


g._save = _save_boom
app._QUOTA = g
app.QUOTA_LIMITS["hot_list"] = 100
app.zhihu_get = lambda path, params=None: {"Items": [{"Title": "x", "Url": "u"}]}
assert app.api_hot_list(), "落盘失败时热榜接口仍应正常返回"
assert g.used("hot_list") == 1, "落盘失败时内存计数仍须准确"
print("场景2a ✅ 落盘异常被守卫吞掉，attempt 正常放行且计数准确")

bad_parent = TMP / "not_a_dir"
bad_parent.write_text("x", encoding="utf-8")
g_io = app.QuotaGuard(bad_parent / "q.json")   # 真实 IO 失败：父路径是文件
assert g_io.attempt("search") is True
assert g_io.used("search") == 1
print("场景2b ✅ 真实目录/磁盘错误同样不会穿透到调用方")


def fake_zhihu_get(path, params=None):
    if path.endswith("hot_list"):
        return {"Items": [{"Title": "热点甲", "Url": "https://www.zhihu.com/question/1"}]}
    return {"Items": [{
        "Title": "候选回答", "Url": "https://www.zhihu.com/question/1/answer/1",
        "ContentText": "摘要",
    }]}


def fake_chat(url, headers, payload, label):
    # _chat 的契约是直接返回模型输出文本（choices 解析由真实 _chat 负责）
    return json.dumps({
        "type": "consensus", "background": "这是一条正常生成的背景", "focus": "焦点",
        "consensus": "结论", "gap": "缺口", "reading": [],
    }, ensure_ascii=False)


app._QUOTA = g
app.QUOTA_LIMITS.update({"hot_list": 100, "search": 100, "zhida": 100})
app.zhihu_get = fake_zhihu_get
app._chat = fake_chat
brief = app.build_brief()
assert len(brief["items"]) == 1
assert brief["items"][0]["background"] != app.NO_BACKGROUND, "整份简报不应因计数落盘失败而全量降级"
print("场景2c ✅ 集成：落盘持续失败时整份简报仍完整生成，不再抛 OSError")


# ---------- 场景3：后台刷新触发异常时仍返回健康缓存 ----------
healthy = {
    "source": "cache", "items": [{"title": "健康缓存"}], "date": app.today_str(),
    "notice": "ok", "generated_at": "2026-11-20 19:00",
}
app.load_today_cache = lambda: dict(healthy)


def stat_boom():
    raise OSError("stat failed")


app.cache_age_seconds = stat_boom
out = app.get_brief()
assert out["source"] == "cache", f"健康缓存必须照常秒回，实际 {out['source']}"
print("场景3 ✅ 后台刷新的非关键异常被隔离，不再把健康缓存丢成兜底")


# ---------- 场景4：兜底链二次故障不再导致 /data.js 500 ----------
app.FALLBACK_PATH = TMP / "missing_fallback.json"
fb = app.load_fallback()
assert fb["source"] == "fallback" and isinstance(fb["items"], list), "兜底文件缺失应返回内存最小结构"
app.load_today_cache = lambda: (_ for _ in ()).throw(RuntimeError("主链路异常"))
resp = app.app.test_client().get("/data.js")
assert resp.status_code == 200, f"/data.js 不应 500，实际 {resp.status_code}"
assert b"window.BRIEF_DATA" in resp.data
print("场景4 ✅ fallback 缺失 + 主链路异常时 /data.js 仍返回 200，页面不白屏")


# ---------- 场景5：计数文件里的脏值不被当成已用次数 ----------
p5 = TMP / "q5.json"
p5.write_text(json.dumps({"date": app.today_str(), "counts": {
    "search": True,          # bool 不是次数
    "zhida": 5,              # 合法
    "hot_list": "99",        # 字符串不是次数
    "model_alt": -1,         # 负数无效
}}), encoding="utf-8")
g5 = app.QuotaGuard(p5)
assert g5.used("search") == 0 and type(g5.used("search")) is int
assert g5.used("zhida") == 5
assert g5.used("hot_list") == 0
assert g5.used("model_alt") == 0
app.QUOTA_LIMITS["search"] = 1
app._QUOTA = g5
assert g5.attempt("search") is True, "bool 计数不得让接口被误熔断"
print("场景5 ✅ bool/字符串/负数计数被拒绝，合法整数正常读回")


# ---------- 场景6：RETRY_TIMES<=0 抛明确错误，不静默返回 None ----------
app.RETRY_TIMES = 0
try:
    app._chat_with_retry("https://x", {}, {}, "知乎直答")
    raise AssertionError("RETRY_TIMES=0 应抛 UpstreamError")
except app.UpstreamError as exc:
    assert "RETRY_TIMES" in str(exc), f"错误信息应说明原因，实际：{exc}"
print("场景6 ✅ 空重试循环抛明确 UpstreamError，不再静默返回 None")


# ---------- 场景7：上游 Code 类型宽容 ----------
app.zhihu_get = _saved["zhihu_get"]


class FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


orig_urlopen = urllib.request.urlopen
try:
    urllib.request.urlopen = lambda *a, **k: FakeResp('{"Code":"0","Data":{"Items":[1,2]}}')
    assert app.zhihu_get("/x")["Items"] == [1, 2], "字符串 Code '0' 应视为成功"
    urllib.request.urlopen = lambda *a, **k: FakeResp('{"Code":429,"Message":"限流"}')
    try:
        app.zhihu_get("/x")
        raise AssertionError("非 0 Code 仍应报错")
    except app.UpstreamError as exc:
        assert "429" in str(exc)
finally:
    urllib.request.urlopen = orig_urlopen
print("场景7 ✅ Code='0' 视为成功，Code=429 仍按错误处理")


_restore()
print("全部通过")
