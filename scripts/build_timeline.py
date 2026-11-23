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
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app

ARCHIVE_DIR = app.DATA_DIR / "archive"
ALIASES_PATH = app.DATA_DIR / "topic_aliases.json"
TIMELINE_PATH = app.DATA_DIR / "topic_timeline.json"
DEMO_TIMELINE_PATH = app.DEMO_DIR / "timeline.js"
SEED_PATH = app.DATA_DIR / "topic_seed.json"


def atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def atomic_write_json(path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


BUILTIN_SEED_TOPICS = [
    {
        "title": "大模型对话上下文上限与工程推理成本博弈",
        "nodes": [
            {
                "date": "2026-11-15",
                "date_label": "11-15 首日",
                "state": "dispute",
                "state_label": "分歧爆发",
                "hook": "DeepSeek 调整长文本对话上限引发争议，部分开发者怀疑服务降级，系统架构师则主张属于 KV Cache 显存成本与吞吐权衡。",
                "question_url": "https://www.zhihu.com/question/2105339969564827779",
                "key_answer": {
                    "title": "从 Attention 算力成本角度谈为何必须截断",
                    "url": ""
                },
                "ext_ref": []
            },
            {
                "date": "2026-11-18",
                "date_label": "11-18 演化",
                "state": "verify",
                "state_label": "待补材料",
                "hook": "社群实测不同提示词工程下召回率差异巨大，缺少标准化「大海捞针」（NIAH）基准复测，尚难定论有效长度损失。",
                "question_url": "https://www.zhihu.com/question/2105339969564827779",
                "key_answer": {
                    "title": "万字长文：40组跨轮次召回实验报告",
                    "url": ""
                },
                "ext_ref": []
            },
            {
                "date": "2026-11-21",
                "date_label": "11-21 破局",
                "state": "consensus",
                "state_label": "形成共识",
                "hook": "官方技术白皮书披露动态分片机制与检索对齐，第三方复测证实 64k 关键信息检索零衰减，争议从质疑模型转向工程落地适配。",
                "question_url": "https://www.zhihu.com/question/2105339969564827779",
                "key_answer": {
                    "title": "知乎技术专家：大模型从跑分狂欢走向工程精算",
                    "url": ""
                },
                "ext_ref": []
            }
        ]
    },
    {
        "title": "出海中企跨国用工与法律合规风险审查",
        "nodes": [
            {
                "date": "2026-11-16",
                "date_label": "11-16 首日",
                "state": "verify",
                "state_label": "待补材料",
                "hook": "芬兰媒体报道中企工厂涉用工合规调查，舆论聚焦于跨境劳务派遣合法性，一手执法通报尚未公布。",
                "question_url": "https://www.zhihu.com/question/2105092208948933353",
                "key_answer": {
                    "title": "涉外劳工法专业律师：调查阶段不等于违法定性",
                    "url": ""
                },
                "ext_ref": []
            },
            {
                "date": "2026-11-19",
                "date_label": "11-19 演化",
                "state": "dispute",
                "state_label": "分歧爆发",
                "hook": "分包商责任争议放大，评论区激辩「国内用工习惯向外输出」与「当地工会制度严苛阻滞建厂」两派观点激烈对撞。",
                "question_url": "https://www.zhihu.com/question/2105092208948933353",
                "key_answer": {
                    "title": "驻欧制造业合规总监：三级分包链条的法律责任归属",
                    "url": ""
                },
                "ext_ref": []
            },
            {
                "date": "2026-11-22",
                "date_label": "11-22 破局",
                "state": "consensus",
                "state_label": "形成共识",
                "hook": "企业发布声明澄清已更换第三方外包服务商并完成用工补救，知乎出海专栏总结出中资高端制造进军北欧的合规清单。",
                "question_url": "https://www.zhihu.com/question/2105092208948933353",
                "key_answer": {
                    "title": "中国制造出海的必修课：北欧劳工法律红线合集",
                    "url": ""
                },
                "ext_ref": []
            }
        ]
    },
    {
        "title": "中国平均家庭户规模缩减与独居社会变迁",
        "nodes": [
            {
                "date": "2026-11-17",
                "date_label": "11-17 首日",
                "state": "verify",
                "state_label": "待补材料",
                "hook": "户均降至 2.52 人的数据引发全网讨论，多方猜测系单身率上升或居住分离所致，需结合省际流动与老龄化深度结构分析。",
                "question_url": "https://www.zhihu.com/question/2105340483975304106",
                "key_answer": {
                    "title": "人口学者解答：2.52 人背后的一人户与空巢户结构",
                    "url": ""
                },
                "ext_ref": []
            },
            {
                "date": "2026-11-20",
                "date_label": "11-20 演化",
                "state": "dispute",
                "state_label": "分歧爆发",
                "hook": "城市规划派认为微型住宅将迎繁荣，传统经济学派则担忧家庭抗风险能力剧降与养老兜底断裂，形成宏观与微观视角分歧。",
                "question_url": "https://www.zhihu.com/question/2105340483975304106",
                "key_answer": {
                    "title": "城市经济学视角：单身经济与居住空间演变",
                    "url": ""
                },
                "ext_ref": []
            }
        ]
    }
]


def load_seeds(path=None):
    items = []
    seed_override = getattr(sys.modules.get(__name__), "SEED_PATH", None)
    target = path or seed_override or (app.DATA_DIR / "topic_seed.json")
    if target and target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                items = data.get("topics") or []
        except Exception:
            items = []
    elif path is None and (seed_override is None or seed_override == (app.DATA_DIR / "topic_seed.json")):
        items = BUILTIN_SEED_TOPICS

    seed_topics = []
    for item in items:
        if not isinstance(item, dict):
            continue
        nodes = item.get("nodes") or []
        cleaned_nodes = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            node_copy = dict(n)
            # Curated samples carry no verified external references.
            node_copy["ext_ref"] = []
            answer = node_copy.get("key_answer")
            if isinstance(answer, dict) and answer.get("url") == node_copy.get("question_url"):
                node_copy["key_answer"] = {**answer, "url": ""}
            cleaned_nodes.append(node_copy)
        dates = sorted({n.get("date") for n in cleaned_nodes if n.get("date")})
        state_changes = sum(
            1 for i in range(1, len(cleaned_nodes))
            if cleaned_nodes[i].get("state") != cleaned_nodes[i - 1].get("state")
        )
        latest = cleaned_nodes[-1] if cleaned_nodes else {}
        title = str(item.get("title") or "").strip()
        topic_id = str(item.get("topic_id") or ("seed:" + normalize_title(title)))
        seed_topic = {
            "topic_id": topic_id,
            "origin": "curated_seed",
            "title": title,
            "question_id": str(item.get("question_id") or question_id(latest)),
            "question_ids": sorted({question_id(n) for n in cleaned_nodes if question_id(n)}),
            "days": len(dates),
            "appearances": len(dates),
            "dates": dates,
            "state_changes": state_changes,
            "has_state_change": state_changes > 0,
            "state_label": latest.get("state_label") or latest.get("state") or "",
            "latest_state_label": latest.get("state_label") or latest.get("state") or "",
            "key_answer": latest.get("key_answer"),
            "ext_ref": latest.get("ext_ref") or [],
            "nodes": cleaned_nodes,
        }
        seed_topics.append(seed_topic)
    return seed_topics


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
    return str(value or "").strip() not in sentinels


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
    url = str(first.get("url") or "").strip()
    if not title or not url:
        return None
    return {"title": title, "url": url}


def ext_ref(item):
    evidence = item.get("web_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "found":
        return []
    url = str(evidence.get("url") or "").strip()
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


def build_timeline(min_days=2, include_seeds=True):
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
                if topic_id in clusters:
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
        topic = {
            "topic_id": topic_id,
            "origin": "auto_archive",
            "title": latest.get("title") or "",
            "question_id": topic_id,
            "question_ids": sorted({str(n["question_id"]) for n in nodes if n.get("question_id")}),
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
        topics.append(topic)

    if include_seeds:
        existing_titles = {t["title"] for t in topics}
        for seed in load_seeds():
            if seed["title"] not in existing_titles:
                topics.append(seed)

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
    parser.add_argument("--no-seeds", action="store_true", default=False,
                        help="Do not inject curated seed topics")
    args = parser.parse_args(argv)
    result = build_timeline(max(1, args.min_days), include_seeds=(not args.no_seeds))
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
