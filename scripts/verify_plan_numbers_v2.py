# -*- coding: utf-8 -*-
"""计划书数字复核脚本 · v2 六字段口径

用途
----
v2 把「支持 / 反对 / 未定论」三派改成了六字段结构
（type / claim_type / focus / consensus / sides / gap），
项目自带的旧脚本 scripts/verify_plan_numbers.py 按旧三派口径写死，
对 v2 产物会输出 57/57/57、18/30、以及恒为 True 的假阳性结论 —— 全部无效。

本脚本按 v2 真实数据结构重写，对 /data.js（或 data/brief_*.json）逐项复核
计划书里所有标注「实测」的数字。任何一项不合格即 exit 1。

用法
----
    python verify_plan_numbers_v2.py <data.js 或 brief_*.json>

退出码
------
    0  全部硬性约束满足
    1  存在不合格项
    2  文件读取或解析失败
"""

import json
import re
import sys
import urllib.parse

# ---- v2 字段硬上限（与 app.py 常量区一致）----
BACKGROUND_MAX = 150
FOCUS_MAX = 60
CONSENSUS_MAX = 80
SIDE_LABEL_MAX = 12
SIDE_CLAIM_MAX = 80
GAP_MAX = 100
REASON_MAX = 30
READING_MAX_PER_ITEM = 2

VALID_TYPES = ("dispute", "consensus", "verify")
VALID_CLAIM_TYPES = ("fact", "inference", "impression", "value")

PLACEHOLDERS = {
    "站内暂无明确该立场声音",
    "暂无解读。",
    "暂无聚焦问题。",
    "暂无可补的拼图。",
}

problems = []


def fail(msg):
    problems.append(msg)


def load(path):
    """同时支持 /data.js（window.BRIEF_DATA = {...};）与裸 JSON。"""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        print(f"[错误] 读不到文件：{exc}")
        sys.exit(2)

    m = re.search(r"window\.BRIEF_DATA\s*=\s*(\{.*\})\s*;?\s*$", raw, re.S)
    text = m.group(1) if m else raw
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"[错误] JSON 解析失败：{exc}")
        sys.exit(2)


def real(value):
    """剥离占位串后返回真实内容；没有真实内容则返回空串。"""
    s = str(value or "").strip()
    return "" if s in PLACEHOLDERS else s


def is_zhihu_url(url):
    """与 app.py 一致：仅允许知乎主域名及其子域名，不用子串匹配。"""
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme in ("http", "https")
        and (host == "zhihu.com" or host.endswith(".zhihu.com"))
    )


def section(title):
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)

    data = load(sys.argv[1])
    items = data.get("items") or []
    digest = data.get("digest") or {}

    print(f"数据文件：{sys.argv[1]}")
    print(f"date={data.get('date')}  source={data.get('source')}  items={len(items)}")

    if not items:
        print("[错误] items 为空")
        sys.exit(1)

    # ---------------------------------------------------------------- [1]
    section("[1] 生成成功率 / 降级条目")
    degraded = [
        it.get("rank")
        for it in items
        if not real(it.get("background")) or it.get("background") == "暂无解读。"
    ]
    print(f"    成功 {len(items) - len(degraded)}/{len(items)}    降级 {len(degraded)} 条")
    if degraded:
        print(f"    降级条目 rank：{degraded}")
        fail(f"存在 {len(degraded)} 条降级（rank={degraded}）")

    # ---------------------------------------------------------------- [2]
    section("[2] background 字数")
    bgs = [len(it.get("background") or "") for it in items]
    print(f"    min={min(bgs)}  max={max(bgs)}  avg={sum(bgs) / len(bgs):.1f}  上限={BACKGROUND_MAX}")
    over = [(it.get("rank"), len(it.get("background") or "")) for it in items
            if len(it.get("background") or "") > BACKGROUND_MAX]
    print(f"    超上限：{len(over)} 条" + (f"  {over}" if over else ""))
    if over:
        fail(f"background 超 {BACKGROUND_MAX} 字：{over}")

    # ---------------------------------------------------------------- [3]
    section("[3] 争议类型分布（v2 核心）")

    # 旧格式识别：整份数据都没有 type 字段 = fallback.json 或 v2 之前的旧缓存。
    # 这类数据本来就不该带 v2 字段，对它判 type 硬约束是误伤，改为跳过并说明。
    legacy_mode = not any(it.get("type") for it in items)
    if legacy_mode:
        print("    [跳过] 整份数据均无 type 字段 → 判定为旧格式（fallback.json / v2 之前的缓存）")
        print("    v2 字段（type / claim_type / sides / focus / consensus / gap）不适用于本文件，")
        print("    本次仅复核字数与链接类硬约束。计划书数字复核请对当日线上 /data.js 执行。")

    counts = {t: 0 for t in VALID_TYPES}
    unknown = []
    if not legacy_mode:
        for it in items:
            t = it.get("type")
            if t in counts:
                counts[t] += 1
            else:
                unknown.append((it.get("rank"), t))
        print(f"    真分歧 dispute={counts['dispute']}   已形成共识 consensus={counts['consensus']}   "
              f"待验证 verify={counts['verify']}   合计={sum(counts.values())}")
        if unknown:
            print(f"    非法 type：{unknown}")
            fail(f"存在非法 type：{unknown}")
        if sum(counts.values()) != len(items):
            fail("type 统计与实际条目数不符")

    # ---------------------------------------------------------------- [4]
    section("[4] 分歧性质 claim_type（仅 dispute 允许有值）")
    claim_counts = {c: 0 for c in VALID_CLAIM_TYPES}
    bad_claim = []
    for it in items:
        c = (it.get("claim_type") or "").strip()
        t = it.get("type")
        if t == "dispute":
            if c in claim_counts:
                claim_counts[c] += 1
            else:
                bad_claim.append((it.get("rank"), c or "(空)"))
        elif c:
            bad_claim.append((it.get("rank"), f"非 dispute 却带 claim_type={c}"))
    print("    " + "   ".join(f"{c}={claim_counts[c]}" for c in VALID_CLAIM_TYPES))
    print(f"    合计={sum(claim_counts.values())}（应等于 dispute 数 {counts['dispute']}）")
    if bad_claim:
        print(f"    非法：{bad_claim}")
        fail(f"claim_type 非法或错位：{bad_claim}")
    if sum(claim_counts.values()) != counts["dispute"]:
        fail("claim_type 合计与 dispute 数不符")

    # ---------------------------------------------------------------- [5]
    section("[5] 阵营完整性闸门（v2 最关键的一条）")
    bad_sides = []
    for it in items:
        sides = it.get("sides") or []
        t = it.get("type")
        if t == "dispute":
            if len(sides) != 2:
                bad_sides.append((it.get("rank"), f"dispute 但 sides={len(sides)}"))
                continue
            for s in sides:
                if not real(s.get("label")) or not real(s.get("claim")):
                    bad_sides.append((it.get("rank"), "存在空 label/claim"))
        elif sides:
            bad_sides.append((it.get("rank"), f"非 dispute 却带 {len(sides)} 个 sides"))
    print(f"    真分歧且恰好 2 个成型阵营：{counts['dispute'] - len([b for b in bad_sides])}/{counts['dispute']}")
    print(f"    非真分歧带阵营：{len([b for b in bad_sides if '非 dispute' in b[1]])} 条（必须为 0）")
    if bad_sides:
        print(f"    违规：{bad_sides}")
        fail(f"阵营完整性闸门被突破：{bad_sides}")

    # ---------------------------------------------------------------- [6]
    section("[6] 各字段字数上限")
    stats = {}
    for key, limit in (
        ("focus", FOCUS_MAX),
        ("consensus", CONSENSUS_MAX),
        ("gap", GAP_MAX),
    ):
        vals = [len(it.get(key) or "") for it in items if real(it.get(key))]
        stats[key] = (min(vals), max(vals), limit) if vals else (0, 0, limit)
        over = [(it.get("rank"), len(it.get(key) or "")) for it in items
                if len(it.get(key) or "") > limit]
        print(f"    {key:<10} min={stats[key][0]:<3} max={stats[key][1]:<3} 上限={limit:<4} "
              f"超限={len(over)}" + (f"  {over}" if over else ""))
        if over:
            fail(f"{key} 超 {limit} 字：{over}")

    label_over, claim_over, side_vals = [], [], []
    for it in items:
        for s in it.get("sides") or []:
            lb, cl = len(s.get("label") or ""), len(s.get("claim") or "")
            side_vals.append(cl)
            if lb > SIDE_LABEL_MAX:
                label_over.append((it.get("rank"), lb))
            if cl > SIDE_CLAIM_MAX:
                claim_over.append((it.get("rank"), cl))
    print(f"    sides.label 上限={SIDE_LABEL_MAX}  超限={len(label_over)}" + (f"  {label_over}" if label_over else ""))
    print(f"    sides.claim 上限={SIDE_CLAIM_MAX}  min={min(side_vals) if side_vals else 0} "
          f"max={max(side_vals) if side_vals else 0}  超限={len(claim_over)}" + (f"  {claim_over}" if claim_over else ""))
    if label_over:
        fail(f"sides.label 超 {SIDE_LABEL_MAX} 字：{label_over}")
    if claim_over:
        fail(f"sides.claim 超 {SIDE_CLAIM_MAX} 字：{claim_over}")

    # ---------------------------------------------------------------- [7]
    section("[7] 延伸阅读：条数 / 域名 / reason 字数")
    total_reading, per_item, non_zhihu, reason_over = 0, [], [], []
    for it in items:
        rd = it.get("reading") or []
        per_item.append(len(rd))
        total_reading += len(rd)
        if len(rd) > READING_MAX_PER_ITEM:
            fail(f"rank={it.get('rank')} 延伸阅读 {len(rd)} 条，超过 {READING_MAX_PER_ITEM}")
        for r in rd:
            url = r.get("url") or ""
            if not is_zhihu_url(url):
                non_zhihu.append((it.get("rank"), url[:60]))
            if len(r.get("reason") or "") > REASON_MAX:
                reason_over.append((it.get("rank"), len(r.get("reason") or "")))
    print(f"    总条数={total_reading}   每条分布={per_item}")
    print(f"    非知乎域名={len(non_zhihu)}" + (f"  {non_zhihu}" if non_zhihu else ""))
    print(f"    reason 超 {REASON_MAX} 字={len(reason_over)}" + (f"  {reason_over}" if reason_over else ""))
    if non_zhihu:
        fail(f"出现非知乎域名：{non_zhihu}")
    if reason_over:
        fail(f"reason 超 {REASON_MAX} 字：{reason_over}")

    # ---------------------------------------------------------------- [8]
    section("[8] 字数拆分（计划书 §2.2 引用）")
    def total_len(key):
        return sum(len(it.get(key) or "") for it in items)

    title_n = total_len("title")
    bg_n = total_len("background")
    focus_n = total_len("focus")
    cons_n = total_len("consensus")
    gap_n = total_len("gap")
    side_n = sum(len(s.get("claim") or "") for it in items for s in (it.get("sides") or []))
    rd_title_n = sum(len(r.get("title") or "") for it in items for r in (it.get("reading") or []))
    rd_reason_n = sum(len(r.get("reason") or "") for it in items for r in (it.get("reading") or []))

    core = title_n + bg_n + focus_n + cons_n + gap_n + side_n
    print(f"    标题={title_n}  背景={bg_n}  聚焦={focus_n}  共识={cons_n}  缺口={gap_n}  两方主张={side_n}")
    print(f"    核心字数 = {title_n}+{bg_n}+{focus_n}+{cons_n}+{gap_n}+{side_n} = {core}")
    print(f"    延伸阅读 标题={rd_title_n}  推荐理由={rd_reason_n}")
    print(f"    全文字数 = {core}+{rd_title_n}+{rd_reason_n} = {core + rd_title_n + rd_reason_n}")

    # ---------------------------------------------------------------- [9]
    section("[9] digest 自洽性（今日洞察卡）")
    if not digest:
        print("    [警告] 数据里没有 digest 字段（旧格式或 fallback 兜底）")
        print("    → 兜底路径不带洞察卡属预期行为，不算失败")
    else:
        d_counts = {t: digest.get(t) for t in VALID_TYPES}
        print(f"    digest: total={digest.get('total')} dispute={digest.get('dispute')} "
              f"consensus={digest.get('consensus')} verify={digest.get('verify')}")
        print(f"    实际: total={len(items)} dispute={counts['dispute']} "
              f"consensus={counts['consensus']} verify={counts['verify']}")
        if digest.get("total") != len(items):
            fail("digest.total 与实际条目数不符")
        for t in VALID_TYPES:
            if d_counts[t] != counts[t]:
                fail(f"digest.{t}={d_counts[t]} 与实际 {counts[t]} 不符")
        d_claim = digest.get("claim_counts") or {}
        if {k: d_claim.get(k) for k in VALID_CLAIM_TYPES} != claim_counts:
            fail(f"digest.claim_counts={d_claim} 与实际 {claim_counts} 不符")
        print(f"    claim_counts 一致：{d_claim == claim_counts}")
        print(f"    headline：{digest.get('headline')}")
        for i, t in enumerate(digest.get("insights") or [], 1):
            print(f"    insight{i}：{t}")
        if not (digest.get("insights") or []):
            fail("digest.insights 为空")

    # ---------------------------------------------------------------- [10]
    section("[10] 旧三派字段兼容性（前端 else 分支依赖）")

    def legacy_vp(it):
        """线上产物用 viewpoints，离线归类快照用 _legacy_viewpoints，两者都算。"""
        for key in ("viewpoints", "_legacy_viewpoints"):
            vp = it.get(key)
            if isinstance(vp, dict) and all(k in vp for k in ("support", "oppose", "undecided")):
                return True
        return False

    with_vp = sum(1 for it in items if legacy_vp(it))
    print(f"    保留三派字段的条目：{with_vp}/{len(items)}")
    if with_vp != len(items):
        fail(f"有 {len(items) - with_vp} 条缺少 viewpoints 三键，旧缓存兼容分支会报错")

    # ---------------------------------------------------------------- 结论
    print()
    print("=" * 68)
    if problems:
        print(f"[不合格] 共 {len(problems)} 项：")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        print("=" * 68)
        sys.exit(1)

    print("[结论] 全部硬性约束满足，计划书数字可复核")
    print("=" * 68)
    sys.exit(0)


if __name__ == "__main__":
    main()
