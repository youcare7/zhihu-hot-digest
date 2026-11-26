# -*- coding: utf-8 -*-
"""离线快照生成回归测试（针对数据注入锚点失效 / timeline 未内联 / 时区口径错误）。

运行：python tests/test_build_snapshot.py（在仓库根目录下）
"""
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_snapshot as snap

failures = []


def check(name, ok, detail=""):
    if ok:
        print("PASS", name)
    else:
        failures.append(name + (f": {detail}" if detail else ""))
        print("FAIL", name, detail)


DATA = {
    "date": "2026-11-26", "source": "cache",
    "generated_at": "2026-11-26 16:18", "notice": "",
    "digest": {"total": 1},
    "items": [{
        "rank": 1, "title": "某热点", "question_url": "https://www.zhihu.com/question/1",
        "background": "有效解读", "reading": [{"title": "r", "url": "https://example.com"}],
    }],
}
TIMELINE = {
    "generated_at": "2026-11-26 16:18", "archive_files": 4, "tracked_days": 6,
    "missing_dates": [], "topics": [{
        "topic_id": "t1", "title": "跨天话题", "question_ids": ["1", "2"], "days": 2,
    }],
    "archives": [{"date": "2026-11-26", "item_count": 1, "items": []}],
}

# 1. 锚点必须容忍 index.html 的缓存击穿查询串
index_html = (
    "<html><body><div id=\"badge\"></div>"
    "<script src=\"data.js?v=2\"></script>"
    "<script src=\"timeline.js?v=1\"></script>"
    "</body></html>"
)
check("data.js 锚点匹配带 ?v=2 标签", bool(snap.DATA_TAG_RE.search(index_html)))
check("timeline.js 锚点匹配带 ?v=1 标签", bool(snap.TIMELINE_TAG_RE.search(index_html)))

# 2. inline_payload 应真正替换标签并内联合法 JSON
data_raw = "window.BRIEF_DATA = " + json.dumps(DATA, ensure_ascii=False) + ";"
snapshot, _ = snap.inline_payload(index_html, snap.DATA_TAG_RE, "BRIEF_DATA", data_raw, "data")
check("data.js 外链标签已移除", not snap.DATA_TAG_RE.search(snapshot))
check("BRIEF_DATA 已内联", ("window.BRIEF_DATA = " + json.dumps(DATA, ensure_ascii=False) + ";") in snapshot)

timeline_raw = "window.TIMELINE_DATA = " + json.dumps(TIMELINE, ensure_ascii=False) + ";"
snapshot, _ = snap.inline_payload(snapshot, snap.TIMELINE_TAG_RE, "TIMELINE_DATA", timeline_raw, "timeline")
check("timeline.js 外链标签已移除", not snap.TIMELINE_TAG_RE.search(snapshot))
check("TIMELINE_DATA 已内联", ("window.TIMELINE_DATA = " + json.dumps(TIMELINE, ensure_ascii=False) + ";") in snapshot)

# 3. 锚点缺失必须报错而非静默跳过
try:
    snap.inline_payload("<html></html>", snap.DATA_TAG_RE, "BRIEF_DATA", data_raw, "data")
    check("锚点缺失时抛 SystemExit", False, "未抛异常")
except SystemExit:
    check("锚点缺失时抛 SystemExit", True)
try:
    snap.inline_payload(index_html, snap.DATA_TAG_RE, "BRIEF_DATA", "not-json-prefix", "data")
    check("坏 JSON 时抛 SystemExit", False, "未抛异常")
except SystemExit:
    check("坏 JSON 时抛 SystemExit", True)

# 4. 时区口径必须是 UTC+8
check("CN_TZ 为 UTC+8", snap.CN_TZ.utcoffset(None).total_seconds() == 8 * 3600)

# 5. 完整 main() 端到端：用临时 INDEX/ROOT + mock urlopen，验证真的产出可用快照
tmp = pathlib.Path(tempfile.mkdtemp(prefix="snapshot-test-"))
(tmp / "demo").mkdir(parents=True)
(tmp / "demo" / "index.html").write_text(index_html, encoding="utf-8")


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload


def fake_urlopen(url, timeout):
    return FakeResponse(data_raw if url.endswith("/data.js") else timeline_raw)


old_index, old_root, old_urlopen = snap.INDEX, snap.ROOT, snap.urllib.request.urlopen
try:
    snap.INDEX = tmp / "demo" / "index.html"
    snap.ROOT = tmp
    snap.urllib.request.urlopen = fake_urlopen
    snap.main()
    outputs = list(tmp.glob("热榜深潜简报_离线快照_*.html"))
    check("端到端生成一份快照", len(outputs) == 1, f"count={len(outputs)}")
    if outputs:
        content = outputs[0].read_text(encoding="utf-8")
        check("端到端：无 data.js 外链", not snap.DATA_TAG_RE.search(content))
        check("端到端：无 timeline.js 外链", not snap.TIMELINE_TAG_RE.search(content))
        check("端到端：BRIEF_DATA 有值", "window.BRIEF_DATA = " in content)
        check("端到端：TIMELINE_DATA 有值", "window.TIMELINE_DATA = " in content)
        check("端到端：含北京时间标注", "北京时间" in content)
finally:
    snap.INDEX, snap.ROOT, snap.urllib.request.urlopen = old_index, old_root, old_urlopen

if failures:
    print("\nFAILURES:", len(failures))
    for failure in failures:
        print("-", failure)
    raise SystemExit(1)
print("\nALL SNAPSHOT TESTS PASSED")
