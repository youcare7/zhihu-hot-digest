# -*- coding: utf-8 -*-
"""文本安全收束与质量验收的独立测试。

运行：python tests/test_text_quality.py（在仓库根目录下）
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app  # noqa: E402

VERIFY = ROOT / "scripts" / "verify_plan_numbers_v2.py"
SMOKE = ROOT / "scripts" / "smoke_check.py"
BASELINE = ROOT / "data" / "baseline_insight_v2.json"


def run_script(script, data, expected_code, expected_text):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "data.js"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [sys.executable, str(script), "--file", str(path)] if script == SMOKE
            else [sys.executable, str(script), str(path)],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
    output = result.stdout + result.stderr
    assert result.returncode == expected_code, (
        f"{script.name} exit={result.returncode}，期望 {expected_code}\n{output}"
    )
    assert expected_text in output, f"{script.name} 未输出 {expected_text!r}\n{output}"
    return output


class TestTextQuality(unittest.TestCase):
    def test_clip_boundaries(self):
        self.assertEqual(app._clip(" 短句。 ", 80), "短句。")
        long_sentence = "甲" * 60 + "。" + "乙" * 50 + "。"
        self.assertEqual(app._clip(long_sentence, 80), "甲" * 60 + "。")
        long_clause = "甲" * 60 + "；" + "乙" * 80
        self.assertEqual(app._clip(long_clause, 80), "甲" * 60 + "。")
        no_boundary = app._clip("甲" * 100, 80)
        self.assertEqual(len(no_boundary), 80)
        self.assertTrue(no_boundary.endswith("…"))
        self.assertEqual(app._clip("甲乙丙", 0), "")
        self.assertEqual(app._clip("甲乙", 1), "…")

    def test_verify_plan_numbers_baseline_garbled_and_truncated(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        run_script(VERIFY, baseline, 0, "全部硬性约束满足")

        garbled = copy.deepcopy(baseline)
        garbled["items"][0]["consensus"] = "人口数据出现\ufffd。"
        output = run_script(VERIFY, garbled, 1, "文本含 U+FFFD 乱码")
        self.assertIn("乱码位置", output)

        truncated = copy.deepcopy(baseline)
        truncated["items"][0]["consensus"] = "甲" * 80
        output = run_script(VERIFY, truncated, 1, "疑似硬截断")
        self.assertNotIn("sides.claim", output.split("疑似硬截断：", 1)[-1])

    def test_smoke_check_passes_and_blocks_degraded(self):
        def smoke_data(background="正常背景。"):
            items = []
            for rank in range(1, 11):
                items.append({
                    "rank": rank,
                    "title": f"热点 {rank}",
                    "question_url": f"https://www.zhihu.com/question/{rank}",
                    "background": background if rank == 10 else "正常背景。",
                    "focus": "聚焦问题。",
                    "gap": "可核验材料。",
                    "type": "verify",
                    "sides": [],
                    "reading": [],
                })
            return {
                "date": "2026-11-17",
                "source": "cache",
                "digest": {
                    "headline": "真分歧 0 条、已形成共识 0 条、待验证 10 条。",
                },
                "items": items,
            }

        run_script(SMOKE, smoke_data(), 0, "结果：PASS")
        output = run_script(SMOKE, smoke_data("暂无解读。"), 1, "background 为降级哨兵")
        self.assertIn("结果：FAIL", output)


if __name__ == "__main__":
    unittest.main()
