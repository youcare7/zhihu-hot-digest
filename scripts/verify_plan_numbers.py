# -*- coding: utf-8 -*-
"""本地无依赖核对脚本：直接解析线上缓存 / fallback.json，算计划书里的可复核指标。

⚠️ 【已废弃 · 请勿用于 v2 数据】
   本脚本按旧的「支持 / 反对 / 未定论三派」口径写死。v2 六字段结构
   （type / claim_type / focus / consensus / sides / gap）合入后，它对同一份
   数据会输出「背景 57/57/57」「空立场 18/30」「三派全非空 True」这类
   **数字合理但结论错误**的结果，比直接报错更危险。

   请改用同目录的 verify_plan_numbers_v2.py：
       python scripts/verify_plan_numbers_v2.py <data.js 或 brief_<日期>.json>
   它按六字段口径做 10 组硬约束检查，任何一项不合格即 exit 1，可挂 CI。

   本文件仅作为历史留档保留（附录 B 有完整的问题说明）。

不需要 flask、不需要联网。用法（旧）：
    python verify_plan_numbers.py <线上 data.js 或 data.json 路径> [...]

也可以直接从线上 /data.js 的 JSON 部分（去掉 `window.BRIEF_DATA = ` 前缀和末尾分号）读取。
"""
import json
import pathlib
import re
import sys

EMPTY_VP = "站内暂无明确该立场声音"
NO_BG = "暂无解读。"


def load(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    text = re.sub(r"^window\.BRIEF_DATA\s*=\s*", "", text)
    text = re.sub(r";\s*$", "", text)
    return json.loads(text)


def analyze(name: str, data: dict) -> None:
    items = data.get("items") or []
    print(f"\n=== {name} ===  source={data.get('source')} date={data.get('date')} items={len(items)}")

    interpreted = [it for it in items if (it.get("background") or "").strip() != NO_BG]
    degraded = [it for it in items if (it.get("background") or "").strip() == NO_BG]
    print(f"有背景解读: {len(interpreted)}/{len(items)}    降级(暂无解读): {len(degraded)}")

    bg_lens = [len((it.get("background") or "").strip()) for it in interpreted]
    if bg_lens:
        print(f"背景字数 min/max/avg: {min(bg_lens)}/{max(bg_lens)}/{sum(bg_lens)/len(bg_lens):.1f}  (约束 ≤150)")

    total_chars = 0
    reading_total = 0
    empty_vp = 0
    non_zhihu = 0
    for it in items:
        total_chars += len(it.get("title") or "")
        total_chars += len((it.get("background") or "").strip())
        vp = it.get("viewpoints") or {}
        for k in ("support", "oppose", "undecided"):
            v = (vp.get(k) or "").strip()
            total_chars += len(v)
            if v == EMPTY_VP:
                empty_vp += 1
        reading = it.get("reading") or []
        reading_total += len(reading)
        for r in reading:
            host = (r.get("url") or "").split("/")[2] if "//" in (r.get("url") or "") else ""
            if not host.endswith("zhihu.com"):
                non_zhihu += 1

    print(f"延伸阅读总数: {reading_total}   每条分布: {[len(it.get('reading') or []) for it in items]}")
    print(f"非知乎域名链接: {non_zhihu}")
    print(f"空立场文案出现次数: {empty_vp}/{len(items)*3}")
    print(f"全文字数(标题+背景+三派, 实测口径): {total_chars}")

    prompts_all = all(
        (it.get("background") or "").strip() != NO_BG
        and all((it.get("viewpoints") or {}).get(k) for k in ("support", "oppose", "undecided"))
        for it in items
    )
    print(f"10 条全部三派非空: {prompts_all}")
    print(f"结论建议: 生成成功率 {len(interpreted)}/{len(items)}；计划书写作请使用“最多 2 条延伸阅读”而非“固定 2 条”")


def main() -> int:
    paths = [pathlib.Path(p) for p in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 2
    for p in paths:
        analyze(p.name, load(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
