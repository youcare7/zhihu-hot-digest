# -*- coding: utf-8 -*-
"""验证收尾补跑机制：场景A 首败后补跑成功；场景B 一直失败保持降级。

运行：python tests/test_retry_pass.py（在仓库根目录下）
"""
import os
import sys
import types

os.environ.pop("MOCK_MODE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

# 加速测试：跳过补跑前的退避等待
app.time = types.SimpleNamespace(sleep=lambda s: None, strftime=__import__("time").strftime)

CANDIDATES = [{"Title": "候选回答1", "Url": "https://www.zhihu.com/question/1/answer/9"}]


def make_hot(n):
    return [{"Title": f"热点{i}", "Url": f"https://www.zhihu.com/question/{i}"} for i in range(1, n + 1)]


def run_case(name, fail_forever):
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
    print(f"[{name}] 第1条调用次数={calls['热点1']}")
    for it in brief["items"]:
        print("   rank", it["rank"], "|", it["background"][:24], "| reading:", len(it["reading"]))
    if fail_forever:
        assert bgs[0] == "暂无解读。", "场景B：应仍降级"
        assert bgs[1] != "暂无解读。", "场景B：其他条应正常"
        print("   ✅ 场景B通过：持续失败保持降级，不影响其他条\n")
    else:
        assert bgs[0] == "热点1的背景解读", "场景A：补跑应成功"
        assert brief["items"][0]["reading"], "场景A：补跑后应有延伸阅读"
        print("   ✅ 场景A通过：首轮降级 → 收尾补跑成功\n")


run_case("场景A：第1条首轮失败、补跑成功", fail_forever=False)
run_case("场景B：第1条持续失败", fail_forever=True)
print("全部通过")
