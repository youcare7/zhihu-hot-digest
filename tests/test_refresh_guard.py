# -*- coding: utf-8 -*-
"""验证后台 TTL 刷新的「防劣化保护」：
刷新结果的成功条目数若少于旧缓存，必须丢弃新结果、沿用旧缓存。

运行：python tests/test_refresh_guard.py（在仓库根目录下）
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

OLD_BG = "旧缓存的完整解读"
NEW_BG = "新缓存的新解读"


def make_item(rank, title, background):
    return {"rank": rank, "title": title, "question_url": "",
            "background": background, "viewpoints": {}, "reading": []}


class TestRefreshGuard(AppStateTestCase):
    def test_degraded_refresh_discarded_and_healthy_refresh_applied(self):
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False
        app.CACHE_TTL_SECONDS = 3 * 3600

        log_lines = []
        orig_log = app._log
        app._log = lambda msg: (log_lines.append(msg), orig_log(msg))

        def wait_refresh_done(timeout=15):
            deadline = time.time() + timeout
            while time.time() < deadline:
                for m in list(log_lines):
                    if (("后台自动刷新完成" in m or "丢弃本次结果" in m
                            or "后台自动刷新失败" in m)
                            and not app._REFRESH_LOCK.locked()):
                        return m
                time.sleep(0.05)
            raise AssertionError("等待超时：后台刷新线程未在预期时间内结束")

        old_data = {
            "date": app.today_str(), "source": "live", "generated_at": "旧",
            "notice": "", "items": [make_item(i, f"旧热点{i}", OLD_BG) for i in range(1, 4)],
        }
        path = app.cache_path()
        path.write_text(json.dumps(old_data, ensure_ascii=False), encoding="utf-8")
        old_time = time.time() - 4 * 3600
        os.utime(path, (old_time, old_time))
        content_before = path.read_text(encoding="utf-8")

        app.api_hot_list = lambda: [
            {"Title": f"新热点{i}", "Url": f"https://www.zhihu.com/question/{i}"}
            for i in (1, 2, 3)
        ]
        app.fetch_search = lambda title, rank: [
            {"Title": "候选", "Url": "https://www.zhihu.com/question/1/answer/9"}
        ]

        def always_fail_generate(title, text):
            if title == "新热点2":
                raise RuntimeError("429 限流（模拟额度触顶）")
            return {
                "background": NEW_BG,
                "viewpoints": {"support": "支持", "oppose": "反对", "undecided": "未定论"},
                "reading": [{"index": 1, "reason": "视角独特"}],
            }

        app.generate = always_fail_generate
        log_lines.clear()
        app.get_brief()
        end_msg = wait_refresh_done()

        self.assertIn("丢弃本次结果", end_msg)
        self.assertEqual(path.read_text(encoding="utf-8"), content_before)
        current = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(all(OLD_BG in it["background"] for it in current["items"]))

        # 场景2：冷却期允许结束后，新结果 3/3 不劣于旧缓存 → 应正常覆盖
        app._REFRESH_NOT_BEFORE = 0.0
        app.generate = lambda title, text: {
            "background": NEW_BG,
            "viewpoints": {"support": "支持", "oppose": "反对", "undecided": "未定论"},
            "reading": [{"index": 1, "reason": "视角独特"}],
        }
        log_lines.clear()
        app.get_brief()
        end_msg = wait_refresh_done()
        self.assertIn("后台自动刷新完成", end_msg)
        cur = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(all(NEW_BG in it["background"] for it in cur["items"]))


if __name__ == "__main__":
    unittest.main()
