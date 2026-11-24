# -*- coding: utf-8 -*-
"""深夜轮 bug 修复回归：归档健康闸门 + 时间线前后端字段契约 + 主服务原子写入重试。

覆盖的三个真实缺陷：
1. daily_archive 用 `background != NO_BACKGROUND` 判健康，字段缺失/None/空白时
   恒为真，5 条全无解读的简报也能写进真实归档（污染合订本，违反防幻觉红线）。
2. build_timeline 产出的 ext_ref / key_answer.url / date 与前端消费字段不匹配：
   前端读 topic.question_url、node.date_label 永远拿不到，站外信源与高赞回答
   完全不渲染；无 qid 时还把内部聚类键 "title:xxx" 暴露成 question_id。
3. app.py 首访生成与后台刷新的缓存写入没有 PermissionError 重试（两个脚本有）。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
from scripts import build_timeline, daily_archive  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if ok:
        print("PASS", name)
    else:
        failures.append(name + (f": {detail}" if detail else ""))
        print("FAIL", name, detail)


def healthy_item(i):
    return {
        "rank": i,
        "title": f"话题{i}",
        "question_url": f"https://www.zhihu.com/question/{i}",
        "background": "这是一段真实可用的背景解读",
        "focus": "可核验材料",
        "gap": "公开原始材料",
        "reading": [{"title": "代表回答", "url": f"https://www.zhihu.com/question/{i}/answer/1"}],
    }


def run_archive(items, target="2026-11-24", preexisting=None):
    """在隔离的临时 DATA_DIR 里跑一次归档，返回 (exit_code, 归档是否存在, 归档文本)。"""
    with tempfile.TemporaryDirectory(prefix="bugfix-archive-") as temp:
        root = pathlib.Path(temp)
        old_dir, old_build = app.DATA_DIR, app.build_brief
        old_secret = os.environ.get("ZHIHU_ACCESS_SECRET")
        app.DATA_DIR = root
        app.build_brief = lambda: {"date": target, "source": "live", "items": items}
        # build_brief 已被 mock 为固定返回值，不会真正请求知乎；
        # 但 daily_archive 的密钥预检要求 ZHIHU_ACCESS_SECRET 非空才会进入生成路径，
        # 这里给一个占位值，让 source / 日期 / 条数闸门继续被真实覆盖。
        os.environ["ZHIHU_ACCESS_SECRET"] = "test-secret-for-archive-only"
        try:
            if preexisting is not None:
                dest = root / "archive" / f"{target}.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(preexisting, ensure_ascii=False), encoding="utf-8")
            rc = daily_archive.main(["--date", target])
            path = root / "archive" / f"{target}.json"
            return rc, path.exists(), path.read_text(encoding="utf-8") if path.exists() else ""
        finally:
            app.DATA_DIR, app.build_brief = old_dir, old_build
            if old_secret is None:
                os.environ.pop("ZHIHU_ACCESS_SECRET", None)
            else:
                os.environ["ZHIHU_ACCESS_SECRET"] = old_secret


# ---------------------------------------------------------------------------
# 1. 归档健康闸门
# ---------------------------------------------------------------------------
good = [healthy_item(i) for i in range(5)]
rc, exists, _ = run_archive(good)
check("5 条真实解读可归档", rc == 0 and exists)

for label, bad_item in (
    ("background=None", dict(healthy_item(0), background=None)),
    ("background 缺失", {k: v for k, v in healthy_item(0).items() if k != "background"}),
    ("background 空串", dict(healthy_item(0), background="")),
    ("background 纯空白", dict(healthy_item(0), background="   \n ")),
    ("background 哨兵文案", dict(healthy_item(0), background=app.NO_BACKGROUND)),
    ("background 非字符串", dict(healthy_item(0), background=123)),
    ("title 缺失", {k: v for k, v in healthy_item(0).items() if k != "title"}),
):
    items = [dict(bad_item, rank=i, title=(bad_item.get("title") or "") + str(i))
             for i in range(5)]
    # title 缺失的用例保持无标题，避免上面的拼接把它补回来
    if label == "title 缺失":
        items = [{k: v for k, v in bad_item.items()} for _ in range(5)]
    rc, exists, _ = run_archive(items)
    check(f"闸门拒绝 {label}", rc != 0 and not exists, f"rc={rc} exists={exists}")

rc, exists, _ = run_archive(good[:4] + [dict(healthy_item(9), background=None)])
check("4 健康 + 1 降级仍拒绝", rc != 0 and not exists)

existing = {"date": "2026-11-24", "source": "cache", "items": [{"keep": True}]}
rc, exists, text = run_archive(
    [dict(healthy_item(i), background=None) for i in range(5)], preexisting=existing)
check("降级简报不覆盖已有健康归档", rc != 0 and exists and json.loads(text) == existing)

check("has_healthy_background 接受健康条目", daily_archive.has_healthy_background(healthy_item(1)))
check("has_healthy_background 拒绝 None", not daily_archive.has_healthy_background({"title": "x", "background": None}))
check("has_healthy_background 拒绝哨兵", not daily_archive.has_healthy_background(
    {"title": "x", "background": app.NO_BACKGROUND}))

# ---------------------------------------------------------------------------
# 2. 时间线 URL 与日期工具
# ---------------------------------------------------------------------------
check("date_label 中文格式", build_timeline.date_label("2026-11-21") == "11月21日")
check("date_label 非法值回落", build_timeline.date_label("bad") == "bad")
check("safe_http_url 放行 https", build_timeline.safe_http_url("https://example.com/a") == "https://example.com/a")
for bad_url in ("javascript:alert(1)", "data:text/html,x", "/relative/path", "ftp://x/y", "  "):
    check(f"safe_http_url 拒绝 {bad_url!r}", build_timeline.safe_http_url(bad_url) == "")
check("question_id 站外链接不提取", build_timeline.question_id(
    {"question_url": "https://www.ifeng.com/c/8yCw4liRNSv"}) == "")

# ---------------------------------------------------------------------------
# 3. 时间线产物字段契约（临时归档，跨两天）
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="bugfix-timeline-") as temp:
    root = pathlib.Path(temp)
    archive_dir = root / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    aliases = root / "topic_aliases.json"
    aliases.write_text(json.dumps({"aliases": {}, "splits": [], "hidden_topics": []}), encoding="utf-8")

    def item(qid, title, evidence=None, reading=None):
        return {
            "rank": 1, "title": title,
            "question_url": f"https://www.zhihu.com/question/{qid}" if qid else "https://www.ifeng.com/c/news",
            "type": "verify", "background": "有效解读", "focus": "可核验材料", "gap": "公开原始材料",
            "reading": reading if reading is not None else [
                {"title": "代表回答", "url": "https://www.zhihu.com/question/1/answer/1"}],
            "web_evidence": evidence,
        }

    day1 = [
        item("123", "神舟二十四号载人飞船发射", evidence={"status": "found", "title": "公开信源", "url": "https://example.com/a"}),
        item("", "幼儿园托班新政落地", evidence={"status": "found", "title": "伪协议", "url": "javascript:alert(1)"}),
    ]
    day2 = [item("123", "神舟二十四号载人飞船发射"), item("", "幼儿园托班新政落地")]
    for date, items in (("2026-11-20", day1), ("2026-11-21", day2)):
        (archive_dir / f"{date}.json").write_text(
            json.dumps({"date": date, "source": "cache", "items": items}, ensure_ascii=False), encoding="utf-8")

    old_archive, old_aliases = build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH
    build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH = archive_dir, aliases
    try:
        result = build_timeline.build_timeline(2)
    finally:
        build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH = old_archive, old_aliases

    topics = {t["title"]: t for t in result["topics"]}
    check("跨天话题产出", len(result["topics"]) == 2, f"实际 {len(result['topics'])}")
    for topic in result["topics"]:
        for node in topic["nodes"]:
            check(f"{topic['title']} node 有 date_label",
                  node.get("date_label") in ("11月20日", "11月21日"))
            for ref in node.get("ext_ref") or []:
                check("ext_ref 只含 http/https", build_timeline.safe_http_url(ref.get("url")) != "")

    a = topics.get("神舟二十四号载人飞船发射")
    if a:
        check("有 qid 时 question_id 为真实数字", a.get("question_id") == "123")
        check("有 qid 时给出可核验链接",
              a.get("question_url") == "https://www.zhihu.com/question/123")
    else:
        check("有 qid 话题存在", False)

    b = topics.get("幼儿园托班新政落地")
    if b:
        check("无 qid 时 question_id 不再被聚类键污染", b.get("question_id") == "")
        check("无 qid 时绝不编造 question_url", "question_url" not in b)
    else:
        check("无 qid 话题存在", False)

# 3b. 不同真实问题号不得仅凭标题相似被误合并（否则会凭空造出假跨天话题）
with tempfile.TemporaryDirectory(prefix="bugfix-merge-") as temp:
    root = pathlib.Path(temp)
    archive_dir = root / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    aliases = root / "topic_aliases.json"
    aliases.write_text(json.dumps({"aliases": {}, "splits": [], "hidden_topics": []}), encoding="utf-8")

    def merge_item(qid, title):
        return {
            "rank": 1, "title": title,
            "question_url": f"https://www.zhihu.com/question/{qid}",
            "type": "verify", "background": "有效解读", "focus": "可核验材料", "gap": "公开原始材料",
            "reading": [{"title": "代表回答", "url": f"https://www.zhihu.com/question/{qid}/answer/1"}],
            "web_evidence": None,
        }

    for date in ("2026-11-20", "2026-11-21"):
        (archive_dir / f"{date}.json").write_text(json.dumps({
            "date": date, "source": "cache",
            "items": [merge_item("301", "长征九号火箭试车成功"),
                      merge_item("302", "长征九号火箭试车推迟")],
        }, ensure_ascii=False), encoding="utf-8")

    old_archive, old_aliases = build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH
    build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH = archive_dir, aliases
    try:
        merged = build_timeline.build_timeline(2)
    finally:
        build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH = old_archive, old_aliases

    ids = {t["topic_id"] for t in merged["topics"]}
    check("不同 qid 不被标题相似误合并", len(merged["topics"]) == 2 and "301" in ids and "302" in ids,
          f"实际 topics={ids}")

# ---------------------------------------------------------------------------
# 4. 前端渲染契约
# ---------------------------------------------------------------------------
html = app.INDEX_PATH.read_text(encoding="utf-8")
block = html.split("// ---------- 合订本", 1)[1]
check("前端有 URL 白名单", "function safeTimelineUrl(" in block)
check("前端渲染站外信源", "node.ext_ref" in block and "node-refs" in block)
check("前端高赞视角可点击", "timelineLink(node.key_answer.url" in block)
check("前端优先用 date_label", "node.date_label || node.date" in block)
check("前端状态类白名单", "function timelineState(" in block)
check("时间线区块无 innerHTML", "innerHTML" not in block)
check("时间线区块仍用 textContent", "textContent" in block)
check("CSS 有 .node-refs 样式", ".node-refs {" in html)

# ---------------------------------------------------------------------------
# 5. app.py 原子写入 PermissionError 重试
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="bugfix-atomic-") as temp:
    root = pathlib.Path(temp)
    real_replace = app.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("simulated Windows file lock")
        return real_replace(src, dst)

    path = root / "brief_test.json"
    with mock.patch.object(app.os, "replace", flaky), mock.patch.object(app.time, "sleep", lambda _: None):
        app.atomic_write_json(path, {"ok": True})
    check("瞬时文件锁重试后写入成功", calls["n"] == 3 and
          json.loads(path.read_text(encoding="utf-8")) == {"ok": True})

    path2 = root / "brief_fail.json"
    raised = False
    with mock.patch.object(app.os, "replace", side_effect=PermissionError("locked")), \
            mock.patch.object(app.time, "sleep", lambda _: None):
        try:
            app.atomic_write_json(path2, {"ok": False})
        except PermissionError:
            raised = True
    check("持续锁定 5 次后明确抛错", raised and not path2.exists())

if failures:
    print("\nFAILURES:", len(failures))
    for failure in failures:
        print("-", failure)
    raise SystemExit(1)
print("\nALL BUGFIX REGRESSION TESTS PASSED")
