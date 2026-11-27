# -*- coding: utf-8 -*-
"""跨天归并话题并生成只读时间线数据。

v3（合订本升级）：去噪 → 双轨特征（硬实体 / 事件词）→ 跨天复合打分与硬闸门
→ 并查集聚类 → 话题 + 往期真实归档双层输出。

防幻觉红线：topics 与 archives 100% 来自 data/archive/*.json 的真实字段，
话题标题直接采用归档内原始标题，不引入任何种子话题或编造外链。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app

ARCHIVE_DIR = app.DATA_DIR / "archive"
ALIASES_PATH = app.DATA_DIR / "topic_aliases.json"
TIMELINE_PATH = app.DATA_DIR / "topic_timeline.json"
DEMO_TIMELINE_PATH = app.DEMO_DIR / "timeline.js"

MATCH_WINDOW_DAYS = 14
SCORE_THRESHOLD = 0.62
SCORE_THRESHOLD_MULTI_ENTITY = 0.52
VALID_STATES = ("dispute", "consensus", "verify")

# ---------------------------------------------------------------------------
# 原子写入（Windows 瞬时文件锁重试）
# ---------------------------------------------------------------------------
def atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
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
    """只放行 http/https 绝对链接，杜绝 javascript:/data: 等伪协议。"""
    text = str(value or "").strip()
    return text if re.match(r"^https?://\S+$", text, re.IGNORECASE) else ""


def _normalize_edit_ts(value):
    """把秒/毫秒级 EditTime 统一成 (unix_sec, YYYY-MM-DD)；非法值返回 (0, '')。"""
    if isinstance(value, bool):
        return 0, ""
    try:
        ts = int(float(value))
    except (TypeError, ValueError):
        return 0, ""
    if 10**11 <= ts < 10**14:
        ts //= 1000
    if ts <= 0:
        return 0, ""
    try:
        date_str = datetime.datetime.fromtimestamp(ts, app.CN_TZ).strftime("%Y-%m-%d")
        return ts, date_str
    except (OverflowError, OSError, ValueError):
        return 0, ""


def fetch_related_past(topic, limit=3):
    """双轨隔离的「往期站内讨论」：纯程序按硬实体检索与去重，不调模型、不打三态、不入 nodes[]。"""
    title = str(topic.get("title") or "").strip()
    entities = [str(e).strip().lower() for e in (topic.get("entities") or []) if str(e).strip()]
    query = " ".join(entities[:2]).strip() if entities else denoise_title(title)[:24]
    if not query:
        return []
    try:
        candidates = app.api_search(query, count=10)
    except Exception:
        return []
    if not isinstance(candidates, list):
        return []

    existing_qids = {str(q) for q in (topic.get("question_ids") or []) if str(q)}
    if topic.get("question_id"):
        existing_qids.add(str(topic["question_id"]))
    title_kw = event_keywords(denoise_title(title))
    seen_qids = set()
    rows = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        c_title = str(entry.get("Title") or "").strip()
        c_url = safe_http_url(entry.get("Url"))
        if not c_title or not c_url or not app._is_zhihu_url(c_url):
            continue
        qid = question_id({"question_url": c_url})
        if qid and (qid in existing_qids or qid in seen_qids):
            continue
        lowered = c_title.lower()
        if entities:
            if not any(ent in lowered for ent in entities):
                continue
        else:
            c_kw = event_keywords(denoise_title(c_title))
            if len(title_kw & c_kw) < 2:
                continue
        if qid:
            seen_qids.add(qid)
        edit_ts, edit_date = _normalize_edit_ts(entry.get("EditTime"))
        row = {
            "title": c_title,
            "url": c_url,
            "question_id": qid,
            "edit_time": edit_ts,
            "edit_date": edit_date,
        }
        rows.append(row)

    rows.sort(key=lambda r: (r["edit_time"], r["title"]), reverse=True)
    return rows[:max(0, int(limit))]


def date_label(value):
    """把 ISO 日期转成前端可读的「11月21日」；非法值原样回落。"""
    try:
        parsed = datetime.date.fromisoformat(str(value))
        return f"{parsed.month}月{parsed.day}日"
    except (ValueError, TypeError):
        return str(value or "")


# ---------------------------------------------------------------------------
# 3.1 去噪 → 双轨特征 → 复合打分 + 硬闸门
# ---------------------------------------------------------------------------
TEMPLATE_PREFIX = re.compile(
    r"^(如何看待|怎样评价|怎么看|为什么|为何|如何|请问|网传|传闻|曝|传|怎样)"
)
TEMPLATE_SUFFIX = re.compile(
    r"(有哪些值得关注.*|是什么体验.*|是真的吗.*|具体是怎么回事.*|实际有哪些调整.*"
    r"|怎样解读.*|如何理解.*|哪些信息值得关注.*|带给投资者哪些反思.*|该如何看待.*"
    r"|问题出在哪.*|该如何合理界定.*|有哪些好处.*|能持续多久.*|有哪些信息值得关注.*"
    r"|怎样看.*|怎么看.*|如何看待.*)$"
)
PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)

# 热榜泛词 / 停用词：事件词轨过滤，硬闸门也据此判断「非泛化事件词」。
GENERIC_WORDS = frozenset({
    "回应", "通报", "曝光", "热议", "网友", "最新", "官方", "事件", "争议",
    "如何看待", "怎样评价", "是什么", "有哪些", "为什么", "真的吗", "具体",
    "实际", "问题", "信息", "值得关注", "如何", "怎么", "怎样", "目前",
    "相关", "发生", "消息", "新闻", "公司", "企业", "裁员", "哪些", "觉得",
    "解读", "理解", "持续", "多久", "背后", "一下", "真的", "带来", "影响",
    "情况", "表示", "落地", "调整", "内容", "此事", "这一", "是否", "能否",
})

_ENTITY_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,}")
_ENTITY_BOOK = re.compile(r"《([^》]{1,40})》")
_ENTITY_CORNER = re.compile(r"「([^」]{1,40})」")
_ENTITY_QUOTE = re.compile(r"[“\"]([^”\"\s]{1,40})[”\"]")
_ENTITY_CODE = re.compile(r"(?:[A-Za-z]+|[\u4e00-\u9fff]{1,4})\d+[A-Za-z0-9_-]*")
_ENTITY_CN_CODE = re.compile(r"[\u4e00-\u9fff]{2,4}[一二三四五六七八九十百千万]+[号代期届轮型版]")

try:  # jieba 为可选增强，未安装时完全走本地规则
    import jieba.posseg as _pseg
except Exception:
    _pseg = None


def denoise_title(title):
    """剥离问句模板与标点，提取事件主干（纯本地，零外部 API）。"""
    text = str(title or "").strip()
    text = TEMPLATE_PREFIX.sub("", text)
    text = TEMPLATE_SUFFIX.sub("", text)
    return PUNCT.sub("", text, ).lower()


def hard_entities(title):
    """硬实体轨：英文专名/型号、书名号/引号内专名、带数字专有代号。"""
    text = str(title or "")
    found = set()
    for match in _ENTITY_LATIN.findall(text):
        found.add(match.lower())
    for pattern in (_ENTITY_BOOK, _ENTITY_CORNER, _ENTITY_QUOTE):
        for match in pattern.findall(text):
            value = str(match).strip().lower()
            if value:
                found.add(value)
    for match in _ENTITY_CODE.findall(text):
        found.add(match.lower())
    for match in _ENTITY_CN_CODE.findall(denoise_title(text)):
        found.add(match.lower())
    if _pseg is not None:
        try:
            for word, flag in _pseg.cut(text):
                if flag in ("nr", "ns", "nt", "nz") and len(word) >= 2:
                    found.add(word.lower())
        except Exception:
            pass
    return found


def event_keywords(stem):
    """事件词轨：主干 2–4 字切词，过滤停用词与热榜泛词。"""
    words = set()
    if _pseg is not None:
        try:
            for word, flag in _pseg.cut(stem):
                value = word.strip().lower()
                if 2 <= len(value) <= 4 and flag.startswith("n") and value not in GENERIC_WORDS:
                    words.add(value)
        except Exception:
            words = set()
    if not words:
        for size in (2, 3, 4):
            for index in range(len(stem) - size + 1):
                value = stem[index:index + size]
                if value in GENERIC_WORDS or any(g in value for g in GENERIC_WORDS):
                    continue
                words.add(value)
    return words


def _bigrams(text):
    return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) >= 2 else set()


def _jaccard(left, right):
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _overlap(left, right):
    """重叠系数：同事件不同问法时比 Jaccard 更稳健。"""
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def cross_day_match(rec_a, rec_b):
    """仅跨天且间隔 <= 14 天时计算复合打分，并通过防误合并硬闸门。"""
    if rec_a["date"] == rec_b["date"]:
        return False
    try:
        gap = abs(
            (datetime.date.fromisoformat(rec_a["date"])
             - datetime.date.fromisoformat(rec_b["date"])).days
        )
    except (ValueError, TypeError):
        return False
    if gap > MATCH_WINDOW_DAYS:
        return False

    shared_entities = rec_a["entities"] & rec_b["entities"]
    shared_keywords = rec_a["keywords"] & rec_b["keywords"]
    non_generic_shared = {w for w in shared_keywords if w not in GENERIC_WORDS}
    # 硬闸门：至少 1 个硬实体，或 2 个非泛化事件词。
    if not shared_entities and len(non_generic_shared) < 2:
        return False

    kw_overlap = _overlap(rec_a["keywords"], rec_b["keywords"])
    bigram_jaccard = _jaccard(_bigrams(rec_a["stem"]), _bigrams(rec_b["stem"]))
    if shared_entities:
        score = (
            0.50 * _overlap(rec_a["entities"], rec_b["entities"])
            + 0.30 * kw_overlap
            + 0.20 * bigram_jaccard
        )
    else:
        score = (0.30 * kw_overlap + 0.20 * bigram_jaccard) / 0.50
    threshold = SCORE_THRESHOLD_MULTI_ENTITY if len(shared_entities) >= 2 else SCORE_THRESHOLD
    return score >= threshold


def _parse_iso_date(value):
    try:
        return datetime.date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


class UnionFind:
    def __init__(self, size, dates=None, max_span_days=MATCH_WINDOW_DAYS):
        self.parent = list(range(size))
        self.max_span_days = max_span_days
        if dates is not None:
            parsed = [_parse_iso_date(d) for d in dates]
            self.min_date = list(parsed)
            self.max_date = list(parsed)
        else:
            self.min_date = None
            self.max_date = None

    def find(self, node):
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def union(self, left, right):
        a, b = self.find(left), self.find(right)
        if a == b:
            return True
        if self.min_date is not None:
            min_a, max_a = self.min_date[a], self.max_date[a]
            min_b, max_b = self.min_date[b], self.max_date[b]
            if min_a is not None and min_b is not None:
                merged_min = min(min_a, min_b)
                merged_max = max(max_a, max_b)
                if (merged_max - merged_min).days > self.max_span_days:
                    return False
                self.min_date[a] = merged_min
                self.max_date[a] = merged_max
            elif min_b is not None:
                self.min_date[a] = min_b
                self.max_date[a] = max_b
        self.parent[b] = a
        return True


def link_topics_with_llm(records):
    """1-call 批量 LLM 跨天语义归并；未配置 MODEL_API_KEY 或任何异常自动回落为 []。"""
    api_key = os.environ.get("MODEL_API_KEY", "").strip()
    model_base = os.environ.get("MODEL_BASE", "").rstrip("/")
    model_name = os.environ.get("MODEL_NAME", "").strip()
    if not (api_key and model_base and model_name) or len(records) < 2:
        return []

    candidates = [
        (idx, rec) for idx, rec in enumerate(records)
        if not rec.get("split") and rec.get("title")
    ]
    if len({rec["date"] for _, rec in candidates}) < 2:
        return []

    lines = [f"[{idx}] {rec['date']} | {rec['title']}" for idx, rec in candidates]
    prompt = (
        "以下是不同日期的知乎热榜话题列表（格式：[编号] 日期 | 标题）。\n"
        "请找出属于**同一个持续新闻事件或同一核心议题**的跨天条目组（每组至少 2 个不同日期的编号）。\n"
        "注意：不要仅仅因为都包含「如何看待」「官方通报」等泛问句模板而合并。\n"
        "只输出严格 JSON 对象，格式为：{\"groups\": [[0, 5], [2, 8]]}；若无可合并项则输出 {\"groups\": []}。\n\n"
        + "\n".join(lines)
    )
    payload = json.dumps(
        {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{model_base}/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        body = json.loads(raw)
        content = str(body["choices"][0]["message"]["content"]).strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("groups"), list):
            return []
        valid_groups = []
        for group in parsed["groups"]:
            if not isinstance(group, list) or len(group) < 2:
                continue
            clean_ids = []
            seen = set()
            for item in group:
                if isinstance(item, bool) or not isinstance(item, int):
                    continue
                if 0 <= item < len(records) and not records[item].get("split") and item not in seen:
                    seen.add(item)
                    clean_ids.append(item)
            if len(clean_ids) >= 2 and len({records[i]["date"] for i in clean_ids}) >= 2:
                valid_groups.append(clean_ids)
        return valid_groups
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 兼容保留的展示工具（旧测试与主流程仍依赖）
# ---------------------------------------------------------------------------
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
    return bool(longest) and longest not in generic


def is_semantic_match(a, b):
    return jaccard_2gram(a, b) >= 0.30 and shares_long_substring(a, b, 3)


def sentinel_filter(value):
    sentinels = (app.NO_BACKGROUND, app.EMPTY_FOCUS, app.EMPTY_GAP)
    if hasattr(app, "EMPTY_CONSENSUS"):
        sentinels += (app.EMPTY_CONSENSUS,)
    text = str(value or "").strip()
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
    # 「高赞视角」只能指向知乎站内回答：人工整理快照里的媒体报道链接不得冒充。
    for entry in reading:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        url = safe_http_url(entry.get("url"))
        if title and url and app._is_zhihu_url(url):
            return {"title": title, "url": url}
    return None


def ext_ref(item):
    evidence = item.get("web_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "found":
        return []
    url = safe_http_url(evidence.get("url"))
    if not url:
        return []
    title = str(evidence.get("title") or evidence.get("source") or "站外公开信源").strip()
    return [{"title": title or "站外公开信源", "url": url}]


# ---------------------------------------------------------------------------
# 归档读取与辅助
# ---------------------------------------------------------------------------
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
    keep = {0, len(nodes) - 1}
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


def item_state(item):
    state = str(item.get("status") or item.get("type") or "verify")
    return state if state in VALID_STATES else "verify"


def build_archives_view(archives):
    """按日期倒序输出往期真实归档，每期只含归档里的真实字段。"""
    view = []
    for archive in sorted(archives, key=lambda a: a["_path_date"], reverse=True):
        date = archive["_path_date"]
        counts = {state: 0 for state in VALID_STATES}
        items = []
        for item in archive["items"]:
            if not isinstance(item, dict):
                continue
            state = item_state(item)
            counts[state] += 1
            entry = {"title": str(item.get("title") or ""), "status": state}
            url = safe_http_url(item.get("question_url"))
            if url:
                entry["url"] = url
            items.append(entry)
        view.append({
            "date": date,
            "date_label": date_label(date),
            "item_count": len(items),
            "status_counts": counts,
            "items": items,
        })
    return view


# ---------------------------------------------------------------------------
# 主构建
# ---------------------------------------------------------------------------
def build_timeline(min_days=2, with_related_past=False):
    aliases = load_aliases()
    alias_map = aliases.get("aliases") if isinstance(aliases.get("aliases"), dict) else {}
    splits = aliases.get("splits") if isinstance(aliases.get("splits"), list) else []
    hidden = set(str(v) for v in (aliases.get("hidden_topics") or []))
    # 一对 split 的两端都须隔离：若只标记 topic_q，另一端 question_q 会与
    # 第三方相似 qid 误合并，凭空造出假跨天话题（违反防幻觉红线）。
    split_qids = set()
    for pair in splits:
        if isinstance(pair, list) and len(pair) == 2:
            split_qids.add(str(pair[0]))
            split_qids.add(str(pair[1]))

    archives = load_archives()
    present_dates = sorted({str(a["_path_date"]) for a in archives})

    records = []
    for archive in sorted(archives, key=lambda a: a["_path_date"]):
        date = archive["_path_date"]
        for item in archive["items"]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            qid = question_id(item)
            stem = denoise_title(title)
            if qid and str(qid) in alias_map:
                forced = "alias:" + str(alias_map[str(qid)])
            elif qid:
                forced = "qid:" + str(qid)
            else:
                # 无 qid 的站外条目：主干完全一致视为同一事件的强身份证据，
                # 纯中文标题也能跨天聚合，且不引入任何人工种子。
                forced = "stem:" + stem if stem else ""
            split = bool(qid and str(qid) in split_qids)
            records.append({
                "date": date,
                "title": title,
                "stem": stem,
                "qid": qid,
                "forced": forced,
                "split": split,
                "state": item_state(item),
                "entities": hard_entities(title),
                "keywords": event_keywords(stem),
                "item": item,
            })

    uf = UnionFind(len(records), dates=[r["date"] for r in records])
    # 1) 强身份合并：相同 qid / 显式 alias / 完全相同的无 qid 主干。
    by_forced = {}
    for index, record in enumerate(records):
        key = record["forced"]
        if not key:
            continue
        if key in by_forced:
            uf.union(by_forced[key], index)
        else:
            by_forced[key] = index
    # 2) 跨天复合打分合并（仅不同日期、<= 14 天、过硬闸门）；split 条目保持独立。
    for left in range(len(records)):
        for right in range(left + 1, len(records)):
            if records[left]["split"] or records[right]["split"]:
                continue
            if uf.find(left) == uf.find(right):
                continue
            if cross_day_match(records[left], records[right]):
                uf.union(left, right)
    # 3) 可选 1-call 批量 LLM 语义归并（未配置 MODEL_API_KEY 或任何异常自动回落）。
    for group in link_topics_with_llm(records):
        anchor = group[0]
        for idx in group[1:]:
            if uf.find(anchor) != uf.find(idx):
                uf.union(anchor, idx)

    groups = {}
    for index, record in enumerate(records):
        groups.setdefault(uf.find(index), []).append(record)

    topics = []
    for members in groups.values():
        unique_dates = sorted({r["date"] for r in members})
        if len(unique_dates) < min_days:
            continue
        if any(str(r["qid"]) in hidden for r in members):
            continue

        forced_keys = {r["forced"] for r in members if r["forced"]}
        if len(forced_keys) == 1:
            key = next(iter(forced_keys))
            topic_id = key.split(":", 1)[1] if ":" in key else key
        else:
            shared = set.intersection(*[r["entities"] for r in members])
            anchor = sorted(shared)[0] if shared else (members[0]["stem"][:16] or "topic")
            topic_id = "entity:" + anchor
        if topic_id in hidden:
            continue

        nodes = []
        for record in members:
            item = record["item"]
            state = record["state"]
            nodes.append({
                "date": record["date"],
                "date_label": date_label(record["date"]),
                "rank": item.get("rank"),
                "title": record["title"],
                "question_id": record["qid"],
                "state": state,
                "status": state,
                "state_label": app.TYPE_NAMES.get(state, state),
                "hook": build_hook(item),
                "key_answer": key_answer(item),
                "ext_ref": ext_ref(item),
            })
        nodes.sort(key=lambda n: n["date"])
        nodes = deduplicate_nodes(nodes)
        status_flow = [n["status"] for n in nodes]
        state_changes = sum(
            1 for i in range(1, len(nodes))
            if nodes[i].get("state") != nodes[i - 1].get("state")
        )
        latest = next((node for node in reversed(nodes) if node.get("title")), {})
        real_qids = sorted({str(n["question_id"]) for n in nodes
                            if str(n.get("question_id") or "").isdigit()})
        primary_qid = real_qids[0] if real_qids else ""

        shared_entities = set.intersection(*[r["entities"] for r in members])
        if not shared_entities:
            all_entities = set().union(*[r["entities"] for r in members])
            shared_entities = set(sorted(all_entities)[:3])

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
            "status_flow": status_flow,
            "entities": sorted(shared_entities)[:6],
            "key_answer": latest.get("key_answer"),
            "ext_ref": latest.get("ext_ref") or [],
            "nodes": nodes,
        }
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
    if with_related_past and (app.ZHIHU_ACCESS_SECRET or app.MOCK_MODE):
        for topic in topics:
            past = fetch_related_past(topic, limit=3)
            if past:
                topic["related_past"] = past
    missing = missing_dates(present_dates)
    tracked_days = (
        (datetime.date.fromisoformat(max(present_dates))
         - datetime.date.fromisoformat(min(present_dates))).days + 1
        if present_dates else 0
    )
    return {
        "generated_at": app.now_str(),
        "archive_files": len(archives),
        "tracked_days": tracked_days,
        "missing_dates": missing,
        "topics": topics,
        "archives": build_archives_view(archives),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-days", type=int, default=2)
    parser.add_argument("--with-related-past", action="store_true")
    args = parser.parse_args(argv)
    result = build_timeline(max(1, args.min_days), with_related_past=args.with_related_past)
    atomic_write_json(TIMELINE_PATH, result)
    js_payload = app._js_safe_json(json.dumps(result, ensure_ascii=False))
    atomic_write_text(DEMO_TIMELINE_PATH, f"window.TIMELINE_DATA = {js_payload};")
    print(
        f"时间线构建完成：归档 {result['archive_files']} 个，"
        f"跨度 {result['tracked_days']} 天，话题 {len(result['topics'])} 个，"
        f"往期归档 {len(result['archives'])} 期"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
