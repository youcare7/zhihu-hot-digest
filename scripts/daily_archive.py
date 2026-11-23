# -*- coding: utf-8 -*-
"""每日真实简报归档。"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app


FIELDS = (
    "rank", "title", "question_url", "type", "claim_type", "background",
    "focus", "consensus", "sides", "gap", "reading", "web_evidence",
)


def valid_archive_brief(data, target_date):
    if not isinstance(data, dict):
        return False
    if data.get("source") not in ("live", "cache"):
        return False
    if data.get("date") != target_date:
        return False
    items = data.get("items")
    return isinstance(items, list) and sum(
        1 for item in items
        if isinstance(item, dict)
        and str(item.get("title") or "").strip()
        and item.get("background") != app.NO_BACKGROUND
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
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    args = parser.parse_args(argv)
    target_date = args.date or app.today_str()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date):
        print("拒绝归档：目标日期格式必须为 YYYY-MM-DD")
        return 1

    brief = load_existing_brief(target_date)
    if brief is None:
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
    healthy = sum(
        1 for item in items
        if isinstance(item, dict) and item.get("background") != app.NO_BACKGROUND
    )
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
    atomic_write(app.DATA_DIR / "archive" / f"{target_date}.json", archived)
    print(f"归档完成：{target_date}，保留 {len(archived_items)} 条，其中健康解读 {healthy} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
