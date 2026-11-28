# -*- coding: utf-8 -*-
"""验证收尾补跑机制：场景A 首败后补跑成功；场景B 一直失败保持降级。

运行：python tests/test_retry_pass.py（在仓库根目录下）
"""
import os
import sys
import time as _real_time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from tests._app_state import AppStateTestCase  # noqa: E402

CANDIDATES = [{"Title": "候选回答1", "Url": "https://www.zhihu.com/question/1/answer/9"}]


def make_hot(n):
    return [{"Title": f"热点{i}", "Url": f"https://www.zhihu.com/question/{i}"} for i in range(1, n + 1)]


class TestRetryPass(AppStateTestCase):
    def setUp(self):
        super().setUp()
        app.QUOTA_GUARD_DISABLED = False
        app.QUOTA_LIMITS["zhida"] = 80
        app._QUOTA = app.QuotaGuard(self.tmp_path / "quota.json")
        app.time = types.SimpleNamespace(
            sleep=lambda s: None,
            strftime=_real_time.strftime,
            monotonic=_real_time.monotonic,
            time=_real_time.time,
        )

    def _run_case(self, fail_forever):
        app.api_hot_list = lambda: make_hot(3)
        app.fetch_search = lambda title, rank: list(CANDIDATES)
        calls = {"热点1": 0}

        def fake_generate(title, text):
            if title == "热点1":
                calls["热点1"] += 1
                if fail_forever or calls["热点1"] == 1:
                    raise app.UpstreamError("429 限流（模拟）")
            return {
                "background": f"{title}的背景解读",
                "viewpoints": {"support": "支持", "oppose": "反对", "undecided": "未定论"},
                "reading": [{"index": 1, "reason": "视角独特"}],
            }

        app.generate = fake_generate
        brief = app.build_brief()
        bgs = [it["background"] for it in brief["items"]]
        if fail_forever:
            self.assertEqual(bgs[0], "暂无解读。")
            self.assertNotEqual(bgs[1], "暂无解读。")
        else:
            self.assertEqual(bgs[0], "热点1的背景解读")
            self.assertTrue(brief["items"][0]["reading"])

    def test_retry_pass_recovers_transient_failure(self):
        self._run_case(fail_forever=False)

    def test_retry_pass_keeps_degraded_on_permanent_failure(self):
        self._run_case(fail_forever=True)


if __name__ == "__main__":
    unittest.main()
