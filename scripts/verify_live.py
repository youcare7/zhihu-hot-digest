# -*- coding: utf-8 -*-
"""
线上站点验收脚本（部署后必须跑一次）。

用法：
    python scripts/verify_live.py
    python scripts/verify_live.py https://your-domain.example.com

判定标准（v2 六字段版）：
    GET /          必须含旧版 id="digest" 或新版首页今日判断挂载点、unslop 署名；不得含「仅为 Flask」
    GET /data.js   必须含 digest 对象，且 total == len(items) == dispute+consensus+verify
                   每条必须含 type；dispute 必须恰好 2 个 sides；非 dispute 不得有 sides
                   source=fallback 只代表服务存活，明确判为“内容未就绪”，不误报版本回退

退出码：0 = 全过；1 = 有不合格项或内容未就绪；2 = 网络/解析失败。
"""
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://zhihu-hot-digest-348140-5-1533039364.sh.run.tcloudbase.com"
TIMEOUT = 15
REASON_MAX = 30

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def fetch(url):
    """单次有界抓取；冷缓存恢复由前端或 smoke_check --wait 负责，不做超长盲等。"""
    req = urllib.request.Request(url, headers={"User-Agent": "verify-live/1.2"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, r.read().decode("utf-8", "replace")


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE).rstrip("/")
    print(f"验收目标：{base}\n")

    # ---------- GET / ----------
    print("[1] 前端 GET /")
    try:
        code, html = fetch(base + "/")
    except Exception as e:  # noqa: BLE001
        print(f"  请求失败：{e}")
        return 2
    check("HTTP 200", code == 200, f"status={code}")
    has_digest_mount = 'id="digest"' in html or (
        'id="today-heading"' in html and 'id="cover-insights"' in html
    )
    check("含今日判断挂载点（旧版 digest 或新版首页）", has_digest_mount)
    check("含 unslop 署名（合规披露）", "unslop" in html)
    check("不含「仅为 Flask」（旧版页脚）", "仅为 Flask" not in html)
    check("含 TYPE_LABEL 或 .sides（v2 渲染逻辑）", ("TYPE_LABEL" in html) or ("sides" in html))
    print(f"  前端字符数：{len(html)}")

    # ---------- GET /data.js ----------
    print("\n[2] 数据 GET /data.js")
    try:
        code, raw = fetch(base + "/data.js")
    except Exception as e:  # noqa: BLE001
        print(f"  请求失败：{e}")
        return 2
    check("HTTP 200", code == 200, f"status={code}")
    if "window.BRIEF_DATA" not in raw:
        check("含 window.BRIEF_DATA", False, "无法解析")
        return 1
    payload = raw.split("window.BRIEF_DATA", 1)[1].lstrip(" =").rstrip().rstrip(";")
    try:
        obj = json.loads(payload)
    except Exception as e:  # noqa: BLE001
        check("JSON 可解析", False, str(e)[:80])
        return 2
    items = obj.get("items") or []
    print(f"  date={obj.get('date')} source={obj.get('source')} items={len(items)}")
    if obj.get("source") == "fallback":
        state = "后台生成中" if obj.get("refreshing") is True else "静态兜底/失败冷却"
        retry = obj.get("retry_after") or 0
        check("内容未就绪（不能按真实简报验收通过）", False,
              f"{state}，retry_after={retry}；请等待恢复后复跑，或先用 smoke_check --wait")
        return 1

    # 模拟内容即使结构完整、主机名合规，也不属于真实线上简报。
    urls = []
    for item in items:
        if not isinstance(item, dict):
            continue
        urls.extend([item.get("question_url"), item.get("url")])
        urls.extend(r.get("url") for r in (item.get("reading") or []) if isinstance(r, dict))
    simulated = (
        obj.get("source") == "demo"
        or "模拟数据" in str(obj.get("notice") or "")
        or "模拟" in str(obj.get("generated_at") or "")
        or any(marker in str(url or "") for url in urls
               for marker in ("/question/mock-", "/answer/mock-"))
    )
    if simulated:
        check("真实线上内容（拒绝模拟数据）", False,
              "检测到 demo／模拟标记或 mock 链接；请清理模拟缓存并恢复真实生成")
        return 1

    dg = obj.get("digest")
    check("含 digest 对象（v2 已部署）", isinstance(dg, dict), "缺 digest 说明线上仍是 v1")
    if isinstance(dg, dict):
        total = dg.get("total")
        d, c, v = dg.get("dispute"), dg.get("consensus"), dg.get("verify")
        check("digest.total == len(items)", total == len(items), f"total={total} items={len(items)}")
        check(
            "dispute+consensus+verify == total",
            (d or 0) + (c or 0) + (v or 0) == total,
            f"{d}+{c}+{v}={((d or 0) + (c or 0) + (v or 0))}",
        )
        print(f"  分类：分歧 {d} / 共识 {c} / 待验证 {v}")
        print(f"  分歧性质：{dg.get('claim_counts')}")
        hl = (dg.get("headline") or "").strip()
        check("headline 非空", bool(hl), hl[:40])
        ins = dg.get("insights") or []
        check("insights 1–3 条", 1 <= len(ins) <= 3, f"{len(ins)} 条")

    # ---------- 逐条字段 ----------
    print("\n[3] 逐条字段（v2 六字段）")
    if items and items[0].get("type") is None:
        print("  ⚠ 本条不含 type —— 属旧格式（fallback 兜底或缓存未刷新），逐条检查跳过")
    else:
        bad = []
        for it in items:
            rank = it.get("rank")
            t = it.get("type")
            sides = it.get("sides") or []
            if t not in ("dispute", "consensus", "verify"):
                bad.append(f"#{rank} type 非法({t})")
            if t == "dispute" and len(sides) != 2:
                bad.append(f"#{rank} dispute 阵营数={len(sides)}")
            if t != "dispute" and sides:
                bad.append(f"#{rank} 非 dispute 却有阵营")
            if not (it.get("focus") or "").strip():
                bad.append(f"#{rank} focus 空")
            if not (it.get("gap") or "").strip():
                bad.append(f"#{rank} gap 空")
            if not it.get("reading"):
                bad.append(f"#{rank} reading 空")
            for r in (it.get("reading") or []):
                if len(str(r.get("reason") or "")) > REASON_MAX:
                    bad.append(f"#{rank} reason 超 {REASON_MAX} 字")
        check("六字段全部合法", not bad, "；".join(bad[:6]))
        print("  逐条：" + " ".join(f"#{i.get('rank')}={i.get('type')}" for i in items))

    # ---------- 后端确定性文案是否说谎 ----------
    print("\n[4] 后端确定性文案自洽性（build_digest 不允许编造类别）")
    if isinstance(dg, dict):
        cc = dg.get("claim_counts") or {}
        # 逐条 insight 都要查，不能只看 insights[0]：
        # build_digest 有三个分支，各自可能硬编码类别，漏检一个分支就等于没查。
        # 匹配表必须同时认全称与简称——else 分支的文案写的是「推测、观感或价值判断」，
        # 旧版只认「推测对推测/观感对观感」全称，导致 2026-11-16 20:26 线上漏检。
        patterns = {
            "inference": ("推测对推测", "推测"),
            "impression": ("观感对观感", "观感"),
            "value": ("价值判断",),
        }
        lies = []
        for idx, ins in enumerate(dg.get("insights") or [], 1):
            for key, names in patterns.items():
                if any(n in ins for n in names) and not cc.get(key):
                    lies.append(f"insight{idx} 提到「{names[0]}」但 claim_counts[{key}]=0")
        check("insight 未声称不存在的分歧类别", not lies,
              "；".join(lies[:4]) if lies else "全部与 claim_counts 一致")
        if lies:
            print("     ↑ 后端确定性文案在说谎：文案写死了类别，未按实际计数生成")

    # ---------- 汇总 ----------
    passed = sum(1 for _, ok, _ in results if ok)
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{'=' * 60}\n合计 {passed}/{len(results)} 项通过")
    if failed:
        print("未通过：")
        for n in failed:
            print(f"  - {n}")
        if any("digest" in n or "TYPE_LABEL" in n or "id=" in n for n in failed):
            print("\n结论：线上仍是 v1，或部署未生效 / 缓存未刷新。")
        else:
            print("\n结论：线上已是 v2，但存在上面列出的缺陷（不是版本问题，是代码问题）。")
        return 1
    print("结论：目标站点已是 v2 六字段版本，验收通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
