# -*- coding: utf-8 -*-
"""时间轴与连续剧合订本编译器 (scripts/build_timeline.py)

落地原则：
1. 启发式分级归并（L1 别名/锚点 -> L2 Question ID直连），不讲无依据的虚假百分比；
2. 零数据库架构：读取 data/archive/ 与 data/topic_seed.json，编译输出至 data/topic_timeline.json；
3. 同步更新 demo/timeline.js 并执行工业级字符安全转义（\u003c, \u003e, \u2028, \u2029）；
4. 保证原子落盘，杜绝并发竞争。
"""
import glob
import json
import os
import pathlib
import re
import sys
import time

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
DEMO_DIR = BASE_DIR / "demo"

TIMELINE_JSON_PATH = DATA_DIR / "topic_timeline.json"
TIMELINE_JS_PATH = DEMO_DIR / "timeline.js"
SEED_PATH = DATA_DIR / "topic_seed.json"
ALIASES_PATH = DATA_DIR / "topic_aliases.json"


def _escape_js_payload(text: str) -> str:
    """与 app.py 中的 _js_safe_json 保持 100% 一致的安全转义规范，杜绝 XSS。"""
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def extract_question_id(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"question/(\d+)", str(url))
    return m.group(1) if m else ""


def build_timeline():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_files = sorted(ARCHIVE_DIR.glob("*.json"))

    # 1. 加载别名表与种子数据
    aliases = []
    if ALIASES_PATH.is_file():
        try:
            aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8")).get("topics", [])
        except Exception as e:
            print(f"[WARN] 别名表读取失败: {e}")

    seed_topics = []
    if SEED_PATH.is_file():
        try:
            seed_topics = json.loads(SEED_PATH.read_text(encoding="utf-8")).get("topics", [])
        except Exception as e:
            print(f"[WARN] 种子数据读取失败: {e}")

    # 2. 统计归档日期与缺失日期
    dates = []
    daily_items = {}
    for f in archive_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            d = data.get("date") or f.stem
            dates.append(d)
            daily_items[d] = data.get("items", [])
        except Exception as e:
            print(f"[WARN] 读取归档文件 {f.name} 失败: {e}")

    dates = sorted(list(set(dates)))
    missing_dates = []
    if len(dates) >= 2:
        # 简单日期连续性检查
        import datetime
        try:
            start_dt = datetime.datetime.strptime(dates[0], "%Y-%m-%d").date()
            end_dt = datetime.datetime.strptime(dates[-1], "%Y-%m-%d").date()
            curr = start_dt
            date_set = set(dates)
            while curr <= end_dt:
                s = curr.strftime("%Y-%m-%d")
                if s not in date_set:
                    missing_dates.append(s)
                curr += datetime.timedelta(days=1)
        except Exception:
            pass

    # 3. 组织话题时间轴（以种子话题为骨架，融合跨天归档数据）
    topics_map = {}
    for st in seed_topics:
        topics_map[st["title"]] = {
            "title": st["title"],
            "nodes": list(st.get("nodes", [])),
        }

    # 跨天归档数据匹配：检查是否命中别名表或已有话题的 Question ID
    for d, items in daily_items.items():
        for it in items:
            q_url = it.get("question_url", "")
            q_id = extract_question_id(q_url)
            title = it.get("title", "")

            # 匹配规则：1. ID 命中；2. 专名实体命中
            matched_title = None
            for topic_name, topic_obj in topics_map.items():
                for node in topic_obj.get("nodes", []):
                    if q_id and extract_question_id(node.get("question_url", "")) == q_id:
                        matched_title = topic_name
                        break
                if matched_title:
                    break

            if not matched_title:
                for alias in aliases:
                    if q_id in alias.get("question_ids", []):
                        matched_title = alias.get("canonical_title")
                        break
                    anchors = alias.get("anchor_entities", [])
                    if any(anc in title for anc in anchors):
                        matched_title = alias.get("canonical_title")
                        break

            if matched_title and matched_title in topics_map:
                existing_dates = [n.get("date") for n in topics_map[matched_title]["nodes"]]
                if d not in existing_dates:
                    # 增量挂载归档节点
                    state_map = {"dispute": "分歧爆发", "consensus": "形成共识", "verify": "待补材料"}
                    t_type = it.get("type", "verify")
                    new_node = {
                        "date": d,
                        "date_label": f"{d[5:]} 跟踪",
                        "state": t_type,
                        "state_label": state_map.get(t_type, "动态观察"),
                        "hook": it.get("verdict") or it.get("background") or title,
                        "question_url": q_url,
                        "key_answer": {
                            "title": it.get("reading", [{}])[0].get("title", "相关回答") if it.get("reading") else "知乎讨论原帖",
                            "url": it.get("reading", [{}])[0].get("url", q_url) if it.get("reading") else q_url,
                        },
                        "ext_ref": [],
                    }
                    if it.get("web_evidence") and it["web_evidence"].get("status") == "found":
                        new_node["ext_ref"].append({
                            "title": it["web_evidence"].get("source_title", "全网公开证据"),
                            "url": it["web_evidence"].get("url", ""),
                        })
                    topics_map[matched_title]["nodes"].append(new_node)
                    topics_map[matched_title]["nodes"].sort(key=lambda x: x.get("date", ""))

    compiled_topics = list(topics_map.values())

    timeline_data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "archive_files": len(archive_files),
        "tracked_days": len(dates),
        "missing_dates": missing_dates,
        "topics": compiled_topics,
    }

    # 4. 原子落盘 JSON 索引
    tmp_json = TIMELINE_JSON_PATH.with_suffix(".tmp")
    tmp_json.write_text(json.dumps(timeline_data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_json.replace(TIMELINE_JSON_PATH)

    # 5. 生成前端 timeline.js（含转义保护）
    raw_json = json.dumps(timeline_data, ensure_ascii=False)
    safe_payload = _escape_js_payload(raw_json)
    js_content = f"window.TIMELINE_DATA = {safe_payload};\n"

    tmp_js = TIMELINE_JS_PATH.with_suffix(".tmp")
    tmp_js.write_text(js_content, encoding="utf-8")
    tmp_js.replace(TIMELINE_JS_PATH)

    print(f"[OK] 时间轴编译成功：生成 {len(compiled_topics)} 个连续剧话题，更新 {TIMELINE_JS_PATH}")
    return timeline_data


if __name__ == "__main__":
    build_timeline()
