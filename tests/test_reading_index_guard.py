# -*- coding: utf-8 -*-
"""回归：延伸阅读的候选编号必须严格是整数，布尔值不得当作编号。

背景：assemble_reading 用 isinstance(idx, int) 校验模型输出的编号，
但 Python 中 bool 是 int 的子类，True 会被当成 1 放行——模型没有给出
合法编号时，程序却静默替它选中了第一条候选，违背「编号由程序翻译、
非整数直接丢弃、不拿别的顶上」的链接守卫契约。

运行：python tests/test_reading_index_guard.py（在仓库根目录下）
"""
import os
import pathlib
import sys

os.environ.pop("MOCK_MODE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

CANDIDATES = [
    {"Title": "候选A", "Url": "https://www.zhihu.com/question/1/answer/1"},
    {"Title": "候选B", "Url": "https://zhihu.com/question/2/answer/2"},
    {"Title": "候选C", "Url": "https://www.zhihu.com/question/3/answer/3"},
]

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))
        failures.append(name)


def reading_with(picks):
    return app.assemble_reading({"reading": picks}, CANDIDATES)


print("场景1：布尔编号必须丢弃（回归核心）")
for bad in (True, False):
    got = reading_with([{"index": bad, "reason": "x"}])
    check(
        f"index={bad!r} 不产生延伸阅读",
        got == [],
        f"实际返回 {len(got)} 条：{got}",
    )

print("场景2：其它非整数编号必须丢弃")
for bad in (1.0, "1", None, 0, -1, 99, [], {}):
    got = reading_with([{"index": bad, "reason": "x"}])
    check(f"index={bad!r} 不产生延伸阅读", got == [], f"实际返回 {len(got)} 条")

print("场景3：合法整数编号仍正常映射")
got = reading_with([{"index": 2, "reason": "看看另一派"}])
check(
    "index=2 精确映射到候选池第 2 条",
    len(got) == 1 and got[0]["url"] == CANDIDATES[1]["Url"],
    f"实际：{got}",
)

print("场景4：混合输入只保留合法项，且 bool 不占位")
got = reading_with([
    {"index": True, "reason": "布尔"},
    {"index": 1, "reason": "合法"},
])
check(
    "bool 被丢弃、合法 int 保留，最终仅 1 条",
    len(got) == 1 and got[0]["url"] == CANDIDATES[0]["Url"],
    f"实际：{got}",
)

print("场景5：既有守卫不被破坏（上限 2 / 去重 / 域名）")
got = reading_with([
    {"index": 1, "reason": "a"},
    {"index": 1, "reason": "b"},
    {"index": 2, "reason": "c"},
    {"index": 3, "reason": "d"},
])
check("每条最多 2 条且条内去重", len(got) == 2, f"实际 {len(got)} 条：{got}")
check(
    "全部来自真实候选池且为知乎域名",
    all(r["url"] in {c["Url"] for c in CANDIDATES} and app._is_zhihu_url(r["url"]) for r in got),
)

if failures:
    print(f"\n结果：FAIL（{len(failures)} 项）")
    sys.exit(1)
print("\n结果：PASS")
sys.exit(0)
