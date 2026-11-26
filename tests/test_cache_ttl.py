# -*- coding: utf-8 -*-
"""验证缓存过期自动刷新（stale-while-revalidate）：
场景A 过期缓存秒回旧数据 + 后台刷新成新数据；场景B 新鲜缓存不触发刷新。

运行：python tests/test_cache_ttl.py（在仓库根目录下）
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402


class TestCacheTTL(AppStateTestCase):
    def test_stale_while_revalidate_and_fresh_cache(self):
        app.DATA_DIR = self.tmp_path
        app.MOCK_MODE = False
        app.CACHE_TTL_SECONDS = 3 * 3600  # TTL 3 小时

        app.api_hot_list = lambda: [
            {"Title": "新热点A", "Url": "https://www.zhihu.com/question/a"},
            {"Title": "新热点B", "Url": "https://www.zhihu.com/question/b"},
        ]
        app.fetch_search = lambda title, rank: [
            {"Title": "候选1", "Url": "https://www.zhihu.com/question/1/answer/9"}
        ]
        app.generate = lambda title, text: {
            "background": f"{title}的新解读",
            "viewpoints": {"support": "支持", "oppose": "反对", "undecided": "未定论"},
            "reading": [{"index": 1, "reason": "视角独特"}],
        }

        path = app.cache_path()
        path.write_text(json.dumps({
            "date": app.today_str(), "source": "live", "generated_at": "旧",
            "notice": "", "items": [{"rank": 1, "title": "旧热点", "question_url": "",
                                     "background": "旧内容", "viewpoints": {}, "reading": []}],
        }, ensure_ascii=False), encoding="utf-8")
        old = time.time() - 4 * 3600  # 4 小时前，超过 TTL
        os.utime(path, (old, old))

        # ---------- 场景A：过期缓存 ----------
        data = app.get_brief()
        self.assertEqual(data["items"][0]["title"], "旧热点", "场景A：本次请求应立即返回旧缓存")

        deadline = time.time() + 15
        while time.time() < deadline and "旧热点" in path.read_text(encoding="utf-8"):
            time.sleep(0.1)
        new_cache = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("旧热点", json.dumps(new_cache, ensure_ascii=False), "场景A：后台应刷新出新数据")
        self.assertEqual(new_cache["items"][0]["background"], "新热点A的新解读", "场景A：内容应为新解读")
        self.assertTrue(new_cache["items"][0]["reading"], "场景A：延伸阅读应生成")

        # ---------- 场景B：新鲜缓存不触发刷新 ----------
        content_before = path.read_text(encoding="utf-8")
        mtime_before = path.stat().st_mtime
        app.get_brief()
        time.sleep(0.5)
        self.assertEqual(path.stat().st_mtime, mtime_before, "场景B：新鲜缓存不应触发刷新")
        self.assertEqual(path.read_text(encoding="utf-8"), content_before, "场景B：缓存内容不应变化")


if __name__ == "__main__":
    unittest.main()
