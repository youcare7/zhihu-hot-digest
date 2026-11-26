# -*- coding: utf-8 -*-
"""每日真实简报归档。"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app


FIELDS = (
    "rank", "title", "question_url", "type", "claim_type", "background",
    "focus", "consensus", "sides", "gap", "reading", "web_evidence",
)


def has_healthy_background(item) -> bool:
    """判定一条条目是否带有真实可用的背景解读。

    防幻觉红线：字段缺失、None、纯空白、非字符串，或哨兵文案「暂无解读。」
    都必须算作降级。旧写法 `item.get("background") != app.NO_BACKGROUND` 在
    字段缺失/None 时恒为真，会把 5 条完全无解读的条目误计为「健康解读 5 条」
    并写进真实归档，进而让合订本凭空长出跨天话题。
    """
    if not isinstance(item, dict):
        return False
    if not str(item.get("title") or "").strip():
        return False
    background = item.get("background")
    if not isinstance(background, str):
        return False
    text = background.strip()
    return bool(text) and text != app.NO_BACKGROUND


def valid_archive_brief(data, target_date):
    if not isinstance(data, dict):
        return False
    if data.get("source") not in ("live", "cache"):
        return False
    if data.get("date") != target_date:
        return False
    items = data.get("items")
    return isinstance(items, list) and sum(
        1 for item in items if has_healthy_background(item)
    ) >= 5


def load_existing_brief(target_date):
    path = app.DATA_DIR / f"brief_{target_date}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if valid_archive_brief(data, target_date) else None


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    # Windows 下杀软/索引器可能在新文件落盘后瞬间持有句柄，重试而非直接失败。
    for attempt in range(5):
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(str(tmp), str(path))
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.4 * (attempt + 1))


# ---------------------------------------------------------------------------
# 兜底快照刷新（--update-fallback，仅每日归档任务开启）
# 首页静态兜底 data/fallback.json 与本地预览 demo/data.js 只用程序生成的完整简报；
# 人工整理内容放在 data/showcase.json（功能示例页），两者不再混用。
# ---------------------------------------------------------------------------
FALLBACK_MIN_ITEMS = 8
FALLBACK_NOTICE = "当前展示的是 {date} 由程序生成的简报快照，今日简报生成后自动切换。AI 内容仅供参考。"
DEMO_NOTICE = "本地预览：{date} 由知乎开放平台 API 生成的简报快照，AI 内容仅供参考。"
_SNAPSHOT_DROP = ("refreshing", "retry_after", "provenance", "offsite_links", "curation_note", "showcase")


def fallback_reject_reason(brief) -> str:
    """兜底快照的门槛比归档更严：至少 8 条、全部有效、无站外链接、非模拟。"""
    if not isinstance(brief, dict):
        return "简报不是对象"
    items = brief.get("items") if isinstance(brief.get("items"), list) else []
    if len(items) < FALLBACK_MIN_ITEMS:
        return f"条目仅 {len(items)} 条（至少 {FALLBACK_MIN_ITEMS} 条）"
    healthy = sum(1 for item in items if has_healthy_background(item))
    if healthy != len(items):
        return f"有 {len(items) - healthy} 条降级"
    if brief.get("provenance") == "curated" or app._offsite_link_count(brief):
        return "含人工整理内容或站外链接"
    if app.is_simulated_brief(brief):
        return "模拟数据"
    return ""


def refresh_fallback(brief, data_dir, demo_dir) -> bool:
    """用健康的程序生成简报覆盖 fallback.json 与 demo/data.js；不合格时保留现有文件。"""
    reason = fallback_reject_reason(brief)
    if reason:
        print(f"未更新兜底快照：{reason}，保留现有 fallback.json")
        return False
    snap = {k: v for k, v in brief.items() if k not in _SNAPSHOT_DROP}
    snap["items"] = [dict(item) for item in brief["items"]]
    for item in snap["items"]:
        if not isinstance(item.get("kanshan"), dict):
            item["kanshan"] = app._build_kanshan(
                item.get("type"), item.get("focus") or "", item.get("gap") or "", item.get("rank") or 1)
    if not isinstance(snap.get("digest"), dict):
        snap["digest"] = app.build_digest(snap["items"])
    if not str(snap.get("generated_at") or "").strip():
        snap["generated_at"] = app.now_str()
    date = str(snap.get("date") or "")
    fallback = dict(snap, source="fallback", notice=FALLBACK_NOTICE.replace("{date}", date))
    atomic_write(pathlib.Path(data_dir) / "fallback.json", fallback)
    # 本地双击预览用 source=cache：file:// 下无法轮询恢复，fallback 会让页面反复重试
    demo = dict(snap, source="cache", notice=DEMO_NOTICE.replace("{date}", date))
    app._atomic_write_text(
        pathlib.Path(demo_dir) / "data.js",
        "window.BRIEF_DATA = " + app._js_safe_json(json.dumps(demo, ensure_ascii=False)) + ";\n",
    )
    print(f"兜底快照已更新为 {date} 程序生成简报（{len(snap['items'])} 条）")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    parser.add_argument("--update-fallback", action="store_true",
                        help="归档成功后，用当日简报刷新 data/fallback.json 与 demo/data.js")
    args = parser.parse_args(argv)
    target_date = args.date or app.today_str()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date):
        print("拒绝归档：目标日期格式必须为 YYYY-MM-DD")
        return 1

    brief = load_existing_brief(target_date)
    if brief is None:
        if not (os.environ.get("ZHIHU_ACCESS_SECRET") or "").strip():
            print(
                "拒绝归档：未检测到 ZHIHU_ACCESS_SECRET 且本地无当日可用 brief 缓存，"
                "请在 GitHub Actions Secrets 中配置密钥"
            )
            return 1
        print(f"开始实时生成：预算 {app.GEN_BUDGET_SECONDS}s / {app.HOT_LIST_LIMIT} 条")
        brief = app.build_brief()

    if brief.get("source") not in ("live", "cache"):
        print(
            f"拒绝归档：source={brief.get('source')}，"
            "仅接受 live / cache（严禁归档 demo 或 fallback）"
        )
        return 1
    if brief.get("date") != target_date:
        print(
            f"拒绝归档：简报日期 {brief.get('date')} 与目标日期 {target_date} 不一致"
        )
        return 1

    items = brief.get("items") if isinstance(brief.get("items"), list) else []
    healthy = sum(1 for item in items if has_healthy_background(item))
    if healthy < 5:
        print(f"拒绝归档：有效解读仅 {healthy} 条，少于 5 条，绝不覆盖已有健康归档")
        return 1

    archived_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        archived = {field: item.get(field) for field in FIELDS}
        archived_items.append(archived)

    archived = {
        "date": target_date,
        "source": brief["source"],
        "archived_at": __import__("datetime").datetime.now(app.CN_TZ).strftime("%Y-%m-%d %H:%M"),
        "items": archived_items,
    }
    # 人工整理快照归档后也要保留来源标注，合订本与复核脚本据此区分管线产物
    annotated = app.annotate_provenance({"items": archived_items, "provenance": brief.get("provenance")})
    if annotated.get("provenance") == "curated":
        archived["provenance"] = "curated"
        archived["offsite_links"] = annotated.get("offsite_links", 0)
    atomic_write(app.DATA_DIR / "archive" / f"{target_date}.json", archived)
    print(f"归档完成：{target_date}，保留 {len(archived_items)} 条，其中健康解读 {healthy} 条")
    if args.update_fallback:
        # 兜底刷新失败不影响归档结果：保留旧兜底，归档照常成功
        try:
            refresh_fallback(brief, app.DATA_DIR, app.DEMO_DIR)
        except Exception as exc:
            print(f"兜底快照更新失败（归档已完成，不受影响）：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
