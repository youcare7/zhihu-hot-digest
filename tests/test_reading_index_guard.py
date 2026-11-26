# -*- coding: utf-8 -*-
"""回归：延伸阅读的候选编号必须严格是整数，布尔值不得当作编号。

运行：python tests/test_reading_index_guard.py（在仓库根目录下）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

CANDIDATES = [
    {"Title": "候选A", "Url": "https://www.zhihu.com/question/1/answer/1"},
    {"Title": "候选B", "Url": "https://zhihu.com/question/2/answer/2"},
    {"Title": "候选C", "Url": "https://www.zhihu.com/question/3/answer/3"},
]


class TestReadingIndexGuard(unittest.TestCase):
    def _reading_with(self, picks):
        return app.assemble_reading({"reading": picks}, CANDIDATES)

    def test_bool_index_rejected(self):
        for bad in (True, False):
            self.assertEqual(self._reading_with([{"index": bad, "reason": "x"}]), [])

    def test_non_int_and_out_of_range_rejected(self):
        for bad in (1.0, "1", None, 0, -1, 99, [], {}):
            self.assertEqual(self._reading_with([{"index": bad, "reason": "x"}]), [])

    def test_valid_index_mapped_and_mixed_input(self):
        got = self._reading_with([{"index": 2, "reason": "看看另一派"}])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["url"], CANDIDATES[1]["Url"])

        got = self._reading_with([
            {"index": True, "reason": "布尔"},
            {"index": 1, "reason": "合法"},
        ])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["url"], CANDIDATES[0]["Url"])

    def test_limit_dedup_and_zhihu_domain(self):
        got = self._reading_with([
            {"index": 1, "reason": "a"},
            {"index": 1, "reason": "b"},
            {"index": 2, "reason": "c"},
            {"index": 3, "reason": "d"},
        ])
        self.assertEqual(len(got), 2)
        candidate_urls = {c["Url"] for c in CANDIDATES}
        self.assertTrue(all(r["url"] in candidate_urls and app._is_zhihu_url(r["url"]) for r in got))


if __name__ == "__main__":
    unittest.main()
