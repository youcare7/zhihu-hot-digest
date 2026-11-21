# -*- coding: utf-8 -*-
"""线上稳定性加固回归测试（2026-11-21）。

背景：既有 12 项 Python 测试全绿、线上 036 健康，但以下边界未被任何测试覆盖。
本文件按项目 SOP 编写——先在**未修复**的 app.py 上运行，确认场景 FAIL，
再对 app.py 做最小修复，重跑本文件应全部 PASS，最后跑全套回归确认无退化。

覆盖的 10 个缺陷（均为「测试盲区」而非线上已发生故障）：
  S1  build_web_evidence 的 EditTime 换算异常击穿「核验绝不降级」契约
  S2  load_fallback() 自身抛异常 → /data.js 500 白屏（兜底没有兜底）
  S3  _clip 把 dict/list/数字 repr 化后写进正文，并绕过降级判定
  S4  api_* 未校验 Data.Items 类型，上游异常结构导致整批/单条失败
  S5  QuotaGuard._load 的 isinstance(v, int) 放过 bool
  S6  zhihu_get 的 Code != 0 严格类型比较（"0" 误失败 / False 误成功）
  S7  QuotaGuard._save 未确保父目录存在
  S8  _is_valid_web_url 放行 loopback / 云元数据 IP
  S9  generate_brief 把 JSON 解析失败误判为「直答调用失败」，静默换模型重试
  S10 build_digest 对脏 item 直接 dict(_it)/len(sides) 抛异常使整批失败

运行：python tests/test_stability_hardening.py（仓库根目录）
"""
import io
import json
import os
import pathlib
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp())
RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True))
        print(f"  [PASS] {name}")
    except Exception as exc:
        RESULTS.append((name, False))
        print(f"  [FAIL] {name} -> {type(exc).__name__}: {exc}")


def restore_limits(saved):
    app.QUOTA_LIMITS.clear()
    app.QUOTA_LIMITS.update(saved)


# ---------------------------------------------------------------------------
# S1 异常 EditTime 不得击穿核验契约（status 四值必落盘 + 去重标记必写入）
# ---------------------------------------------------------------------------
def s1_edit_time():
    saved = app.api_web_search
    try:
        for bad in (1753632000000, 2 ** 63, 10 ** 30, -1, "1753632000", True, 1753632000):
            app._WEB_SEARCH_TRIED.clear()
            title = f"EditTime-{bad!r}"

            def web(query, count=app.WEB_SEARCH_COUNT, _b=bad):
                return [{"Title": "站外原始标题", "Url": "https://news.example.com/a",
                         "EditTime": _b}]

            app.api_web_search = web
            try:
                ev = app.build_web_evidence(title, "缺口词")
            except Exception as exc:
                ev = {"__raised__": f"{type(exc).__name__}: {exc}"}
            assert "__raised__" not in ev, f"EditTime={bad!r} 不应抛异常：{ev}"
            assert ev.get("status") in ("found", "no_result", "error", "skipped"), \
                f"status 四值必落盘：{ev}"
            assert ev.get("query") and ev.get("checked_at"), f"query/checked_at 必落盘：{ev}"
            assert title in app._WEB_SEARCH_TRIED, "去重标记必须写入，否则补跑会重复检索"
        # 合法秒级时间戳仍应换算出日期（不回归场景 1 的既有断言）
        app._WEB_SEARCH_TRIED.clear()
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [
            {"Title": "站外", "Url": "https://news.example.com/a", "EditTime": 1753632000}]
        ev = app.build_web_evidence("合法时间戳", "缺口")
        assert ev.get("status") == "found" and ev.get("published_at") == "2025-07-28", ev
    finally:
        app.api_web_search = saved
        app._WEB_SEARCH_TRIED.clear()


# ---------------------------------------------------------------------------
# S2 fallback.json 缺失/损坏时，/data.js 仍须 200 且 BRIEF_DATA 可解析
# ---------------------------------------------------------------------------
def s2_fallback_self_guard():
    saved = (app.DATA_DIR, app.FALLBACK_PATH, app.MOCK_MODE, app._start_background_refresh)
    empty = TMP / "s2_empty_data"
    empty.mkdir(exist_ok=True)
    try:
        app.DATA_DIR = empty
        app.FALLBACK_PATH = empty / "does_not_exist.json"
        app.MOCK_MODE = False
        app._start_background_refresh = lambda: False  # 不启动真实后台线程
        for broken in (False, True):
            if broken:
                app.FALLBACK_PATH.write_text('{"items": "not-a-list"', encoding="utf-8")
            with app.app.test_client() as client:
                resp = client.get("/data.js")
                assert resp.status_code == 200, f"兜底自身异常时仍须 HTTP 200，实际 {resp.status_code}"
                body = resp.get_data(as_text=True)
                prefix = "window.BRIEF_DATA = "
                assert body.startswith(prefix) and body.rstrip().endswith(";"), body[:120]
                obj = json.loads(body[len(prefix):].rstrip().rstrip(";"))
                assert obj.get("source") == "fallback", obj
                assert isinstance(obj.get("items"), list), obj
                assert isinstance(obj.get("digest"), dict), "最小兜底结构也须含 digest"
    finally:
        (app.DATA_DIR, app.FALLBACK_PATH, app.MOCK_MODE,
         app._start_background_refresh) = saved


# ---------------------------------------------------------------------------
# S3 _clip 脏类型不得 repr 化写进正文，且必须回落降级哨兵
# ---------------------------------------------------------------------------
def s3_clip_dirty_types():
    for bad in ({"a": 1}, ["x", "y"], 123, 1.5, True):
        got = app._clip(bad, 150)
        assert got == "", f"_clip({bad!r}) 应按缺失回落空串，实际 {got!r}"
    assert app._clip("正常文本", 150) == "正常文本", "正常字符串路径不得回归"
    assert app._clip("超长文本" * 40, 20), "正常超长文本仍须有输出"

    saved = (app.generate, app.fetch_search)
    app.generate = lambda title, ct: {
        "type": "consensus", "background": {"a": 1}, "focus": ["x"],
        "gap": {"k": "v"}, "consensus": 123, "sides": None, "reading": [],
    }
    app.fetch_search = lambda title, rank: [
        {"Title": "候选", "ContentText": "摘要", "Url": "https://www.zhihu.com/question/1/answer/2"}]
    try:
        item = app.process_item(1, {"Title": "脏类型热点", "Url": "https://www.zhihu.com/question/1"})
        assert item["background"] == app.NO_BACKGROUND, f"脏 background 须降级：{item['background']!r}"
        assert item["focus"] == app.EMPTY_FOCUS, f"脏 focus 须降级：{item['focus']!r}"
        assert item["gap"] == app.EMPTY_GAP, f"脏 gap 须降级：{item['gap']!r}"
        assert item["consensus"] == app.EMPTY_CONSENSUS
        for field in ("background", "focus", "gap", "consensus"):
            assert "{" not in str(item[field]) and "[" not in str(item[field]), \
                f"{field} 不得含 Python repr 特征：{item[field]!r}"
    finally:
        app.generate, app.fetch_search = saved


# ---------------------------------------------------------------------------
# S4 Data.Items 非 list / 混入非 dict 时，api_* 须返回干净列表
# ---------------------------------------------------------------------------
def s4_items_type_guard():
    saved = (app.zhihu_get, app._QUOTA, dict(app.QUOTA_LIMITS))
    app._QUOTA = app.QuotaGuard(TMP / "s4.json")
    app.QUOTA_LIMITS.update({"hot_list": 100, "search": 2000, "zhida": 90,
                             "model_alt": 500, "web_search": 20})
    try:
        for bad in ({"a": 1}, "oops", 123, None):
            app.zhihu_get = lambda path, params=None, _b=bad: {"Items": _b}
            assert app.api_hot_list() == [], f"Items={bad!r} 时 api_hot_list 须返回 []"
            assert app.api_search("q") == [], f"Items={bad!r} 时 api_search 须返回 []"
            assert app.api_web_search("q") == [], f"Items={bad!r} 时 api_web_search 须返回 []"
        app.zhihu_get = lambda path, params=None: {"Items": [None, "x", {"Title": "ok"}]}
        assert app.api_hot_list() == [{"Title": "ok"}], "热榜须过滤非 dict 元素"
        assert app.api_search("q") == [{"Title": "ok"}], "搜索候选须过滤非 dict 元素"
        app.zhihu_get = lambda path, params=None: {"Items": [{"Title": "a"}, {"Title": "b"}]}
        assert len(app.api_hot_list()) == 2, "正常列表不得回归"
    finally:
        app.zhihu_get, app._QUOTA = saved[0], saved[1]
        restore_limits(saved[2])


# ---------------------------------------------------------------------------
# S5 QuotaGuard._load 不得把 bool 当成计数
# ---------------------------------------------------------------------------
def s5_quota_load_bool():
    p = TMP / "s5.json"
    p.write_text(json.dumps({"date": app.today_str(),
                             "counts": {"zhida": True, "hot_list": 3}}), encoding="utf-8")
    g = app.QuotaGuard(p)
    assert g.used("zhida") == 0, f"bool 不得计为 1，实际 {g.used('zhida')}"
    assert g.used("hot_list") == 3, "合法整数计数不得回归"


# ---------------------------------------------------------------------------
# S6 Code 类型比较：0 与 "0" 成功；False / 1 / None / "1" 必须判错
# ---------------------------------------------------------------------------
def s6_code_type():
    def call(code):
        body = json.dumps({"Code": code, "Data": {"Items": []}}).encode("utf-8")
        with mock.patch.object(app.urllib.request, "urlopen", return_value=io.BytesIO(body)):
            return app.zhihu_get("/x")

    assert call(0) == {"Items": []}, "整数 0 须成功"
    assert call("0") == {"Items": []}, '字符串 "0" 须按成功兼容（旧代码误判为错误码）'
    for bad in (False, True, 1, None, "1", "error"):
        try:
            call(bad)
        except app.UpstreamError:
            continue
        raise AssertionError(f"Code={bad!r} 必须判为错误码")


# ---------------------------------------------------------------------------
# S7 QuotaGuard._save 须自建父目录
# ---------------------------------------------------------------------------
def s7_quota_save_mkdir():
    p = TMP / "s7_nested" / "deep" / "q.json"
    g = app.QuotaGuard(p)
    saved = dict(app.QUOTA_LIMITS)
    app.QUOTA_LIMITS["zhida"] = 90
    try:
        assert g.attempt("zhida") is True, "父目录不存在时 attempt 不得抛 FileNotFoundError"
        assert p.exists(), "计数文件须成功落盘"
    finally:
        restore_limits(saved)


# ---------------------------------------------------------------------------
# S8 站外链接须拒绝 loopback / 私网 / 云元数据 IP
# ---------------------------------------------------------------------------
def s8_web_url_ip_guard():
    for bad in ("https://127.0.0.1/x", "https://169.254.169.254/latest/meta-data",
                "http://10.0.0.1/a", "http://192.168.1.1/a", "https://[::1]/a"):
        assert not app._is_valid_web_url(bad), f"IP 字面量不得放行：{bad}"
    assert app._is_valid_web_url("https://news.example.com/a"), "正常站外域名不得回归"
    assert not app._is_valid_web_url("https://www.zhihu.com/question/1"), "知乎域名仍须排除"


# ---------------------------------------------------------------------------
# S9 坏 JSON 不得被当成「直答调用失败」而换模型重试、消耗 model_alt 配额
# ---------------------------------------------------------------------------
def s9_generate_brief_alt_model():
    saved = (app.MODEL_API_KEY, app.MODEL_BASE, app.MODEL_NAME, app._chat,
             app._QUOTA, dict(app.QUOTA_LIMITS), app.RETRY_TIMES, app.RETRY_WAIT)
    app.MODEL_API_KEY, app.MODEL_BASE, app.MODEL_NAME = "k", "https://model.example.com", "m"
    app._QUOTA = app.QuotaGuard(TMP / "s9.json")
    app.QUOTA_LIMITS.update({"zhida": 90, "model_alt": 500})
    app.RETRY_TIMES, app.RETRY_WAIT = 1, 0
    calls = {"n": 0}

    def chat(url, headers, payload, label):
        calls["n"] += 1
        return "不是 JSON"

    app._chat = chat
    try:
        try:
            app.generate_brief("标题", "候选素材")
        except app.UpstreamError as exc:
            assert "不是合法 JSON" in str(exc), f"应透传解析错误，实际 {exc}"
        else:
            raise AssertionError("坏 JSON 必须抛 UpstreamError")
        assert calls["n"] == 1, f"不应换模型重试，实际调用 {calls['n']} 次"
        assert app._QUOTA.used("model_alt") == 0, "坏 JSON 不应消耗 model_alt 配额"
        assert app._QUOTA.used("zhida") == 1, "直答配额须正常计 1 次"
    finally:
        (app.MODEL_API_KEY, app.MODEL_BASE, app.MODEL_NAME, app._chat,
         app._QUOTA, _, app.RETRY_TIMES, app.RETRY_WAIT) = saved
        restore_limits(saved[5])


# ---------------------------------------------------------------------------
# S10 build_digest 遇到脏 item 不得抛异常，且 total 与三态之和自洽
# ---------------------------------------------------------------------------
def s10_build_digest_dirty_item():
    d = app.build_digest([
        None, "oops", 42,
        {"type": "consensus", "gap": "正常缺口"},
        {"type": "dispute", "sides": [{"label": "a", "claim": "b"}]},
    ])
    assert d["total"] == 5, f"total 须等于输入条数，实际 {d['total']}"
    assert d["dispute"] + d["consensus"] + d["verify"] == d["total"], \
        f"三态之和须等于 total：{d}"


# ---------------------------------------------------------------------------
for _name, _fn in [
    ("S1  异常 EditTime 不击穿核验契约", s1_edit_time),
    ("S2  fallback 自身异常时 /data.js 不白屏", s2_fallback_self_guard),
    ("S3  _clip 脏类型按缺失回落", s3_clip_dirty_types),
    ("S4  Data.Items 类型守卫", s4_items_type_guard),
    ("S5  QuotaGuard._load 拒绝 bool", s5_quota_load_bool),
    ("S6  Code 类型严格比较", s6_code_type),
    ("S7  QuotaGuard._save 自建父目录", s7_quota_save_mkdir),
    ("S8  站外链接拒绝 IP 字面量", s8_web_url_ip_guard),
    ("S9  坏 JSON 不换模型重试", s9_generate_brief_alt_model),
    ("S10 build_digest 脏 item 不整批失败", s10_build_digest_dirty_item),
]:
    check(_name, _fn)

_failed = [n for n, ok in RESULTS if not ok]
print(f"\n合计 {len(RESULTS) - len(_failed)}/{len(RESULTS)} 通过")
if _failed:
    print("失败场景：" + " / ".join(_failed))
    sys.exit(1)
print("全部通过")
