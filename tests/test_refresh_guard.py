# -*- coding: utf-8 -*-
"""验证后台 TTL 刷新的「防劣化保护」：
刷新结果的成功条目数若少于旧缓存（如直答额度触顶导致大面积降级），
必须丢弃新结果、沿用旧缓存——宁留旧全绿，不覆新残次。

运行：python tests/test_refresh_guard.py（在仓库根目录下）
"""
import json
import os
import pathlib
import sys
import tempfile
import time

os.environ.pop("MOCK_MODE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

app.DATA_DIR = pathlib.Path(tempfile.mkdtemp())
app.CACHE_TTL_SECONDS = 3 * 3600  # TTL 3 小时

OLD_BG = "旧缓存的完整解读"
NEW_BG = "新缓存的新解读"


def make_item(rank, title, background):
    return {"rank": rank, "title": title, "question_url": "",
            "background": background, "viewpoints": {}, "reading": []}


def item_ok(it):
    return it.get("background") != app.NO_BACKGROUND


# ---------- 捕获日志，用于等待后台刷新线程结束 ----------
log_lines = []
orig_log = app._log
app._log = lambda msg: (log_lines.append(msg), orig_log(msg))


def wait_refresh_done(timeout=60):
    """等待后台刷新线程打出结束日志（完成/丢弃/失败都算结束）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for m in log_lines:
            if ("后台自动刷新完成" in m or "丢弃本次结果" in m
                    or "后台自动刷新失败" in m):
                return m
        time.sleep(0.2)
    raise AssertionError("等待超时：后台刷新线程未在预期时间内结束")


# ---------- 准备：写入一份 3/3 全绿的旧缓存，并把 mtime 拨到 TTL 之外 ----------
old_data = {"date": app.today_str(), "source": "live", "generated_at": "旧",
            "notice": "", "items": [make_item(i, f"旧热点{i}", OLD_BG) for i in range(1, 4)]}
path = app.cache_path()
path.write_text(json.dumps(old_data, ensure_ascii=False), encoding="utf-8")
old_time = time.time() - 4 * 3600
os.utime(path, (old_time, old_time))
content_before = path.read_text(encoding="utf-8")

# ---------- mock 上游 ----------
app.api_hot_list = lambda: [{"Title": f"新热点{i}", "Url": f"https://www.zhihu.com/question/{i}"}
                            for i in (1, 2, 3)]
app.fetch_search = lambda title, rank: [{"Title": "候选", "Url": "https://www.zhihu.com/question/1/answer/9"}]


# ---------- 场景1：新一次生成持续只成功 2/3 条（新热点2 永远失败）→ 必须丢弃、沿用旧缓存 ----------
def always_fail_generate(title, text):
    if title == "新热点2":
        raise RuntimeError("429 限流（模拟额度触顶）")
    return {"background": NEW_BG,
            "viewpoints": {"support": "支持", "oppose": "反对", "undecided": "未定论"},
            "reading": [{"index": 1, "reason": "视角独特"}]}


app.generate = always_fail_generate
log_lines.clear()
app.get_brief()
end_msg = wait_refresh_done()

assert "丢弃本次结果" in end_msg, f"应命中防劣化丢弃逻辑，实际结束日志：{end_msg}"
assert path.read_text(encoding="utf-8") == content_before, \
    "防劣化保护失效：2/3 成功的新结果覆盖了 3/3 全绿的旧缓存"
current = json.loads(path.read_text(encoding="utf-8"))
assert all(OLD_BG in it["background"] for it in current["items"]), "缓存内容应为旧的全绿数据"
print("场景1 ✅ 新结果持续 2/3 成功 < 旧缓存 3/3，命中丢弃逻辑，沿用旧缓存")

# ---------- 场景2：新结果 3/3 不劣于旧缓存 → 应正常覆盖 ----------
app.generate = lambda title, text: {"background": NEW_BG,
                                    "viewpoints": {"support": "支持", "oppose": "反对", "undecided": "未定论"},
                                    "reading": [{"index": 1, "reason": "视角独特"}]}
log_lines.clear()
app.get_brief()
end_msg = wait_refresh_done()
assert "后台自动刷新完成" in end_msg, f"应正常完成刷新，实际结束日志：{end_msg}"
cur = json.loads(path.read_text(encoding="utf-8"))
assert all(NEW_BG in it["background"] for it in cur["items"]), \
    "新结果成功条数不劣于旧缓存时，应正常覆盖"
print("场景2 ✅ 新结果 3/3 成功不劣于旧缓存，正常覆盖")

print("全部通过")
