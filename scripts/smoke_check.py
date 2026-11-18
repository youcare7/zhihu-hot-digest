#!/usr/bin/env python3
"""最小 HTTP 冒烟验收：只查“线上到底能不能用”，不评价内容质量。

用法：
    python scripts/smoke_check.py                      # 默认打公网
    python scripts/smoke_check.py --url http://127.0.0.1:5000
    python scripts/smoke_check.py --wait 360             # 部署后最多等待 6 分钟恢复
    python scripts/smoke_check.py --file data.js        # 离线复核本地快照

检查项（任一失败即 exit 1）：
  1. GET /            → HTTP 200，HTML 里引用了 data.js
  2. GET /data.js     → HTTP 200，JSON 可解析
  3. 数据条数          → 恰好 10 条（PRD 固定值）
  4. 必填字段          → title/background/focus/gap 非空，type 合法
  5. 三态自洽          → digest 计数与 items 实际分类一致
  6. 延伸阅读          → 每条 ≤2、主机名为知乎、条内不重复
  7. 来源标识          → live/cache/demo/fallback 之一

注意：--file 只接受运行时产物（线上 data.js / 本地生成结果）。
`data/baseline_insight_v2.json` 是人工归类预览基线，不是运行时产物，不要用它跑冒烟。
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

DEFAULT_URL = "https://zhihu-hot-digest-348140-5-1533039364.sh.run.tcloudbase.com"
ALLOWED_TYPES = {"dispute", "consensus", "verify"}
ALLOWED_SOURCES = {"live", "cache", "demo", "fallback"}
REQUIRED = ("title", "background", "focus", "gap")


def fail(msg):
    print(f"  ✗ {msg}")
    return 1


def parse_data_js(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("{"):
        return json.loads(text)
    start = raw.index("=") + 1
    return json.loads(raw[start:].strip().rstrip(";"))


def is_zhihu(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(str(url or "").strip())
    except (TypeError, ValueError):
        return False
    host = (p.hostname or "").lower().rstrip(".")
    return p.scheme in ("http", "https") and (host == "zhihu.com" or host.endswith(".zhihu.com"))


def simulated_reason(data: dict) -> str:
    """模拟/demo 内容不得通过真实线上内容门，即使被误标成 cache。"""
    if data.get("source") == "demo":
        return "source=demo"
    if "模拟数据" in str(data.get("notice") or ""):
        return "notice 含模拟数据"
    if "模拟" in str(data.get("generated_at") or ""):
        return "generated_at 含模拟标记"
    items = data.get("items") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            urls = [item.get("question_url"), item.get("url")]
            reading = item.get("reading") or []
            if isinstance(reading, list):
                urls.extend(r.get("url") for r in reading if isinstance(r, dict))
            if any(marker in str(url or "") for url in urls
                   for marker in ("/question/mock-", "/answer/mock-")):
                return "含 mock 链接"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--file", help="离线 data.js 路径；给出时跳过 HTTP 检查")
    ap.add_argument("--timeout", type=float, default=15, help="单次 HTTP 请求超时秒数，默认 15")
    ap.add_argument("--wait", type=float, default=0,
                    help="冷缓存时等待真实内容恢复的总秒数；默认 0，严格单次验收")
    ap.add_argument("--poll-interval", type=float, default=15,
                    help="后台生成中的轮询间隔秒数，默认 15")
    args = ap.parse_args()
    if args.timeout <= 0 or args.wait < 0 or args.poll_interval <= 0:
        ap.error("--timeout/--poll-interval 必须大于 0，--wait 不得小于 0")

    errors = 0
    print(f"冒烟验收 · 目标 {args.file or args.url}")

    if args.file:
        try:
            with open(args.file, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            return fail(f"快照读取失败：{exc}") or 1
    else:
        # 1. 首页
        try:
            home = urllib.request.urlopen(args.url + "/", timeout=args.timeout).read().decode("utf-8", "replace")
            print(f"  ✓ GET / → 200（{len(home)} 字节）")
            if "data.js" not in home:
                errors += fail("首页未引用 data.js，页面与数据可能脱节")
        except Exception as exc:
            errors += fail(f"GET / 失败：{type(exc).__name__} {exc}")
            home = ""
        # 2. data.js。fallback 是明确的“内容未就绪”，不是版本错误或普通坏 JSON。
        deadline = time.monotonic() + args.wait
        while True:
            try:
                started = time.monotonic()
                raw = urllib.request.urlopen(
                    args.url + "/data.js", timeout=args.timeout
                ).read().decode("utf-8", "replace")
                elapsed = time.monotonic() - started
                print(f"  ✓ GET /data.js → 200（{len(raw)} 字节，{elapsed:.2f}s）")
            except Exception as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return fail(f"GET /data.js 失败：{type(exc).__name__} {exc}") or 1
                delay = min(args.poll_interval, remaining)
                print(f"  · 请求暂失败，{delay:.1f}s 后重试：{type(exc).__name__} {exc}")
                time.sleep(delay)
                continue

            try:
                preview = parse_data_js(raw)
            except Exception:
                break  # 交给下方统一解析与报错，避免在这里掩盖坏响应
            if preview.get("source") != "fallback":
                break

            refreshing = preview.get("refreshing") is True
            try:
                retry_after = max(0.0, float(preview.get("retry_after") or 0))
            except (TypeError, ValueError):
                retry_after = 0.0
            state = "后台生成中" if refreshing else (
                f"失败冷却，约 {int(retry_after)}s 后重试" if retry_after > 0 else "无恢复状态"
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return fail(f"内容未就绪：source=fallback（{state}）；服务可用不代表内容验收通过") or 1
            if refreshing:
                delay = args.poll_interval
            elif retry_after > 0:
                delay = max(1.0, retry_after + 1)
            else:
                delay = max(30.0, args.poll_interval)
            if delay > remaining:
                return fail(f"内容未就绪：{state}，超出本次 --wait={args.wait:g}s 等待上限") or 1
            print(f"  · 内容未就绪（{state}），{delay:.1f}s 后重试")
            time.sleep(delay)

    # 3. 解析
    try:
        data = parse_data_js(raw)
    except Exception as exc:
        return fail(f"data.js 无法解析为 JSON：{type(exc).__name__} {exc}") or 1
    items = data.get("items") or []
    print(f"  · date={data.get('date')} source={data.get('source')} 条数={len(items)}")

    simulated = simulated_reason(data)
    if simulated:
        errors += fail(f"模拟数据不属于真实线上内容（{simulated}）")
    if len(items) != 10:
        errors += fail(f"条数为 {len(items)}，PRD 固定 10 条")
    if data.get("source") not in ALLOWED_SOURCES:
        errors += fail(f"source={data.get('source')!r} 不在 {sorted(ALLOWED_SOURCES)} 内")

    # 4. 必填字段与类型
    for it in items:
        rank = it.get("rank", "?")
        for k in REQUIRED:
            if not str(it.get(k) or "").strip():
                errors += fail(f"#{rank} 缺少 {k}")
        if str(it.get("background") or "").strip() == "暂无解读。":
            errors += fail(f"#{rank} background 为降级哨兵「暂无解读。」")
        if it.get("type") not in ALLOWED_TYPES:
            errors += fail(f"#{rank} type={it.get('type')!r} 非法")
        if it.get("type") == "dispute" and len(it.get("sides") or []) != 2:
            errors += fail(f"#{rank} 声明真分歧但阵营数不是 2")
        if it.get("type") != "dispute" and (it.get("sides") or []):
            errors += fail(f"#{rank} 非真分歧却带阵营数据")
        qurl = it.get("question_url") or it.get("url")
        if not is_zhihu(qurl):
            errors += fail(f"#{rank} 标题链接非知乎域名：{qurl}")

    # 5. digest 与 items 自洽
    digest = data.get("digest") or {}
    headline = str(digest.get("headline") or "")
    counts = {t: sum(1 for i in items if i.get("type") == t) for t in ALLOWED_TYPES}
    if not headline:
        errors += fail("digest.headline 为空")
    else:
        for t, label in (("dispute", "真分歧"), ("consensus", "已形成共识"), ("verify", "待验证")):
            if f"{label} {counts[t]} 条" not in headline:
                errors += fail(f"headline 与 items 不一致：{label} 实际 {counts[t]} 条，未在 headline 出现")
        print(f"  ✓ digest 自洽（{counts['dispute']}/{counts['consensus']}/{counts['verify']}）")

    # 6. 延伸阅读
    total = 0
    for it in items:
        rank = it.get("rank", "?")
        rs = it.get("reading") or []
        if len(rs) > 2:
            errors += fail(f"#{rank} 延伸阅读 {len(rs)} 条，超过硬上限 2")
        seen = set()
        for r in rs:
            total += 1
            url = r.get("url")
            if not is_zhihu(url):
                errors += fail(f"#{rank} 延伸阅读非知乎域名：{url}")
            if url in seen:
                errors += fail(f"#{rank} 延伸阅读条内重复：{url}")
            seen.add(url)
    print(f"  ✓ 延伸阅读 {total} 条，全部知乎域名、每条 ≤2、无条内重复")

    if errors:
        print(f"\n结果：FAIL（{errors} 项问题）")
        return 1
    print("\n结果：PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
