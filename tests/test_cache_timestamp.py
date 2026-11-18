# -*- coding: utf-8 -*-
"""验证当日缓存保留真实生成时刻，并兼容无有效时间戳的旧缓存。"""
import datetime
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

app.DATA_DIR = pathlib.Path(tempfile.mkdtemp())
path = app.cache_path()
payload = {
    "date": app.today_str(),
    "source": "live",
    "generated_at": "2026-11-17 19:59",
    "notice": "",
    # 时间戳测试使用一条最小有效缓存；空 items 已由 test_invalid_cache.py 单独覆盖。
    "items": [{"rank": 1, "title": "缓存热点", "background": "有效背景",
               "viewpoints": {}, "reading": []}],
}
path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

cached = app.load_today_cache()
assert cached["source"] == "cache"
assert cached["generated_at"] == "2026-11-17 19:59", "读取缓存时不得覆盖真实生成时刻"
print("场景A ✅ 当日缓存保留真实生成时刻与来源状态")

payload["generated_at"] = "当日缓存"
path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
mtime = datetime.datetime(2026, 11, 17, 19, 59, tzinfo=app.CN_TZ).timestamp()
os.utime(path, (mtime, mtime))

legacy = app.load_today_cache()
assert legacy["source"] == "cache"
assert legacy["generated_at"] == "2026-11-17 19:59", "旧缓存应使用文件修改时间补齐"
print("场景B ✅ 旧缓存按文件修改时间补齐真实时刻")
