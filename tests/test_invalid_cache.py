# -*- coding: utf-8 -*-
"""坏缓存必须视为未命中，且新鲜坏缓存也不能阻止后台恢复。

运行：python tests/test_invalid_cache.py（在仓库根目录下）
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


class TestInvalidCache(AppStateTestCase):
    def test_invalid_caches_recover_and_mock_mode_reads_mock_cache(self):
        app.DATA_DIR = self.tmp_path
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

        mock_cache = {
            "date": app.today_str(),
            "source": "demo",
            "generated_at": app.now_str() + "（模拟）",
            "notice": app.NOTICE_MOCK,
            "items": [{
                "rank": 1,
                "title": "模拟热点",
                "question_url": "https://www.zhihu.com/question/mock-1",
                "type": app.TYPE_VERIFY,
                "claim_type": "",
                "background": "模拟背景",
                "focus": "模拟聚焦",
                "consensus": "模拟共识",
                "sides": [],
                "gap": "模拟缺口",
                "reading": [{
                    "title": "模拟延伸",
                    "url": "https://www.zhihu.com/question/mock-1/answer/mock-100",
                    "reason": "模拟推荐",
                }],
            }],
        }
        mock_cache["digest"] = app.build_digest(mock_cache["items"])

        cases = [
            ("空 items 缓存", {"date": app.today_str(), "source": "live", "items": []}),
            ("非对象条目缓存", {"date": app.today_str(), "source": "live", "items": [None]}),
            ("真实模式中的模拟缓存", mock_cache),
        ]
        for label, payload in cases:
            while app._REFRESH_LOCK.locked():
                time.sleep(0.02)
            app._REFRESH_NOT_BEFORE = 0.0
            with app._STATE_LOCK:
                app._BRIEF_CACHE_KEY = None
                app._BRIEF_CACHE_DATA = None
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            response = app.get_brief()
            wait_for_recovery(label, response)

        while app._REFRESH_LOCK.locked():
            time.sleep(0.02)
        app._REFRESH_NOT_BEFORE = 0.0
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = None
            app._BRIEF_CACHE_DATA = None
        path.write_text("{broken json", encoding="utf-8")
        response = app.get_brief()
        wait_for_recovery("损坏 JSON 缓存", response)

        self.assertEqual(calls["count"], 4, f"四种坏缓存都应各恢复一次，实际 {calls['count']} 次")

        while app._REFRESH_LOCK.locked():
            time.sleep(0.02)
        app.MOCK_MODE = True
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = None
            app._BRIEF_CACHE_DATA = None
        path.write_text(json.dumps(mock_cache, ensure_ascii=False), encoding="utf-8")
        mock_response = app.get_brief()
        self.assertEqual(mock_response.get("source"), "cache")
        self.assertIn("模拟数据", mock_response.get("notice", ""))
        self.assertEqual(calls["count"], 4, "mock 模式读取模拟缓存不应重新生成")


if __name__ == "__main__":
    unittest.main()
