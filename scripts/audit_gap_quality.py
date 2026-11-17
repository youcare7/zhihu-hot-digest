# -*- coding: utf-8 -*-
"""
B 方案试验 · gap 质量审计脚本（2026-11-17）

用途：对一份 data.js（线上 URL 或本地文件）逐条评估「缺的那块拼图」(gap) 的
"可执行性"——即是否写到了「具体材料 + 可去的出处」，而不是只描述缺什么类型的信息。

判定三分类（启发式，逐条打印命中依据，最终以人工复核为准）：
  - specific   可执行：命中具体材料词，且命中出处锚点或含日期/数字
  - honest_null 如实承认：明确写出「无法判定/无法核实」且给出时间性或
               「目前无公开可核验材料」（B 方案 PROMPT 的预期产物）
  - descriptive 描述性：其余——只说缺哪类信息，没说去哪找

用法：
  python scripts/audit_gap_quality.py                     # 审计当前线上 data.js
  python scripts/audit_gap_quality.py <本地 data.js 路径>   # 审计本地快照
  python scripts/audit_gap_quality.py <路径> --threshold 7  # 可执行+如实 >= 7/10 才 exit 0

退出码：未设 --threshold 时恒为 0（纯测量工具，不假装自己是验收门）；
设了 --threshold 时不达标 exit 1（这时它才有资格进验收链）。
"""
import json
import re
import sys
import urllib.request

LIVE_URL = "https://zhihu-hot-digest-348140-5-1533039364.sh.run.tcloudbase.com/data.js"

MATERIAL_RE = re.compile(
    "财报|年报|季报|半年报|流水|营收|利润|登记|备案|通报|声明|公告|判决|裁定|鉴定"
    "|检测|合同|协议|名单|记录|参数表|口径|基准|定妆|预告|片段|史料|文献|统计"
    "|单价|比例|数量|人数|对比|原始材料|一手材料|数据"
    "|图像|原片|回应|调查结论|说明|演示|实测|报告|标注|原文|论文|处置"
)
TITLE_RE = re.compile(r"《[^》]+》")  # 具名书刊/文件本身就是最强出处锚点
SOURCE_RE = re.compile(
    "官方|当事方|监管|法院|检察|公安|校方|学校|大学|医院|公司|集团|协会|学会"
    "|部门|机构|平台|片方|制片方|编辑部|出版社|基金|街道|教育部门|期刊|工作室"
    "|商户|中汽协|原著"
)
NUM_RE = re.compile(r"\d")
HONEST_RE = re.compile("无法判定|无法核实|难以核实|尚难判定")
TEMPORAL_RE = re.compile("开播前|上映前|尚未|目前|暂无|无公开|未公开|公开后")


def load(source: str) -> dict:
    if source.startswith("http"):
        raw = urllib.request.urlopen(source, timeout=45).read().decode("utf-8")
    else:
        raw = open(source, encoding="utf-8").read()
    raw = raw.strip()
    if raw.startswith("{") or raw.startswith("["):
        return json.loads(raw)  # 纯 JSON 快照（如 baseline_insight_v2.json）
    start = raw.index("=") + 1  # data.js 包装格式：window.BRIEF_DATA = {...};
    return json.loads(raw[start:].strip().rstrip(";"))


def classify(gap: str):
    """返回 (判定, 命中说明)。"""
    gap = (gap or "").strip()
    if not gap:
        return "descriptive", "gap 为空"
    if "目前无公开可核验材料" in gap:
        return "honest_null", "显式写出「目前无公开可核验材料」（B 方案预期产物）"
    if HONEST_RE.search(gap) and TEMPORAL_RE.search(gap):
        return "honest_null", "承认无法判定且给出时间性边界（如实型）"
    materials = MATERIAL_RE.findall(gap)
    sources = SOURCE_RE.findall(gap)
    titles = TITLE_RE.findall(gap)
    has_num = bool(NUM_RE.search(gap))
    if materials and (sources or titles or has_num):
        hits = "材料:" + "/".join(sorted(set(materials))[:4])
        if sources:
            hits += " 出处:" + "/".join(sorted(set(sources))[:3])
        if titles:
            hits += f" 具名:{titles[0]}"
        if has_num:
            hits += " 含数字锚点"
        return "specific", hits
    if materials:
        return "descriptive", "有材料词但无出处锚点/数字: " + "/".join(sorted(set(materials))[:4])
    return "descriptive", "无材料词命中"


VERDICT_NAME = {
    "specific": "可执行",
    "honest_null": "如实承认",
    "descriptive": "描述性 ",
}


def main():
    argv = sys.argv[1:]
    threshold = None
    positional = []
    i = 0
    while i < len(argv):
        if argv[i] == "--threshold":
            threshold = int(argv[i + 1])
            i += 2
        else:
            positional.append(argv[i])
            i += 1
    source = positional[0] if positional else LIVE_URL

    data = load(source)
    items = data.get("items", [])
    print(f"来源: {source}")
    print(f"快照: date={data.get('date')} generated_at={data.get('generated_at')} "
          f"source={data.get('source')} 共 {len(items)} 条\n")

    counts = {"specific": 0, "honest_null": 0, "descriptive": 0}
    for it in items:
        v, why = classify(it.get("gap"))
        counts[v] += 1
        print(f"#{it.get('rank')} [{it.get('type'):9s}] {VERDICT_NAME[v]} | {why}")
        print(f"   {(it.get('gap') or '').strip()[:90]}")

    ok = counts["specific"] + counts["honest_null"]
    print(f"\n汇总: 可执行 {counts['specific']} / 如实承认 {counts['honest_null']} "
          f"/ 描述性 {counts['descriptive']}  →  达标(可执行+如实) {ok}/{len(items)}")
    print("提醒: 本脚本是启发式辅助判定，逐条命中依据已打印，"
          "正式验收以人工复核为准；单日快照有波动，重要决策请多取几个时点。")

    if threshold is not None:
        if ok >= threshold:
            print(f"\n--threshold {threshold}: PASS")
            sys.exit(0)
        print(f"\n--threshold {threshold}: FAIL ({ok} < {threshold})")
        sys.exit(1)


if __name__ == "__main__":
    main()
