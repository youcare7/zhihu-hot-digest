# -*- coding: utf-8 -*-
"""刷新失败/劣化后必须冷却，避免访客流量形成整批生成风暴。

运行：python tests/test_refresh_cooldown.py（在仓库根目录下）
"""
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


class TestRefreshCooldown(AppStateTestCase):
    def test_failure_and_degraded_refresh_trigger_cooldown(self):
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False
        app.CACHE_TTL_SECONDS = 3600
        app.REFRESH_COOLDOWN_SECONDS = 60

        old_item = {
            "rank": 1,
            "title": "旧热点",
            "question_url": "https://www.zhihu.com/question/old",
            "type": app.TYPE_VERIFY,
            "claim_type": "",
            "background": "旧缓存的完整解读",
            "focus": "旧聚焦",
            "consensus": "",
            "sides": [],
            "gap": "旧缺口",
            "reading": [],
        }
        old_second = dict(old_item, rank=2, title="旧热点2", question_url="https://www.zhihu.com/question/old-2")
        path = app.cache_path()
        path.write_text(json.dumps({
            "date": app.today_str(),
            "source": "live",
            "generated_at": app.now_str(),
            "notice": "",
            "items": [old_item, old_second],
        }, ensure_ascii=False), encoding="utf-8")
        old = time.time() - 2 * 3600
        os.utime(path, (old, old))
        content_before = path.read_text(encoding="utf-8")

        attempts = {"failure": 0, "degraded": 0}
        attempts_lock = threading.Lock()

        def failing_build():
            with attempts_lock:
                attempts["failure"] += 1
            raise app.UpstreamError("模拟上游持续故障")

        app.build_brief = failing_build
        self.assertEqual(app.get_brief()["items"][0]["title"], "旧热点")
        deadline = time.time() + 3
        while time.time() < deadline and app._REFRESH_LOCK.locked():
            time.sleep(0.02)
        self.assertEqual(attempts["failure"], 1)

        self.assertEqual(app.get_brief()["items"][0]["title"], "旧热点")
        time.sleep(0.3)
        self.assertEqual(attempts["failure"], 1, "上游失败后没有冷却，后续访客重复触发整批生成")
        self.assertEqual(path.read_text(encoding="utf-8"), content_before)

        def degraded_build():
            with attempts_lock:
                attempts["degraded"] += 1
            good = dict(old_item)
            good.update({"title": "新热点1", "background": "新有效背景", "reading": []})
            bad = dict(old_second)
            bad.update({"title": "新热点2", "background": app.NO_BACKGROUND, "reading": []})
            return {
                "date": app.today_str(),
                "source": "live",
                "generated_at": app.now_str(),
                "notice": "",
                "items": [good, bad],
            }

        app._REFRESH_NOT_BEFORE = 0.0
        app.build_brief = degraded_build
        app.get_brief()
        deadline = time.time() + 3
        while time.time() < deadline and app._REFRESH_LOCK.locked():
            time.sleep(0.02)
        self.assertEqual(attempts["degraded"], 1)
        app.get_brief()
        time.sleep(0.3)
        self.assertEqual(attempts["degraded"], 1, "劣化结果被丢弃后不应立即再次整批生成")
        self.assertEqual(path.read_text(encoding="utf-8"), content_before)


if __name__ == "__main__":
    unittest.main()
