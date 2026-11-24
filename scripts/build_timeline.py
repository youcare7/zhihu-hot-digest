# -*- coding: utf-8 -*-
"""跨天归并话题并生成只读时间线数据。"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import time
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app

ARCHIVE_DIR = app.DATA_DIR / "archive"
ALIASES_PATH = app.DATA_DIR / "topic_aliases.json"
TIMELINE_PATH = app.DATA_DIR / "topic_timeline.json"
DEMO_TIMELINE_PATH = app.DEMO_DIR / "timeline.js"


def atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # Windows 下杀软/索引器可能在新文件落盘后瞬间持有句柄，重试而非直接失败。
    for attempt in range(5):
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(str(tmp), str(path))
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.4 * (attempt + 1))


def atomic_write_json(path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def load_aliases():
    try:
        data = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"aliases": {}, "splits": [], "hidden_topics": []}


def question_id(item):
    url = str(item.get("question_url") or "")
    match = re.search(r"/question/(\d+)", url)
    return match.group(1) if match else ""


def safe_http_url(value):
    """只放行 http/https 绝对链接。

    归档里的外链来自上游，前端会把它们写进 href。javascript:/data: 等协议
    或相对路径一律丢弃，既防 XSS，也守住「不展示不可核验链接」的防幻觉红线。
    """
    text = str(value or "").strip()
    return text if re.match(r"^https?://\S+$", text, re.IGNORECASE) else ""


def date_label(value):
    """把 ISO 日期转成前端可读的「11月21日」；非法值原样回落。"""
    try:
        parsed = datetime.date.fromisoformat(str(value))
        return f"{parsed.month}月{parsed.day}日"
    except (ValueError, TypeError):
        return str(value or "")


def normalize_title(title):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(title or ""), flags=re.UNICODE).lower()


def title_2grams(title):
    text = normalize_title(title)
    return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) >= 2 else set()


def jaccard_2gram(a, b):
    left, right = title_2grams(a), title_2grams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def shares_long_substring(a, b, minimum=3):
    a, b = normalize_title(a), normalize_title(b)
    if not a or not b:
        return False
    if len(a) > len(b):
        a, b = b, a
    generic = ("公司", "企业", "裁员", "公司裁员", "事件", "消息", "新闻", "发生", "相关")
    longest = ""
    for length in range(min(len(a), len(b)), minimum - 1, -1):
        for start in range(len(a) - length + 1):
            candidate = a[start:start + length]
            if candidate in b:
                longest = candidate if len(candidate) > len(longest) else longest
                break
        if longest:
            break
    if not longest or longest in generic:
        return False
    return True


def is_semantic_match(a, b):
    return jaccard_2gram(a, b) >= 0.30 and shares_long_substring(a, b, 3)


def sentinel_filter(value):
    sentinels = (app.NO_BACKGROUND, app.EMPTY_FOCUS, app.EMPTY_GAP)
    if hasattr(app, "EMPTY_CONSENSUS"):
        sentinels += (app.EMPTY_CONSENSUS,)
    text = str(value or "").strip()
    # 空值不是「有效内容」，旧写法会让空 focus/gap 通过过滤。
    return bool(text) and text not in sentinels


def clamp_hook(text, limit=24):
    text = re.sub(r"\s+", " ", str(text or "").strip())
    text = re.sub(r"^(目前缺少|需要当事方披露|本条热榜)", "", text).strip(" ，。,:：")
    return text[:limit]


def build_hook(item):
    for field in ("focus", "gap"):
        value = item.get(field)
        if sentinel_filter(value):
            hook = clamp_hook(value)
            if hook:
                return hook
    return clamp_hook(item.get("title") or "未命名话题")


def key_answer(item):
    reading = item.get("reading")
    if not isinstance(reading, list) or not reading:
        return None
    first = reading[0]
    if not isinstance(first, dict):
        return None
    title = str(first.get("title") or "").strip()
    url = safe_http_url(first.get("url"))
    if not title or not url:
        return None
    return {"title": title, "url": url}


def ext_ref(item):
    evidence = item.get("web_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "found":
        return []
    url = safe_http_url(evidence.get("url"))
    if not url:
        return []
    title = str(evidence.get("title") or evidence.get("source") or "站外公开信源").strip()
    return [{"title": title or "站外公开信源", "url": url}]


def load_archives():
    archives = []
    if not ARCHIVE_DIR.exists():
        return archives
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
    for path in sorted(ARCHIVE_DIR.glob("*.json")):
        match = pattern.match(path.name)
        if not match:
            continue
        try:
            datetime.date.fromisoformat(match.group(1))
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                data["_path_date"] = match.group(1)
                archives.append(data)
        except Exception:
            continue
    return archives


def missing_dates(dates):
    if len(dates) < 2:
        return []
    earliest = datetime.date.fromisoformat(min(dates))
    latest = datetime.date.fromisoformat(max(dates))
    present = set(dates)
    result = []
    day = earliest
    while day <= latest:
        value = day.isoformat()
        if value not in present:
            result.append(value)
        day += datetime.timedelta(days=1)
    return result


def deduplicate_nodes(nodes):
    if len(nodes) <= 2:
        return nodes
    keep = set([0, len(nodes) - 1])
    previous_state = nodes[0].get("state")
    previous_hook = nodes[0].get("hook")
    for index in range(1, len(nodes)):
        current_state = nodes[index].get("state")
        current_hook = nodes[index].get("hook")
        if current_state != previous_state or current_hook != previous_hook:
            keep.add(index - 1)
            keep.add(index)
        previous_state, previous_hook = current_state, current_hook
    return [node for index, node in enumerate(nodes) if index in keep]


def build_timeline(min_days=2):
    aliases = load_aliases()
    alias_map = aliases.get("aliases") if isinstance(aliases.get("aliases"), dict) else {}
    splits = aliases.get("splits") if isinstance(aliases.get("splits"), list) else []
    hidden = set(aliases.get("hidden_topics") or [])
    split_pairs = set()
    for pair in splits:
        if isinstance(pair, list) and len(pair) == 2:
            split_pairs.add((str(pair[0]), str(pair[1])))

    archives = load_archives()
    present_dates = sorted({str(a["_path_date"]) for a in archives})
    clusters = {}
    titles = {}

    for archive in sorted(archives, key=lambda a: a["_path_date"]):
        date = archive["_path_date"]
        for item in archive["items"]:
            if not isinstance(item, dict):
                continue
            qid = question_id(item)
            title = str(item.get("title") or "").strip()
            split = qid and any(topic_q == qid for topic_q, question_q in split_pairs)
            if qid and str(qid) in alias_map:
                topic_id = str(alias_map[str(qid)])
            elif split:
                topic_id = qid
            elif qid:
                topic_id = qid
            else:
                topic_id = "title:" + normalize_title(title)

            canonical = topic_id
            if not split:
                if topic_id in clusters or qid:
                    # 真实知乎问题号是比标题相似度更强的身份标识：不同 qid 的
                    # 条目不得仅凭标题相似被并进别的簇，否则会凭空造出「同一
                    # 话题跨天反转」的假关联。显式 aliases 已在上面改变 topic_id，
                    # 仍可按人工配置合并。无 qid 的站外条目才走标题相似匹配。
                    canonical = topic_id
                else:
                    for existing_id, existing_title in titles.items():
                        if existing_id in hidden:
                            continue
                        if is_semantic_match(title, existing_title):
                            canonical = existing_id
                            break
            if canonical not in clusters:
                clusters[canonical] = []
                titles[canonical] = title

            state = str(item.get("type") or "verify")
            hook = build_hook(item)
            clusters[canonical].append({
                "date": date,
                "date_label": date_label(date),
                "rank": item.get("rank"),
                "title": title,
                "question_id": qid,
                "state": state,
                "state_label": app.TYPE_NAMES.get(state, state),
                "hook": hook,
                "key_answer": key_answer(item),
                "ext_ref": ext_ref(item),
            })

    topics = []
    for topic_id, nodes in clusters.items():
        if topic_id in hidden:
            continue
        unique_dates = sorted({node["date"] for node in nodes})
        if len(unique_dates) < min_days:
            continue
        nodes.sort(key=lambda n: n["date"])
        nodes = deduplicate_nodes(nodes)
        state_changes = sum(
            1 for i in range(1, len(nodes))
            if nodes[i].get("state") != nodes[i - 1].get("state")
        )
        representative = next((node for node in reversed(nodes) if node.get("title")), {})
        latest = representative
        # topic_id 是内部聚类键（无真实 qid 时形如 "title:xxx"），不能直接
        # 暴露成 question_id。只有节点里抓到的纯数字知乎问题号才是可核验链接。
        real_qids = sorted({str(n["question_id"]) for n in nodes
                            if str(n.get("question_id") or "").isdigit()})
        primary_qid = real_qids[0] if real_qids else ""
        topic = {
            "topic_id": topic_id,
            "origin": "auto_archive",
            "title": latest.get("title") or "",
            "question_id": primary_qid,
            "question_ids": real_qids,
            "days": len(unique_dates),
            "appearances": len(unique_dates),
            "dates": unique_dates,
            "state_changes": state_changes,
            "has_state_change": state_changes > 0,
            "state_label": latest.get("state_label") or latest.get("state") or "",
            "latest_state_label": latest.get("state_label") or latest.get("state") or "",
            "key_answer": latest.get("key_answer"),
            "ext_ref": latest.get("ext_ref") or [],
            "nodes": nodes,
        }
        # 只由真实抓到的问题号构造知乎链接；无 qid 的话题不给链接，绝不编造。
        if primary_qid:
            topic["question_url"] = f"https://www.zhihu.com/question/{primary_qid}"
        topics.append(topic)

    topics.sort(
        key=lambda topic: (
            bool(topic["has_state_change"]),
            max(topic["dates"]) if topic["dates"] else "",
            topic["days"],
        ),
        reverse=True,
    )
    topics = topics[:8]
    missing = missing_dates(present_dates)
    tracked_days = (
        (datetime.date.fromisoformat(max(present_dates)) - datetime.date.fromisoformat(min(present_dates))).days + 1
        if present_dates else 0
    )
    result = {
        "generated_at": app.now_str(),
        "archive_files": len(archives),
        "tracked_days": tracked_days,
        "missing_dates": missing,
        "topics": topics,
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-days", type=int, default=2)
    args = parser.parse_args(argv)
    result = build_timeline(max(1, args.min_days))
    atomic_write_json(TIMELINE_PATH, result)
    js_payload = app._js_safe_json(json.dumps(result, ensure_ascii=False))
    atomic_write_text(DEMO_TIMELINE_PATH, f"window.TIMELINE_DATA = {js_payload};")
    print(
        f"时间线构建完成：归档 {result['archive_files']} 个，"
        f"跨度 {result['tracked_days']} 天，话题 {len(result['topics'])} 个"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
