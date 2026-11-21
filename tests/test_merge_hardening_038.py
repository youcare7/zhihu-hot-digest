# -*- coding: utf-8 -*-
"""038 合并缺口回归测试（2026-11-21）。

背景：队友在另一条线把生产升级到 DeployId 038（gunicorn WSGI 化、/healthz、
DAILY_ZHIDA_LIMIT 直答日配额、_js_safe_json+nosniff、/app/data 建目录等），
并独立覆盖了 11-19 稳定性加固中的 5 项。但下列 8 类边界在 038 里仍未被覆盖
（均为「测试盲区」而非线上已发生故障）。本文件按项目 SOP 编写——先在
**未修复**的 038 上跑出 FAIL，再对 app.py 做最小修复，重跑应全 PASS，最后跑
全套回归确认 038 已有加固零退化。

8 个待补缺陷：
  G1  generate_brief 把 JSON 解析失败误判为「直答调用失败」，静默换备选模型、多烧 model_alt 配额
  G2  _is_valid_web_url 放行 loopback / 私网 / 云元数据 IP 字面量
  G3  api_* 未校验 Data.Items 类型（dict → 整批 KeyError/TypeError；字符串 → 静默单条降级）
  G4  _clip 把 dict/list/数字 repr 化后写进正文
  G5  build_web_evidence 的 EditTime 毫秒级/超大值换算异常击穿「核验绝不降级」契约
  G6  build_digest 对脏 item（非 dict / 未知 type / 非 list sides）抛异常或计数不自洽
  G7  load_fallback 内存最小兜底缺 digest（前端读 digest.total 等会崩）
  G8  /data.js 路由缺最后一层 try，json 序列化/转义异常仍可能 500（守 038 的 nosniff 不回退）

守门断言（确保补丁不回退 038 已有加固，应恒 PASS）：
  K1  zhihu_get 兼容字符串 "0"、排除 bool（038 已用 str().strip() 覆盖）
  K2  QuotaGuard._load 拒绝 bool 计数
  K3  QuotaGuard._save 自建父目录
  K4  /data.js 仍带 _js_safe_json 转义 + nosniff 头（不得回退为裸 json.dumps）

运行：python tests/test_merge_hardening_038.py（仓库根目录）
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
# G1 坏 JSON 不得被当成「直答调用失败」而换模型重试、消耗 model_alt 配额
# ---------------------------------------------------------------------------
def g1_generate_brief_alt_model():
    saved = (app.MODEL_API_KEY, app.MODEL_BASE, app.MODEL_NAME, app._chat,
             app._chat_with_retry, app._QUOTA, dict(app.QUOTA_LIMITS))
    app.MODEL_API_KEY, app.MODEL_BASE, app.MODEL_NAME = "k", "https://model.example.com", "m"
    app._QUOTA = app.QuotaGuard(TMP / "g1.json")
    app.QUOTA_LIMITS.update({"zhida": 90, "model_alt": 500})
    calls = {"n": 0}

    def chat(url, headers, payload, label):
        calls["n"] += 1
        return "不是 JSON"

    def chat_retry(url, headers, payload, label):
        calls["n"] += 0  # 直答主链路本身返回坏 JSON，不计入「换模型」次数
        return "不是 JSON"

    app._chat = chat
    app._chat_with_retry = chat_retry
    try:
        try:
            app.generate_brief("标题", "候选素材")
        except app.UpstreamError as exc:
            assert "不是合法 JSON" in str(exc) or "不是 JSON 对象" in str(exc), \
                f"应透传解析错误，实际 {exc}"
        else:
            raise AssertionError("坏 JSON 必须抛 UpstreamError")
        assert calls["n"] == 0, f"不应换备选模型，实际 _chat 被调用 {calls['n']} 次"
        assert app._QUOTA.used("model_alt") == 0, "坏 JSON 不应消耗 model_alt 配额"
    finally:
        (app.MODEL_API_KEY, app.MODEL_BASE, app.MODEL_NAME, app._chat,
         app._chat_with_retry, app._QUOTA, _) = saved
        restore_limits(saved[6])


# ---------------------------------------------------------------------------
# G2 站外链接须拒绝 loopback / 私网 / 云元数据 IP
# ---------------------------------------------------------------------------
def g2_web_url_ip_guard():
    for bad in ("https://127.0.0.1/x", "https://169.254.169.254/latest/meta-data",
                "http://10.0.0.1/a", "http://192.168.1.1/a", "https://[::1]/a"):
        assert not app._is_valid_web_url(bad), f"IP 字面量不得放行：{bad}"
    assert app._is_valid_web_url("https://news.example.com/a"), "正常站外域名不得回归"
    assert not app._is_valid_web_url("https://www.zhihu.com/question/1"), "知乎域名仍须排除"


# ---------------------------------------------------------------------------
# G3 Data.Items 非 list / 混入非 dict 时，api_* 须返回干净列表
# ---------------------------------------------------------------------------
def g3_items_type_guard():
    saved = (app.zhihu_get, app._QUOTA, dict(app.QUOTA_LIMITS))
    app._QUOTA = app.QuotaGuard(TMP / "g3.json")
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
# G4 _clip 脏类型不得 repr 化写进正文
# ---------------------------------------------------------------------------
def g4_clip_dirty_types():
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
        for field in ("background", "focus", "gap"):
            assert "{" not in str(item[field]) and "[" not in str(item[field]), \
                f"{field} 不得含 Python repr 特征：{item[field]!r}"
    finally:
        app.generate, app.fetch_search = saved


# ---------------------------------------------------------------------------
# G5 异常 EditTime 不得击穿核验契约（status 四值必落盘 + 去重标记必写入）
# ---------------------------------------------------------------------------
def g5_edit_time():
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
        # 合法秒级时间戳仍应换算出日期（不回归既有断言）
        app._WEB_SEARCH_TRIED.clear()
        app.api_web_search = lambda query, count=app.WEB_SEARCH_COUNT: [
            {"Title": "站外", "Url": "https://news.example.com/a", "EditTime": 1753632000}]
        ev = app.build_web_evidence("合法时间戳", "缺口")
        assert ev.get("status") == "found" and ev.get("published_at") == "2025-07-28", ev
    finally:
        app.api_web_search = saved
        app._WEB_SEARCH_TRIED.clear()


# ---------------------------------------------------------------------------
# G6 build_digest 遇脏 item / 未知 type / 非 list sides 不得抛异常，且 total 与三态自洽
# ---------------------------------------------------------------------------
def g6_build_digest_dirty_item():
    d = app.build_digest([
        None, "oops", 42,
        {"type": "consensus", "gap": "正常缺口"},
        {"type": "dispute", "sides": [{"label": "a", "claim": "b"}]},
        {"type": "unknown_type", "gap": "未知类型"},
        {"type": "dispute", "sides": 5},
        {"type": "dispute", "sides": "ab"},
    ])
    assert d["total"] == 8, f"total 须等于输入条数，实际 {d['total']}"
    assert d["dispute"] + d["consensus"] + d["verify"] == d["total"], \
        f"三态之和须等于 total：{d}"


# ---------------------------------------------------------------------------
# G7 load_fallback 内存最小兜底仍须含 digest（前端读 digest.total/headline）
# ---------------------------------------------------------------------------
def g7_fallback_minimal_has_digest():
    saved = (app.FALLBACK_PATH, app._start_background_refresh, app.MOCK_MODE)
    empty = TMP / "g7_empty_data"
    empty.mkdir(exist_ok=True)
    try:
        app.FALLBACK_PATH = empty / "does_not_exist.json"
        app.MOCK_MODE = False
        app._start_background_refresh = lambda: False
        data = app.load_fallback()
        assert isinstance(data.get("digest"), dict), f"最小兜底须含 digest，实际 {data}"
        assert "total" in data["digest"], f"digest 须含 total，实际 {data['digest']}"
        assert data["source"] == "fallback" and isinstance(data.get("items"), list), data
        # /data.js 全程 200 且可解析（守 038 不白屏契约）
        with app.app.test_client() as client:
            resp = client.get("/data.js")
            assert resp.status_code == 200, f"HTTP {resp.status_code}"
            body = resp.get_data(as_text=True)
            obj = json.loads(body[len("window.BRIEF_DATA = "):].rstrip().rstrip(";"))
            assert isinstance(obj.get("digest"), dict), obj
    finally:
        (app.FALLBACK_PATH, app._start_background_refresh, app.MOCK_MODE) = saved


# ---------------------------------------------------------------------------
# G8 /data.js 路由须有最后一层 try：get_brief 抛异常时仍输出可解析 JS，不 500
# ---------------------------------------------------------------------------
def g8_datajs_route_guard():
    saved = app.get_brief

    def boom():
        raise RuntimeError("模拟 get_brief 未预期异常")

    app.get_brief = boom
    try:
        with app.app.test_client() as client:
            resp = client.get("/data.js")
            assert resp.status_code == 200, f"get_brief 异常时仍须 200，实际 {resp.status_code}"
            body = resp.get_data(as_text=True)
            obj = json.loads(body[len("window.BRIEF_DATA = "):].rstrip().rstrip(";"))
            assert obj.get("source") == "fallback", obj
    finally:
        app.get_brief = saved


# ---------------------------------------------------------------------------
# 守门：以下在 038 已覆盖，补丁不得回退
# ---------------------------------------------------------------------------
def k1_code_type():
    def call(code):
        body = json.dumps({"Code": code, "Data": {"Items": []}}).encode("utf-8")
        with mock.patch.object(app.urllib.request, "urlopen", return_value=io.BytesIO(body)):
            return app.zhihu_get("/x")

    assert call(0) == {"Items": []}, "整数 0 须成功"
    assert call("0") == {"Items": []}, '字符串 "0" 须按成功兼容'
    for bad in (False, True, 1, None, "1", "error"):
        try:
            call(bad)
        except app.UpstreamError:
            continue
        raise AssertionError(f"Code={bad!r} 必须判为错误码")


def k2_quota_load_bool():
    p = TMP / "k2.json"
    p.write_text(json.dumps({"date": app.today_str(),
                             "counts": {"zhida": True, "hot_list": 3}}), encoding="utf-8")
    g = app.QuotaGuard(p)
    assert g.used("zhida") == 0, f"bool 不得计为 1，实际 {g.used('zhida')}"
    assert g.used("hot_list") == 3, "合法整数计数不得回归"


def k3_quota_save_mkdir():
    p = TMP / "k3_nested" / "deep" / "q.json"
    g = app.QuotaGuard(p)
    saved = dict(app.QUOTA_LIMITS)
    app.QUOTA_LIMITS["zhida"] = 90
    try:
        assert g.attempt("zhida") is True, "父目录不存在时 attempt 不得抛 FileNotFoundError"
        assert p.exists(), "计数文件须成功落盘"
    finally:
        restore_limits(saved)


def k4_js_safe_and_nosniff():
    src = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    assert "_js_safe_json" in src and "X-Content-Type-Options" in src, \
        "补丁不得回退 038 的 _js_safe_json 转义与 nosniff 头"
    assert "def _js_safe_json" in src, "_js_safe_json 定义须保留"
    with app.app.test_client() as client:
        # 让 get_brief 返回含 </script> 的内容，验证转义仍生效
        app.get_brief = lambda: {"date": "2026-11-21", "source": "cache", "generated_at": "x",
                                "notice": "</script><script>alert(1)</script>",
                                "digest": {}, "items": []}
        try:
            resp = client.get("/data.js")
            assert resp.status_code == 200
            assert resp.headers.get("X-Content-Type-Options") == "nosniff", "nosniff 头须保留"
            body = resp.get_data(as_text=True)
            assert "</script><script>" not in body, "危险字面量须被转义"
        finally:
            pass


# ---------------------------------------------------------------------------
for _name, _fn in [
    ("G1  坏 JSON 不换备选模型/不多烧 model_alt 配额", g1_generate_brief_alt_model),
    ("G2  站外链接拒绝 IP 字面量", g2_web_url_ip_guard),
    ("G3  Data.Items 类型守卫 + 元素 dict 过滤", g3_items_type_guard),
    ("G4  _clip 脏类型按缺失回落", g4_clip_dirty_types),
    ("G5  异常 EditTime 不击穿核验契约", g5_edit_time),
    ("G6  build_digest 脏 item / 未知 type / sides 自洽", g6_build_digest_dirty_item),
    ("G7  fallback 最小兜底含 digest（不白屏）", g7_fallback_minimal_has_digest),
    ("G8  /data.js 路由最后一层兜底不 500", g8_datajs_route_guard),
    ("K1  守门：Code 兼容 \"0\" / 排除 bool", k1_code_type),
    ("K2  守门：QuotaGuard._load 拒绝 bool", k2_quota_load_bool),
    ("K3  守门：QuotaGuard._save 自建父目录", k3_quota_save_mkdir),
    ("K4  守门：_js_safe_json + nosniff 不回退", k4_js_safe_and_nosniff),
]:
    check(_name, _fn)

_failed = [n for n, ok in RESULTS if not ok]
print(f"\n合计 {len(RESULTS) - len(_failed)}/{len(RESULTS)} 通过")
if _failed:
    print("失败场景：" + " / ".join(_failed))
    sys.exit(1)
print("全部通过")
