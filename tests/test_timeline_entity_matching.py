# -*- coding: utf-8 -*-
"""合订本 v3 实体匹配升级的三条验收测试（任务书 3.3）。

1. 同事件不同问法跨天命中：去模板 + 硬实体匹配成功聚合，status_flow 按日期排序。
2. 同模板不同事件防误合并：两条均含「如何看待官方最新通报」但主体不同，
   被硬实体闸门拦截，topics == []。
3. 无交集双层降级契约：topics == []、len(archives) == archive_files，
   产物中无 curated_seed / tag-seed。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import build_timeline  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if ok:
        print("PASS", name)
    else:
        failures.append(name + (f": {detail}" if detail else ""))
        print("FAIL", name, detail)


def archive_item(title, state="verify", qid=""):
    return {
        "rank": 1,
        "title": title,
        "question_url": f"https://www.zhihu.com/question/{qid}" if qid else "https://www.ifeng.com/c/real-news",
        "type": state,
        "background": "有效解读",
        "focus": "可核验材料",
        "gap": "公开原始材料",
        "reading": [{"title": "代表回答", "url": "https://www.zhihu.com/question/1/answer/1"}],
        "web_evidence": None,
    }


def build_with(day1, day2, min_days=2):
    with tempfile.TemporaryDirectory(prefix="entity-match-") as temp:
        root = pathlib.Path(temp)
        archive_dir = root / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        aliases = root / "topic_aliases.json"
        aliases.write_text(
            json.dumps({"aliases": {}, "splits": [], "hidden_topics": []}),
            encoding="utf-8",
        )
        for date, items in (("2026-11-20", day1), ("2026-11-21", day2)):
            (archive_dir / f"{date}.json").write_text(
                json.dumps({"date": date, "source": "cache", "items": items}, ensure_ascii=False),
                encoding="utf-8",
            )
        old_archive, old_aliases = build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH
        build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH = archive_dir, aliases
        try:
            return build_timeline.build_timeline(min_days)
        finally:
            build_timeline.ARCHIVE_DIR, build_timeline.ALIASES_PATH = old_archive, old_aliases


# ---------------------------------------------------------------------------
# 1. 同事件不同问法跨天命中
# ---------------------------------------------------------------------------
hit = build_with(
    [archive_item("DeepSeek 完成第二轮融资 1050 亿元", "verify")],
    [archive_item("如何看待 DeepSeek 第二轮融资 1050 亿落地？", "consensus")],
)
check("同事件不同问法聚合出 1 个话题", len(hit["topics"]) == 1, f"实际 {len(hit['topics'])}")
if hit["topics"]:
    topic = hit["topics"][0]
    check("命中硬实体标签", "deepseek" in topic.get("entities", []), str(topic.get("entities")))
    check("status_flow 按日期升序且含状态跃迁",
          topic.get("status_flow") == ["verify", "consensus"], str(topic.get("status_flow")))
    check("节点按日期升序", [n["date"] for n in topic["nodes"]] == ["2026-11-20", "2026-11-21"])
    check("标题取归档原始标题（未经二次改写）",
          topic["title"] in ("DeepSeek 完成第二轮融资 1050 亿元",
                             "如何看待 DeepSeek 第二轮融资 1050 亿落地？"),
          topic["title"])
else:
    check("命中硬实体标签", False)
    check("status_flow 按日期升序且含状态跃迁", False)
    check("节点按日期升序", False)
    check("标题取归档原始标题（未经二次改写）", False)

check("去噪剥离问句模板",
      build_timeline.denoise_title("如何看待 DeepSeek 第二轮融资？").startswith("deepseek"),
      build_timeline.denoise_title("如何看待 DeepSeek 第二轮融资？"))
check("硬实体提取英文专名", "deepseek" in build_timeline.hard_entities("DeepSeek 完成融资"))
check("硬实体提取书名号专名", "幼儿园托班新政" in build_timeline.hard_entities("《幼儿园托班新政》落地"))
check("事件词轨过滤热榜泛词",
      "官方" not in build_timeline.event_keywords(build_timeline.denoise_title("官方最新通报")))

# ---------------------------------------------------------------------------
# 2. 同模板不同事件防误合并
# ---------------------------------------------------------------------------
blocked = build_with(
    [archive_item("如何看待 Apple 官方最新通报？", "verify", "301")],
    [archive_item("如何看待 Tesla 官方最新通报？", "consensus", "302")],
)
check("同模板不同事件被硬闸门拦截", blocked["topics"] == [], f"实际 {len(blocked['topics'])} 个话题")
check("被拦截时跨天间隔在窗口内但仍不合并",
      not build_timeline.cross_day_match(
          {"date": "2026-11-20", "entities": {"apple"}, "keywords": set(), "stem": "apple官方最新通报"},
          {"date": "2026-11-21", "entities": {"tesla"}, "keywords": set(), "stem": "tesla官方最新通报"}))
check("超过 14 天窗口不合并",
      not build_timeline.cross_day_match(
          {"date": "2026-11-01", "entities": {"deepseek"}, "keywords": set(), "stem": "deepseek融资"},
          {"date": "2026-11-21", "entities": {"deepseek"}, "keywords": set(), "stem": "deepseek融资"}))
check("同一天不参与跨天打分",
      not build_timeline.cross_day_match(
          {"date": "2026-11-21", "entities": {"deepseek"}, "keywords": set(), "stem": "deepseek融资"},
          {"date": "2026-11-21", "entities": {"deepseek"}, "keywords": set(), "stem": "deepseek融资"}))

# ---------------------------------------------------------------------------
# 3. 无交集双层降级契约
# ---------------------------------------------------------------------------
empty = build_with(
    [archive_item("胖东来搬离后原址所开新商场几乎倒闭", "verify")],
    [archive_item("神舟二十四号载人飞船瞄准发射", "consensus")],
)
check("无交集输入 topics 为空", empty["topics"] == [])
check("archives 期数等于 archive_files",
      len(empty["archives"]) == empty["archive_files"] == 2,
      f"archives={len(empty['archives'])} archive_files={empty['archive_files']}")
check("archives 按日期倒序",
      [a["date"] for a in empty["archives"]] == ["2026-11-21", "2026-11-20"],
      str([a["date"] for a in empty["archives"]]))
if empty["archives"]:
    first = empty["archives"][0]
    check("归档期刊含真实字段", set(("date", "item_count", "status_counts", "items")) <= set(first))
    check("status_counts 三态齐全",
          set(first["status_counts"]) == {"dispute", "consensus", "verify"},
          str(first["status_counts"]))
    check("归档条目标题为原始标题",
          first["items"][0]["title"] == "神舟二十四号载人飞船瞄准发射")
    check("归档条目状态为真实状态", first["items"][0]["status"] == "consensus")
payload = json.dumps(empty, ensure_ascii=False)
check("产物无 curated_seed", "curated_seed" not in payload)
check("产物无 tag-seed", "tag-seed" not in payload)
check("话题 origin 恒为 auto_archive",
      all(t.get("origin") == "auto_archive" for t in empty["topics"]))

if failures:
    print("\nFAILURES:", len(failures))
    for failure in failures:
        print("-", failure)
    raise SystemExit(1)
print("\nALL ENTITY MATCHING TESTS PASSED")
