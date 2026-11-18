# -*- coding: utf-8 -*-
"""坏缓存必须视为未命中，且新鲜坏缓存也不能阻止后台恢复。

运行：python tests/test_invalid_cache.py（在仓库根目录下）
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
app.MOCK_MODE = False
app.CACHE_TTL_SECONDS = 3600
app.REFRESH_COOLDOWN_SECONDS = 1

calls = {"count": 0}


def recovered_brief():
    calls["count"] += 1
    items = [{
        "rank": 1,
        "title": "已恢复热点",
        "question_url": "https://www.zhihu.com/question/recovered",
        "type": app.TYPE_VERIFY,
        "claim_type": "",
        "background": "恢复后的有效背景",
        "focus": "恢复后的聚焦问题",
        "consensus": "",
        "sides": [],
        "gap": "恢复后的可核验材料",
        "reading": [],
    }]
    return {
        "date": app.today_str(),
        "source": "live",
        "generated_at": app.now_str(),
        "notice": app.NOTICE_LIVE,
        "digest": app.build_digest(items),
        "items": items,
    }


app.build_brief = recovered_brief
path = app.cache_path()


def wait_for_recovery(label, response):
    deadline = time.time() + 2
    while time.time() < deadline:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("items")
            if (isinstance(items, list) and items
                    and all(isinstance(it, dict) for it in items)
                    and items[0].get("title") == "已恢复热点"):
                while app._REFRESH_LOCK.locked():
                    time.sleep(0.02)
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError(
        f"{label} 未自动恢复：首响 source={response.get('source')}，"
        f"items={response.get('items')}，生成调用={calls['count']}"
    )


cases = [
    ("空 items 缓存", {"date": app.today_str(), "source": "live", "items": []}),
    ("非对象条目缓存", {"date": app.today_str(), "source": "live", "items": [None]}),
]
failures = []
for label, payload in cases:
    while app._REFRESH_LOCK.locked():
        time.sleep(0.02)
    app._REFRESH_NOT_BEFORE = 0.0
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    response = app.get_brief()  # 新文件 mtime 在 TTL 内，旧逻辑会误认为可直接服务
    try:
        wait_for_recovery(label, response)
        print(f"场景 ✅ {label}：视为未命中并由后台写入有效缓存")
    except AssertionError as exc:
        failures.append(str(exc))

# 损坏 JSON 已经会被 load_today_cache 忽略，但旧后台双重检查仅看 mtime，
# 会因“缓存很新鲜”直接退出，导致每次访问都重复启动一个立即退出的线程。
while app._REFRESH_LOCK.locked():
    time.sleep(0.02)
app._REFRESH_NOT_BEFORE = 0.0
path.write_text("{broken json", encoding="utf-8")
response = app.get_brief()
try:
    wait_for_recovery("损坏 JSON 缓存", response)
    print("场景 ✅ 损坏 JSON 缓存：新鲜 mtime 不再阻止后台恢复")
except AssertionError as exc:
    failures.append(str(exc))

if failures:
    raise AssertionError("\n".join(failures))
assert calls["count"] == 3, f"三种坏缓存都应各恢复一次，实际 {calls['count']} 次"
print("全部通过")
