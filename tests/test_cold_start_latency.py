# -*- coding: utf-8 -*-
"""冷启动耗时回归（2026-11-20）。

运行：python tests/test_cold_start_latency.py（在仓库根目录下）
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


class TestColdStartLatency(AppStateTestCase):
    def test_backoff_and_budget(self):
        vt = {"now": 1000.0}
        clock = {"s": 0.0, "seq": []}
        logs = []

        def fake_monotonic():
            return vt["now"]

        def fake_sleep(sec):
            clock["s"] += sec
            clock["seq"].append(sec)
            vt["now"] += sec

        def reset_clock():
            vt["now"] = 1000.0
            clock["s"] = 0.0
            clock["seq"] = []
            logs.clear()

        _orig_sleep, _orig_monotonic = time.sleep, time.monotonic
        time.sleep = fake_sleep
        time.monotonic = fake_monotonic
        try:
            app._log = lambda msg: logs.append(str(msg))
            app.MOCK_MODE = False
            app.QUOTA_GUARD_DISABLED = False
            app._QUOTA = app.QuotaGuard(self.tmp_path / "q.json")
            app.QUOTA_LIMITS.update({"hot_list": 10000, "search": 10000, "zhida": 10000})
            app.RETRY_WAIT, app.RETRY_TIMES, app.RETRY_BACKOFF_BASE = 30, 6, 2
            app.GEN_BUDGET_SECONDS = 0

            hot = [{"Title": f"热点{i}", "Url": f"https://www.zhihu.com/question/{i}"}
                   for i in range(1, 11)]

            def zhihu_get(path, params=None):
                if path.endswith("hot_list"):
                    return {"Items": hot}
                return {"Items": [{"Title": "候选", "Url": "https://www.zhihu.com/question/1/answer/1",
                                   "ContentText": "摘要"}]}

            app.zhihu_get = zhihu_get
            ok_json = json.dumps({"type": "consensus", "background": "正常背景", "focus": "焦点",
                                  "consensus": "结论", "gap": "缺口", "reading": []}, ensure_ascii=False)
            # 富化 pass（锐评/导读等组件）与主生成共用 _chat 桩：
            # 按 prompt 区分返回主生成 JSON 或组件 JSON，一次调用即补齐全部组件。
            enrich_json = json.dumps({
                "punch": "锐评",
                "lou": {"angle": "角度", "tips": ["一", "二", "三"],
                        "personas": {"考研党": "a", "上班族": "b", "业内人士": "c"}},
            }, ensure_ascii=False)

            flaky_n = {"n": 0}

            def flaky_chat(url, headers, payload, label):
                flaky_n["n"] += 1
                if flaky_n["n"] % 2 == 1:
                    raise app.UpstreamError("429 限流")
                prompt = payload.get("messages", [{}])[0].get("content", "")
                return enrich_json if "互动文案编辑" in prompt else ok_json

            def always_429(url, headers, payload, label):
                raise app.UpstreamError("429 限流")

            # 场景1：每条 1 次 429（主生成 + 富化各一次），整批等待从 300 秒降到 40 秒
            reset_clock()
            flaky_n["n"] = 0
            app._chat = flaky_chat
            brief = app.build_brief()
            ok = sum(1 for it in brief["items"] if it["background"] != app.NO_BACKGROUND)
            self.assertEqual(ok, 10)
            self.assertEqual(clock["s"], 40)
            self.assertEqual(clock["seq"], [2] * 20)

            # 场景2：有界指数退避序列与上限
            reset_clock()
            app._chat = always_429
            with self.assertRaises(app.UpstreamError):
                app._chat_with_retry("https://x", {}, {}, "知乎直答")
            self.assertEqual(clock["seq"], [2, 4, 8, 16, 30])
            self.assertEqual(clock["s"], 60)

            # 场景3：RETRY_WAIT=0 时零等待
            reset_clock()
            app.RETRY_WAIT = 0
            with self.assertRaises(app.UpstreamError):
                app._chat_with_retry("https://x", {}, {}, "知乎直答")
            self.assertEqual(clock["s"], 0)
            self.assertEqual(clock["seq"], [])
            app.RETRY_WAIT = 30

            # 场景4：整批预算耗尽不杀线程、保留部分结果、跳过补跑
            reset_clock()
            app.GEN_BUDGET_SECONDS = 10
            app._chat = always_429
            brief = app.build_brief()
            self.assertEqual(len(brief["items"]), 10)
            self.assertLessEqual(clock["s"], 10)
            self.assertTrue(any("预算已耗尽" in m or "跳过补跑" in m for m in logs))
            self.assertEqual(app._GEN_DEADLINE, 0.0)

            # 场景5：预算=0 关闭开关时行为不变（含富化 pass 的 429 退避）
            reset_clock()
            app.GEN_BUDGET_SECONDS = 0
            flaky_n["n"] = 0
            app._chat = flaky_chat
            brief = app.build_brief()
            ok = sum(1 for it in brief["items"] if it["background"] != app.NO_BACKGROUND)
            self.assertEqual(ok, 10)
            self.assertEqual(clock["s"], 40)
            self.assertEqual(app._GEN_DEADLINE, 0.0)

            # 场景6：补跑不再无条件 sleep(30)
            reset_clock()
            app.GEN_BUDGET_SECONDS = 0
            app.RETRY_TIMES = 1
            app._chat = always_429
            brief = app.build_brief()
            self.assertTrue(any("收尾补跑" in m for m in logs))
            self.assertEqual(clock["s"], 0)
        finally:
            time.sleep, time.monotonic = _orig_sleep, _orig_monotonic


if __name__ == "__main__":
    unittest.main()
