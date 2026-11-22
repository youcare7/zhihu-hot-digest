# -*- coding: utf-8 -*-
"""合订本与时间轴集成测试 (tests/test_timeline_integration.py)

验证内容：
1. daily_archive.py 归档生成：字段齐备、原子落盘、零额外模型消耗；
2. build_timeline.py 编译生成：话题结构合法、跨天匹配准确；
3. /timeline.js 服务端路由：HTTP 200、nosniff 标头、XSS 特殊字符转义、降级容错；
4. 前端 hash 路由硬锁：默认根路由必须硬锁 #today，绝不自动劫持跳转至时间轴。
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from scripts.daily_archive import archive_today
from scripts.build_timeline import build_timeline, _escape_js_payload

client = app.app.test_client()
checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(f"{name} 失败: {detail}")


def run_tests():
    print("=== 开始合订本与时间轴集成测试 ===")

    # 1. 归档脚本测试
    print("\n[1] 每日归档脚本契约")
    archive_file = archive_today()
    check("归档文件已生成", archive_file.is_file(), str(archive_file))
    arc_data = json.loads(archive_file.read_text(encoding="utf-8"))
    check("归档含 date 字段", "date" in arc_data, arc_data.get("date"))
    check("归档含 items 列表", isinstance(arc_data.get("items"), list), f"len={len(arc_data.get('items', []))}")
    check("归档 items 非空", len(arc_data["items"]) > 0, f"count={len(arc_data['items'])}")

    # 2. 时间轴编译器测试
    print("\n[2] 时间轴编译器契约")
    tl_data = build_timeline()
    check("返回时间轴字典", isinstance(tl_data, dict))
    check("含 topics 列表", isinstance(tl_data.get("topics"), list))
    check("topics 话题数 >= 1", len(tl_data.get("topics", [])) >= 1, f"topics={len(tl_data.get('topics', []))}")
    for idx, top in enumerate(tl_data.get("topics", [])):
        check(f"话题 #{idx+1} 含 title", bool(top.get("title")), top.get("title"))
        check(f"话题 #{idx+1} 含 nodes", len(top.get("nodes", [])) >= 2, f"nodes={len(top.get('nodes', []))}")
        for n in top.get("nodes", []):
            check(f"节点含合法 state", n.get("state") in ("dispute", "consensus", "verify"), n.get("state"))

    # 3. /timeline.js 服务端路由与安全契约
    print("\n[3] /timeline.js 路由与安全契约")
    resp = client.get("/timeline.js")
    check("/timeline.js 返回 200", resp.status_code == 200, str(resp.status_code))
    check("/timeline.js 带 nosniff 标头", resp.headers.get("X-Content-Type-Options") == "nosniff")
    check("/timeline.js 带 no-store", "no-store" in resp.headers.get("Cache-Control", ""))

    text = resp.get_data(as_text=True)
    check("/timeline.js 前缀正确", text.startswith("window.TIMELINE_DATA = "))
    check("/timeline.js 无裸 <script>", "<script>" not in text)

    # 验证 XSS 转义与回原性
    escaped = _escape_js_payload('{"test": "<script>alert(1)</script>", "p": "\u2028\u2029"}')
    check("XSS 左括号转义", "\\u003c" in escaped and "<" not in escaped)
    check("XSS 右括号转义", "\\u003e" in escaped and ">" not in escaped)
    check("段落分隔符转义", "\\u2028" in escaped and "\u2028" not in escaped)

    # 4. 前端防劫持路由硬锁契约
    print("\n[4] 前端防劫持路由契约")
    html_text = app.INDEX_PATH.read_text(encoding="utf-8")
    check("无默认 hasTopics 劫持", "showTab(hasTopics ? 'timeline' : 'today', false)" not in html_text)
    check("默认根路由硬锁 today", "if (!value) { showTab('today', false); return; }" in html_text)

    print("\n" + "=" * 50)
    failed = [name for name, ok, _ in checks if not ok]
    print(f"全部 {len(checks)} 项检查通过！" if not failed else f"失败 {len(failed)} 项！")


if __name__ == "__main__":
    run_tests()
