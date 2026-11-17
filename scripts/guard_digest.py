# -*- coding: utf-8 -*-
"""build_digest 三道脏数据守卫（DS 复审 P2 建议），锚点断言唯一后替换。"""
import io, sys

path = "app.py"
raw = io.open(path, encoding="utf-8").read()
crlf = "\r\n" in raw
text = raw.replace("\r\n", "\n")

GUARD = '''    # 防御性清洗：build_digest 也可能直接读到缓存直写/旧版残留的脏数据
    # （正常链路由 sanitize_type / sanitize_sides / sanitize_claim_type 保证，此为第二道防线）
    _cleaned = []
    for _it in items:
        _it = dict(_it)
        if _it.get("type") == TYPE_DISPUTE and len(_it.get("sides") or []) != 2:
            _it["type"] = TYPE_VERIFY
        if _it.get("type") != TYPE_DISPUTE:
            _it["claim_type"] = ""
        _cleaned.append(_it)
    items = _cleaned

'''

OLD_RETURN = '''    return {
        "total": total,
        "dispute": dispute,
        "consensus": consensus,
        "verify": verify,
        "claim_counts": claim_counts,
        "headline": headline,
        "insights": insights[:3],
    }'''

NEW_RETURN = '''    insights = insights[:3]
    if consensus and not any("已形成共识" in s for s in insights):
        _consensus_line = (
            f"{consensus} 条已形成共识——它们在热榜上的热度来自关注度，不来自争议。"
        )
        if insights:
            insights[-1] = _consensus_line
        else:
            insights = [_consensus_line]

    return {
        "total": total,
        "dispute": dispute,
        "consensus": consensus,
        "verify": verify,
        "claim_counts": claim_counts,
        "headline": headline,
        "insights": insights,
    }'''

edits = [
    ("    total = len(items)\n    dispute = sum(1 for it in items if it.get(\"type\") == TYPE_DISPUTE)",
     GUARD + "    total = len(items)\n    dispute = sum(1 for it in items if it.get(\"type\") == TYPE_DISPUTE)"),
    ("        soft = dispute - decidable",
     "        soft = max(0, dispute - decidable)"),
    (OLD_RETURN, NEW_RETURN),
]

for old, new in edits:
    n = text.count(old)
    if n != 1:
        print(f"FAIL 锚点出现 {n} 次（预期 1）：{old[:50]!r}")
        sys.exit(1)
    text = text.replace(old, new, 1)
    print(f"OK 锚点唯一：{old[:40]!r} ...")

out = text.replace("\n", "\r\n") if crlf else text
compile(out, path, "exec")
io.open(path, "w", encoding="utf-8", newline="").write(out)
print("已写入，语法 OK，字符数", len(out))
