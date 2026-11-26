# -*- coding: utf-8 -*-
"""冷缓存可用性：首访不得阻塞等待整批生成，且并发只生成一次。

运行：python tests/test_cold_start.py（在仓库根目录下）
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


class TestColdStart(AppStateTestCase):
    def test_cold_start_nonblocking_singleflight_and_zero_success_cooldown(self):
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False

        started = threading.Event()
        release = threading.Event()
        calls = {"count": 0}
        calls_lock = threading.Lock()

        def make_brief():
            items = [{
                "rank": 1,
                "title": "新热点",
                "question_url": "https://www.zhihu.com/question/1",
                "type": app.TYPE_VERIFY,
                "claim_type": "",
                "background": "有效背景",
                "focus": "聚焦问题",
                "consensus": "",
                "sides": [],
                "gap": "可核验材料",
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

        def slow_build():
            with calls_lock:
                calls["count"] += 1
            started.set()
            if not release.wait(10):
                raise AssertionError("测试未及时释放模拟生成")
            return make_brief()

        app.build_brief = slow_build
        results = {}

        def request(name):
            results[name] = app.get_brief()

        first = threading.Thread(target=request, args=("first",))
        first.start()
        second = None
        try:
            self.assertTrue(started.wait(2), "冷缓存没有触发生成")
            first.join(0.5)
            self.assertFalse(first.is_alive(), "冷缓存首访仍被整批生成阻塞，可能触发网关 504")
            self.assertEqual(results["first"]["source"], "fallback", "无缓存时应立即返回明确标记的兜底")
            self.assertIs(results["first"].get("refreshing"), True, "兜底响应应告知前端后台正在生成")

            second = threading.Thread(target=request, args=("second",))
            second.start()
            second.join(0.5)
            self.assertFalse(second.is_alive(), "生成期间的后续访客也不应阻塞")
            self.assertEqual(results["second"]["source"], "fallback")
            self.assertEqual(calls["count"], 1, "并发冷启动不得重复触发整批生成")
        finally:
            release.set()
            first.join(12)
            if second is not None:
                second.join(12)

        path = app.cache_path()
        deadline = time.time() + 5
        while time.time() < deadline and not path.exists():
            time.sleep(0.05)
        self.assertTrue(path.exists(), "后台生成完成后应写入当日缓存")
        cached = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(cached["items"][0]["background"], "有效背景")
        self.assertEqual(calls["count"], 1, "等待生成期间不得出现额外生成")

        # 场景C：整批 0 条成功时不能落坏缓存，后续访问受冷却期约束。
        while app._REFRESH_LOCK.locked():
            time.sleep(0.02)
        path.unlink()
        app._REFRESH_NOT_BEFORE = 0.0
        zero_calls = {"count": 0}

        def zero_success_build():
            zero_calls["count"] += 1
            data = make_brief()
            data["items"][0]["background"] = app.NO_BACKGROUND
            return data

        app.build_brief = zero_success_build
        cold = app.get_brief()
        self.assertEqual(cold["source"], "fallback")
        deadline = time.time() + 3
        while time.time() < deadline and app._REFRESH_LOCK.locked():
            time.sleep(0.02)
        self.assertEqual(zero_calls["count"], 1)
        self.assertFalse(path.exists(), "0 条成功的整批结果不得覆盖静态兜底")
        retry = app.get_brief()
        self.assertEqual(retry["source"], "fallback")
        self.assertGreater(retry.get("retry_after", 0), 0)
        self.assertEqual(zero_calls["count"], 1, "冷却期内不得再次触发全量生成")


if __name__ == "__main__":
    unittest.main()
