# -*- coding: utf-8 -*-
"""验证当日缓存保留真实生成时刻，并兼容无有效时间戳的旧缓存。"""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


class TestCacheTimestamp(AppStateTestCase):
    def test_cache_preserves_generated_at_and_falls_back_to_mtime(self):
        app.DATA_DIR = self.tmp_path
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
        self.assertEqual(cached["source"], "cache")
        self.assertEqual(cached["generated_at"], "2026-11-17 19:59", "读取缓存时不得覆盖真实生成时刻")

        payload["generated_at"] = "当日缓存"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        mtime = datetime.datetime(2026, 11, 17, 19, 59, tzinfo=app.CN_TZ).timestamp()
        os.utime(path, (mtime, mtime))

        legacy = app.load_today_cache()
        self.assertEqual(legacy["source"], "cache")
        self.assertEqual(legacy["generated_at"], "2026-11-17 19:59", "旧缓存应使用文件修改时间补齐")


if __name__ == "__main__":
    unittest.main()
