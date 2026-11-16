# -*- coding: utf-8 -*-
"""验证缓存过期自动刷新（stale-while-revalidate）：
场景A 过期缓存秒回旧数据 + 后台刷新成新数据；场景B 新鲜缓存不触发刷新。

运行：python tests/test_cache_ttl.py（在仓库根目录下）
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


def patch_upstream():
    app.api_hot_list = lambda: [{"Title": "新热点A", "Url": "https://www.zhihu.com/question/a"},
                                {"Title": "新热点B", "Url": "https://www.zhihu.com/question/b"}]
    app.fetch_search = lambda title, rank: [{"Title": "候选1", "Url": "https://www.zhihu.com/question/1/answer/9"}]
    app.generate = lambda title, text: {
        "background": f"{title}的新解读",
        "viewpoints": {"support": "支持", "oppose": "反对", "undecided": "未定论"},
        "reading": [{"index": 1, "reason": "视角独特"}],
    }


def write_old_cache():
    path = app.cache_path()
    path.write_text(json.dumps({
        "date": app.today_str(), "source": "live", "generated_at": "旧",
        "notice": "", "items": [{"rank": 1, "title": "旧热点", "question_url": "",
                                 "background": "旧内容", "viewpoints": {}, "reading": []}],
    }, ensure_ascii=False), encoding="utf-8")
    old = time.time() - 4 * 3600  # 4 小时前，超过 TTL
    os.utime(path, (old, old))
    return path


patch_upstream()

# ---------- 场景A：过期缓存 ----------
path = write_old_cache()
data = app.get_brief()
assert data["items"][0]["title"] == "旧热点", "场景A：本次请求应立即返回旧缓存"
print("场景A-1 ✅ 过期缓存秒回旧数据（访客零等待）")

deadline = time.time() + 15
while time.time() < deadline and "旧热点" in path.read_text(encoding="utf-8"):
    time.sleep(0.2)
new_cache = json.loads(path.read_text(encoding="utf-8"))
assert "旧热点" not in json.dumps(new_cache, ensure_ascii=False), "场景A：后台应刷新出新数据"
assert new_cache["items"][0]["background"] == "新热点A的新解读", "场景A：内容应为新解读"
assert new_cache["items"][0]["reading"], "场景A：延伸阅读应生成"
print("场景A-2 ✅ 后台线程静默刷新，缓存已换新")

# ---------- 场景B：新鲜缓存不触发刷新 ----------
content_before = path.read_text(encoding="utf-8")
mtime_before = path.stat().st_mtime
app.get_brief()
time.sleep(1.0)
assert path.stat().st_mtime == mtime_before, "场景B：新鲜缓存不应触发刷新"
assert path.read_text(encoding="utf-8") == content_before, "场景B：缓存内容不应变化"
print("场景B ✅ 新鲜缓存不触发刷新")

print("全部通过")
