# -*- coding: utf-8 -*-
"""
把「知识洞察 v2」合入真实项目的 app.py 与 demo/index.html。

设计原则：
  1. 每一处替换都用**逐字原文**做锚点，且断言「恰好出现 1 次」——锚点漂移立刻报错，
     不会静默改错地方。
  2. 先写到 merged/ 目录，不碰原文件；验证通过后才由调用方决定是否覆盖。
  3. 保留全部原有兜底/缓存/重试逻辑，只改「一条热榜怎么被解读」和「页面怎么渲染」。

用法：
  python merge_v2.py <项目目录> <输出目录>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


# ===========================================================================
# app.py —— 6 处替换
# ===========================================================================

APP_1_OLD = '''EMPTY_VIEWPOINT = "站内暂无明确该立场声音"
NO_BACKGROUND = "暂无解读。"
'''

APP_1_NEW = '''EMPTY_VIEWPOINT = "站内暂无明确该立场声音"   # 保留：历史缓存 / fallback.json 兼容
NO_BACKGROUND = "暂无解读。"                  # 保留：降级哨兵，不改

# ---- v2：三态分类（先判断这条属于哪一类，再决定填哪些字段）----
TYPE_DISPUTE = "dispute"        # 有真分歧
TYPE_CONSENSUS = "consensus"    # 已形成共识
TYPE_VERIFY = "verify"          # 待验证
VALID_TYPES = (TYPE_DISPUTE, TYPE_CONSENSUS, TYPE_VERIFY)

# ---- v2：分歧的性质（只对 dispute 有意义）----
VALID_CLAIM_TYPES = ("fact", "inference", "impression", "value")
CLAIM_TYPE_NAMES = {
    "fact": "可裁决",
    "inference": "推测对推测",
    "impression": "观感对观感",
    "value": "价值判断",
}
TYPE_NAMES = {
    TYPE_DISPUTE: "有真分歧",
    TYPE_CONSENSUS: "已形成共识",
    TYPE_VERIFY: "待验证",
}

EMPTY_FOCUS = "暂无聚焦问题。"
EMPTY_GAP = "暂无可补的拼图。"
EMPTY_CONSENSUS = ""            # consensus 允许为空：不是每条都有事实底座

BACKGROUND_MAX = 150           # 沿用原限制
FOCUS_MAX = 60
CONSENSUS_MAX = 80
SIDE_LABEL_MAX = 12
SIDE_CLAIM_MAX = 80
GAP_MAX = 100
'''

APP_2_OLD = '''# ---------------------------------------------------------------------------
# 大模型 Prompt（与任务书 4.7 一致，不要改动输出要求）
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = (
    "你是「热榜深潜简报」的内容编辑。请基于我提供的知乎站内回答素材，为一条热点生成简报内容。\\n\\n"
    "【热点标题】\\n{title}\\n\\n"
    "【候选回答素材】（编号 | 标题 | 摘要）\\n{candidates_text}\\n\\n"
    "【输出要求】\\n"
    "1. background：≤150 个中文字符，讲清事件是什么、来龙去脉，不要评价、不要复述标题。\\n"
    "2. viewpoints：support / oppose / undecided 三派各一句（每句 ≤60 字），"
    "必须能从上面的素材中找到依据；某派素材不足时，该字段填「站内暂无明确该立场声音」，"
    "不要凭空编造立场。\\n"
    "3. reading：从候选编号中选 2 个最值得延伸阅读的，返回编号 index 和一句推荐理由 reason"
    "（reason ≤30 字，说明“为什么值得点开”，不要复述标题）。\\n"
    "4. 只输出一个 JSON 对象，不要 markdown 代码块、不要解释文字、不要输出任何 URL。\\n\\n"
    "【输出 JSON 结构】\\n"
    '{{"background":"...","viewpoints":{{"support":"...","oppose":"...","undecided":"..."}},'
    ' "reading":[{{"index":1,"reason":"..."}},{{"index":2,"reason":"..."}}]}}'
)
'''

APP_2_NEW = '''# ---------------------------------------------------------------------------
# 大模型 Prompt（v2：先判定争议类型，再按类型填字段）
# 与「知识洞察 v2」契约一致；改动此段必须同步 process_item 与前端渲染。
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = (
    "你是「热榜深潜简报」的内容编辑。你的任务不是复述这条热点，"
    "而是判断这条热榜里的争议到底属于哪一种，并指出它还缺什么。\\n\\n"
    "【热点标题】\\n{title}\\n\\n"
    "【候选回答素材】（编号 | 标题 | 摘要）\\n{candidates_text}\\n\\n"
    "【第一步：判定这条热点的类型 type】\\n"
    "· dispute（有真分歧）：站内存在两种或以上可辨认的对立立场，双方都在为各自立场辩护。\\n"
    "· consensus（已形成共识）：站内对核心结论基本无异议，或只有一个声音。\\n"
    "· verify（待验证）：争议卡在事实/证据缺失上，或问题本身的前提就是错的，"
    "读者真正需要的是补齐一手材料或纠正前提。\\n"
    "注意：如果站内对某个立场只有一种声音，不要为了凑三派而把它拆成正反两方——"
    "请如实判为 consensus 或 verify。\\n\\n"
    "【第二步：按 type 填字段】\\n"
    "所有类型都必须填：\\n"
    "  · background：≤150 个中文字符，讲清事件是什么、来龙去脉。不评价、不复述标题。\\n"
    "  · focus：≤60 字，把这条热点里真正值得回答的那个问题抽出来。"
    "不要照抄标题，要指出争议的焦点在哪。\\n"
    "  · gap：≤100 字，说清「还缺哪一块拼图」——读者若想自己判断，需要补的是哪份材料、"
    "哪项数据、哪一方的回应。必须具体到可去找的东西，不要写「需要更多信息」。\\n\\n"
    "仅当 type=dispute 时额外填：\\n"
    "  · claim_type：这条分歧的性质，四选一：\\n"
    "      fact（可裁决）：双方对同一事实给出不同版本，补齐数据/官方口径即可判定谁对。\\n"
    "      inference（推测对推测）：双方都在推测内部机制/动机，谁都没有公开证据。\\n"
    "      impression（观感对观感）：双方基于各自观感下判断，缺客观指标。\\n"
    "      value（价值判断）：分歧源于价值观不同，再多事实也裁决不了。\\n"
    "  · sides：恰好 2 个，每个含 label（≤12 字，给这一派起个名字，如「技术派」「运力派」）"
    "和 claim（≤80 字，用这一派自己的逻辑复述其主张）。两派必须真的对立。\\n"
    "  · consensus：≤80 字，写双方都不否认的事实底座；若确实没有，填空字符串。\\n\\n"
    "仅当 type=consensus 或 verify 时：\\n"
    "  · consensus：≤80 字，写站内无争议的那个结论（verify 类型下写「已被证实的部分」）。\\n"
    "  · sides 与 claim_type 不要输出，或输出 null。\\n\\n"
    "【第三步：延伸阅读】\\n"
    "reading：从候选编号中选 2 个最值得点开的，返回 index 和 reason（≤30 字，"
    "说明「为什么值得点开」，不要复述标题）。\\n\\n"
    "【硬性要求】\\n"
    "1. 所有内容必须能在上面的素材中找到依据，不要编造立场、数据、机构名或人名。\\n"
    "2. 只输出一个 JSON 对象，不要 markdown 代码块、不要解释文字、不要输出任何 URL。\\n\\n"
    "【输出 JSON 结构】\\n"
    '{{"type":"dispute|consensus|verify",'
    '"claim_type":"fact|inference|impression|value 或 null",'
    '"background":"...","focus":"...",'
    '"consensus":"...",'
    '"sides":[{{"label":"...","claim":"..."}},{{"label":"...","claim":"..."}}],'
    '"gap":"...",'
    '"reading":[{{"index":1,"reason":"..."}},{{"index":2,"reason":"..."}}]}}'
)
'''

APP_3_OLD = '''def sanitize_viewpoint(value) -> str:
    value = str(value or "").strip()
    return value if value else EMPTY_VIEWPOINT
'''

APP_3_NEW = '''def sanitize_viewpoint(value) -> str:
    value = str(value or "").strip()
    return value if value else EMPTY_VIEWPOINT


# ---- v2：六字段清洗 ----
def _clip(value, limit: int) -> str:
    """按字符数截断，并去掉首尾空白。"""
    return str(value or "").strip()[:limit]


def sanitize_type(value) -> str:
    """type 必须是三态之一，认不出来一律按「待验证」处理（最保守的降级）。"""
    v = str(value or "").strip().lower()
    return v if v in VALID_TYPES else TYPE_VERIFY


def sanitize_claim_type(value, item_type: str) -> str:
    """claim_type 只在 dispute 下有意义；认不出来返回空串（前端不显示标签）。"""
    if item_type != TYPE_DISPUTE:
        return ""
    v = str(value or "").strip().lower()
    return v if v in VALID_CLAIM_TYPES else ""


def sanitize_sides(value, item_type: str) -> list:
    """
    只接受恰好 2 个成型阵营；不足 2 个则整体丢弃。

    这是关键的完整性闸门：声称「有真分歧」却给不出两派，说明模型没找到真分歧，
    宁可不显示，也不要展示一个假的两派对峙——那正是 v1 被诟病的地方。
    """
    if item_type != TYPE_DISPUTE:
        return []
    if not isinstance(value, list):
        return []
    out = []
    for raw in value[:2]:
        if not isinstance(raw, dict):
            continue
        label = _clip(raw.get("label"), SIDE_LABEL_MAX)
        claim = _clip(raw.get("claim"), SIDE_CLAIM_MAX)
        if label and claim:
            out.append({"label": label, "claim": claim})
    return out if len(out) == 2 else []
'''

APP_4_OLD = '''def process_item(rank: int, hot: dict) -> dict:
    """处理一条热点：搜索素材 → 大模型 → 组装。单条失败只降级该条，不中断简报。"""
    title = str(hot.get("Title") or "").strip()
    question_url = str(hot.get("Url") or "").strip()
    item = {
        "rank": rank,
        "title": title,
        "question_url": question_url,
        "background": NO_BACKGROUND,
        "viewpoints": {
            "support": EMPTY_VIEWPOINT,
            "oppose": EMPTY_VIEWPOINT,
            "undecided": EMPTY_VIEWPOINT,
        },
        "reading": [],
    }
    try:
        candidates = fetch_search(title, rank)
        if not candidates:
            raise UpstreamError("搜索无候选回答，按 FR-07/FR-04 降级")
        model_output = generate(title, build_candidates_text(candidates))

        background = str(model_output.get("background") or "").strip()[:150]
        item["background"] = background if background else NO_BACKGROUND

        viewpoints = model_output.get("viewpoints")
        if isinstance(viewpoints, dict):
            item["viewpoints"] = {
                "support": sanitize_viewpoint(viewpoints.get("support")),
                "oppose": sanitize_viewpoint(viewpoints.get("oppose")),
                "undecided": sanitize_viewpoint(viewpoints.get("undecided")),
            }

        item["reading"] = assemble_reading(model_output, candidates)
        _log(f"  第 {rank} 条生成成功：{title[:24]}")
    except Exception as exc:
        _log(f"  第 {rank} 条生成失败（单条降级，不影响整份简报）：{exc}")
    return item
'''

APP_4_NEW = '''def process_item(rank: int, hot: dict) -> dict:
    """处理一条热点：搜索素材 → 大模型 → 组装成 v2 六字段。单条失败只降级该条。"""
    title = str(hot.get("Title") or "").strip()
    question_url = str(hot.get("Url") or "").strip()
    item = {
        "rank": rank,
        "title": title,
        "question_url": question_url,
        # 降级态默认「待验证」：不确定时不假装有分歧
        "type": TYPE_VERIFY,
        "claim_type": "",
        "background": NO_BACKGROUND,
        "focus": EMPTY_FOCUS,
        "consensus": "",
        "sides": [],
        "gap": EMPTY_GAP,
        "reading": [],
        # 兼容字段：让旧缓存 / 旧前端读取时不至于 KeyError
        "viewpoints": {
            "support": EMPTY_VIEWPOINT,
            "oppose": EMPTY_VIEWPOINT,
            "undecided": EMPTY_VIEWPOINT,
        },
    }
    try:
        candidates = fetch_search(title, rank)
        if not candidates:
            raise UpstreamError("搜索无候选回答，按 FR-07/FR-04 降级")
        model_output = generate(title, build_candidates_text(candidates))

        item_type = sanitize_type(model_output.get("type"))
        sides = sanitize_sides(model_output.get("sides"), item_type)

        # 完整性闸门：说是分歧却凑不出两派 → 降级为待验证，并记日志
        if item_type == TYPE_DISPUTE and not sides:
            item_type = TYPE_VERIFY
            _log(f"  第 {rank} 条声明 dispute 但未给出两个成型阵营，降级为 verify")

        item["type"] = item_type
        item["claim_type"] = sanitize_claim_type(model_output.get("claim_type"), item_type)
        item["sides"] = sides

        background = _clip(model_output.get("background"), BACKGROUND_MAX)
        item["background"] = background if background else NO_BACKGROUND

        focus = _clip(model_output.get("focus"), FOCUS_MAX)
        item["focus"] = focus if focus else EMPTY_FOCUS

        consensus = _clip(model_output.get("consensus"), CONSENSUS_MAX)
        item["consensus"] = consensus if consensus else EMPTY_CONSENSUS

        gap = _clip(model_output.get("gap"), GAP_MAX)
        item["gap"] = gap if gap else EMPTY_GAP

        item["reading"] = assemble_reading(model_output, candidates)

        # 兼容字段：由 v2 字段回填，旧前端仍能渲染出大致正确的三派
        if item_type == TYPE_DISPUTE and sides:
            item["viewpoints"] = {
                "support": sides[0]["claim"],
                "oppose": sides[1]["claim"],
                "undecided": consensus or EMPTY_VIEWPOINT,
            }

        _log(f"  第 {rank} 条生成成功[{item_type}]：{title[:24]}")
    except Exception as exc:
        _log(f"  第 {rank} 条生成失败（单条降级，不影响整份简报）：{exc}")
    return item
'''

APP_5_OLD = '''    return {
        "date": today_str(),
        "source": "live",
        "generated_at": now_str(),
        "notice": notice,
        "items": items,
    }
'''

APP_5_NEW = '''    return {
        "date": today_str(),
        "source": "live",
        "generated_at": now_str(),
        "notice": notice,
        "digest": build_digest(items),
        "items": items,
    }
'''

APP_6_OLD = '''def mock_generate(title: str, candidates_text: str) -> str:
    """返回 JSON 字符串，走与真实大模型完全相同的解析与编号映射路径。"""
    return json.dumps({
        "background": f"这是 mock 模式自动生成的模拟背景，用于验证全链路，不代表真实事件解读：{title[:18]}。",
        "viewpoints": {
            "support": "模拟支持派观点：整体利大于弊，值得鼓励。",
            "oppose": "模拟反对派观点：风险被低估，需要更谨慎。",
            "undecided": "模拟中立声音：利弊取决于具体使用方式。",
        },
        "reading": [
            {"index": 1, "reason": "模拟推荐：支持方代表视角。"},
            {"index": 2, "reason": "模拟推荐：反对方代表视角。"},
        ],
    }, ensure_ascii=False)
'''

APP_6_NEW = '''def mock_generate(title: str, candidates_text: str) -> str:
    """返回 JSON 字符串，走与真实大模型完全相同的解析与编号映射路径。

    v2：三种 type 轮流出现，确保 mock 模式下三条渲染分支都能被肉眼验到。
    """
    # 用标题在 MOCK_HOT_TITLES 中的位置决定类型，保证每次启动分布完全一致：
    # 不能用 hash()——CPython 对 str 的 hash 每进程随机加盐，mock 数据会漂。
    try:
        _variant = MOCK_HOT_TITLES.index(title) % 3
    except ValueError:
        _variant = 0
    if _variant == 0:
        # 让 mock 也能覆盖四种 claim_type，否则肉眼验收时看不到标签差异
        _claims = [
            ("fact", "模拟支持派观点：整体利大于弊，值得鼓励。",
             "模拟反对派观点：风险被低估，需要更谨慎。"),
            ("inference", "模拟推测派：成本结构决定了这个结果。",
             "模拟反推测派：机制可能完全相反。"),
            ("impression", "模拟观感派：体感上明显变好了。",
             "模拟反观感派：体感上没什么变化。"),
            ("value", "模拟价值派：效率优先，其他都该让路。",
             "模拟反价值派：公平优先，效率不能这么换。"),
        ]
        _ct, _a, _b = _claims[MOCK_HOT_TITLES.index(title) // 3 % 4]
        payload = {
            "type": "dispute",
            "claim_type": _ct,
            "background": f"这是 mock 模式自动生成的模拟背景，用于验证全链路，不代表真实事件解读：{title[:18]}。",
            "focus": "模拟聚焦问题：这件事真正值得回答的是哪一点？",
            "consensus": "模拟共识：双方都承认这是一个需要被认真对待的问题。",
            "sides": [
                {"label": "支持派", "claim": _a},
                {"label": "反对派", "claim": _b},
            ],
            "gap": "模拟缺口：缺少可核对的官方口径与量化数据。",
        }
    elif _variant == 1:
        payload = {
            "type": "verify",
            "claim_type": None,
            "background": f"这是 mock 模式自动生成的模拟背景，用于验证全链路，不代表真实事件解读：{title[:18]}。",
            "focus": "模拟聚焦问题：这条争议缺的是哪一份一手材料？",
            "consensus": "模拟已被证实的部分：事件本身确实发生过。",
            "gap": "模拟缺口：缺少原始材料，现有讨论均为二手转述。",
        }
    else:
        payload = {
            "type": "consensus",
            "claim_type": None,
            "background": f"这是 mock 模式自动生成的模拟背景，用于验证全链路，不代表真实事件解读：{title[:18]}。",
            "focus": "模拟聚焦问题：这件事为什么没有争议却仍然很热？",
            "consensus": "模拟共识：站内对这一结论基本无异议。",
            "gap": "模拟缺口：缺少跨赛区/跨群体的统一比较标准。",
        }
    payload["reading"] = [
        {"index": 1, "reason": "模拟推荐：支持方代表视角。"},
        {"index": 2, "reason": "模拟推荐：反对方代表视角。"},
    ]
    return json.dumps(payload, ensure_ascii=False)
'''

APP_7_OLD = '''    return {
        "date": today_str(),
        "source": "demo",  # 与静态示例同等对待：链接不可点、徽章显示「示例数据」
        "generated_at": now_str() + "（模拟）",
        "notice": NOTICE_MOCK,
        "items": items,
    }
'''

APP_7_NEW = '''    return {
        "date": today_str(),
        "source": "demo",  # 与静态示例同等对待：链接不可点、徽章显示「示例数据」
        "generated_at": now_str() + "（模拟）",
        "notice": NOTICE_MOCK,
        "digest": build_digest(items),
        "items": items,
    }
'''

APP_8_OLD = '''# ---------------------------------------------------------------------------
# Mock 模式：无密钥/无网络时跑通全链路，页面会标注「模拟数据」
# ---------------------------------------------------------------------------
'''

APP_8_NEW = '''def build_digest(items: list) -> dict:
    """
    今日洞察：全部由后端确定性计算，不调用大模型。

    宁可少一点文采，也不要多一次幻觉机会——这是「知识洞察奖」最不该翻车的地方。
    """
    total = len(items)
    dispute = sum(1 for it in items if it.get("type") == TYPE_DISPUTE)
    consensus = sum(1 for it in items if it.get("type") == TYPE_CONSENSUS)
    verify = sum(1 for it in items if it.get("type") == TYPE_VERIFY)

    claim_counts = {k: 0 for k in VALID_CLAIM_TYPES}
    for it in items:
        ct = it.get("claim_type") or ""
        if ct in claim_counts:
            claim_counts[ct] += 1

    headline = (
        f"今天 {total} 条热榜里，真正吵起来的只有 {dispute} 条；"
        f"{consensus} 条根本没人在吵；剩下 {verify} 条卡在同一个地方——缺一手材料。"
    )

    insights = []
    decidable = claim_counts["fact"]
    if dispute:
        soft = dispute - decidable
        if decidable and soft:
            insights.append(
                f"{dispute} 条真分歧里，只有 {decidable} 条是补数据就能裁决的事实之争；"
                f"其余 {soft} 条分别属于推测对推测、观感对观感、价值判断——吵得再久也不会有答案。"
            )
        elif decidable:
            insights.append(
                f"{dispute} 条真分歧全部是补数据就能裁决的事实之争——"
                "这类争论有终点，缺的只是把材料补齐。"
            )
        else:
            insights.append(
                f"{dispute} 条真分歧没有一条能靠补数据裁决，全部是推测、观感或价值判断之争。"
            )
    if verify:
        insights.append(
            f"{verify} 条待验证的问题，缺的其实是同一样东西：一手材料。"
            "二手转述在热榜上替代了一手证据，这是这类话题反复上热榜却始终没有结论的原因。"
        )
    corrected = [
        it for it in items
        if "前提" in str(it.get("gap") or "") and "错" in str(it.get("gap") or "")
    ]
    if corrected:
        ranks = "、".join(f"第 {it['rank']} 条" for it in corrected)
        insights.append(
            f"{ranks}最值得注意：不是缺证据，而是问题前提本身错了。"
            "这类问题不该被「回答」，而该被「纠正」——这正是热榜最容易放过的一类。"
        )
    if consensus:
        insights.append(
            f"{consensus} 条已形成共识——它们在热榜上的热度来自关注度，不来自争议。"
        )

    return {
        "total": total,
        "dispute": dispute,
        "consensus": consensus,
        "verify": verify,
        "claim_counts": claim_counts,
        "headline": headline,
        "insights": insights[:3],
    }


# ---------------------------------------------------------------------------
# Mock 模式：无密钥/无网络时跑通全链路，页面会标注「模拟数据」
# ---------------------------------------------------------------------------
'''


# ===========================================================================
# demo/index.html —— 4 处替换
# ===========================================================================

HTML_1_OLD = '''  /* 观点对照：三派色块 */
  .viewpoints {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 12px;
  }
  .vp { border-radius: 8px; padding: 12px; font-size: 13px; line-height: 1.65; }
  .vp b { display: block; font-size: 12px; margin-bottom: 4px; letter-spacing: .1em; }
  .vp.support { background: #eef4ff; border: 1px solid #bed4ff; }
  .vp.support b { color: var(--blue); }
  .vp.oppose { background: #fff4e8; border: 1px solid #ffd8a8; }
  .vp.oppose b { color: var(--orange); }
  .vp.undecided { background: #f2f3f5; border: 1px solid #dcdfe5; }
  .vp.undecided b { color: #4e5969; }
'''

HTML_1_NEW = '''  /* v2：type / claim 标签 */
  .tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .tag {
    display: inline-block; padding: 1px 10px; border-radius: 6px;
    font-size: 12px; font-weight: 600; border: 1px solid transparent;
  }
  .tag.dispute { background: #fff4e8; color: #d25f00; border-color: #ffd8a8; }
  .tag.consensus { background: #e8ffea; color: #00801f; border-color: #aff0b5; }
  .tag.verify { background: #f0eaff; color: #5b34d6; border-color: #d6c8ff; }
  .tag.claim { background: #f7f8fa; color: #4e5969; border-color: #dcdfe5; font-weight: 500; }

  /* v2：聚焦问题 */
  .focus {
    margin-top: 14px; padding: 12px 14px; background: #f7f8fa;
    border-left: 3px solid var(--blue); border-radius: 0 8px 8px 0;
    font-size: 15px; font-weight: 600; line-height: 1.7;
  }

  /* v2：共识底座 */
  .consensus-box {
    margin-top: 12px; padding: 12px 14px; background: #f0fff4;
    border: 1px solid #c6f0d0; border-radius: 8px; font-size: 14px; color: #1a4d28;
    line-height: 1.75;
  }
  .consensus-box b { color: #00801f; font-size: 12px; letter-spacing: .1em; display: block; margin-bottom: 4px; }

  /* v2：两派对照（替代原三列） */
  .sides { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 12px; }
  .side { border-radius: 8px; padding: 12px 14px; font-size: 13.5px; line-height: 1.7; }
  .side.a { background: #eef4ff; border: 1px solid #bed4ff; }
  .side.b { background: #fff4e8; border: 1px solid #ffd8a8; }
  .side b { display: block; font-size: 12px; letter-spacing: .1em; margin-bottom: 5px; }
  .side.a b { color: var(--blue); }
  .side.b b { color: #d25f00; }

  /* v2：缺的那块拼图 */
  .gap {
    margin-top: 12px; padding: 12px 14px; background: #fffbf0;
    border: 1px dashed #f0c060; border-radius: 8px; font-size: 14px; color: #7a5200;
    line-height: 1.75;
  }
  .gap b { color: #ad6800; font-size: 12px; letter-spacing: .1em; display: block; margin-bottom: 4px; }

  /* v2：今日洞察（深色卡） */
  .digest {
    margin-top: 24px; background: #0f1114; color: #e8eaed;
    border-radius: 14px; padding: 26px 26px 22px;
  }
  .digest h2 {
    font-size: 13px; letter-spacing: .16em; color: #7b61ff; font-weight: 700;
    text-transform: uppercase; margin-bottom: 14px;
  }
  .digest .headline {
    font-size: 20px; line-height: 1.6; font-weight: 700; color: #fff;
    font-family: "Noto Serif SC", "Songti SC", "SimSun", serif;
  }
  .digest .counts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 20px 0 18px; }
  .digest .count-box { background: #1b1e23; border-radius: 10px; padding: 12px 14px; }
  .digest .count-box .n { font-size: 26px; font-weight: 700; line-height: 1.2; font-family: Georgia, serif; }
  .digest .count-box .l { font-size: 12px; color: #9aa0a6; margin-top: 2px; }
  .digest .count-box.dispute .n { color: #ff8f1f; }
  .digest .count-box.consensus .n { color: #3ddc84; }
  .digest .count-box.verify .n { color: #7b61ff; }
  .digest ul { list-style: none; border-top: 1px solid #2a2e35; padding-top: 16px; }
  .digest li {
    font-size: 14px; color: #c3c7cc; line-height: 1.8;
    padding-left: 18px; position: relative; margin-top: 10px;
  }
  .digest li:first-child { margin-top: 0; }
  .digest li::before { content: "▸"; position: absolute; left: 0; color: #7b61ff; }
'''

HTML_2_OLD = '''  @media (max-width: 600px) {
    .wrap { padding: 32px 16px 48px; }
    h1 { font-size: 28px; }
    .viewpoints { grid-template-columns: 1fr; }
  }
'''

HTML_2_NEW = '''  @media (max-width: 600px) {
    .wrap { padding: 32px 16px 48px; }
    h1 { font-size: 28px; }
    .sides { grid-template-columns: 1fr; }
    .digest { padding: 20px 18px 18px; }
    .digest .headline { font-size: 18px; }
    .digest .counts { grid-template-columns: 1fr; }
  }
'''

HTML_3_OLD = '''  <div id="notice" class="notice" hidden></div>
  <main id="cards"></main>
'''

HTML_3_NEW = '''  <div id="notice" class="notice" hidden></div>
  <section id="digest" class="digest" hidden></section>
  <main id="cards"></main>
'''

HTML_4_OLD = '''    var vp = item.viewpoints || {};
    var grid = el('div', 'viewpoints');
    [['support', 'vp support', '支持'], ['oppose', 'vp oppose', '反对'], ['undecided', 'vp undecided', '未定论']]
      .forEach(function (cfg) {
        var block = el('div', cfg[1]);
        block.appendChild(el('b', '', cfg[2]));
        block.appendChild(document.createTextNode(vp[cfg[0]] || ''));
        grid.appendChild(block);
      });
    card.appendChild(grid);
'''

HTML_4_NEW = '''    // ---- v2：按 type 分派渲染 ----
    var TYPE_LABEL = { dispute: '有真分歧', consensus: '已形成共识', verify: '待验证' };
    var CLAIM_LABEL = { fact: '可裁决', inference: '推测对推测', impression: '观感对观感', value: '价值判断' };

    if (item.type) {
      var tags = el('div', 'tags');
      tags.appendChild(el('span', 'tag ' + item.type, TYPE_LABEL[item.type] || item.type));
      if (item.claim_type) {
        tags.appendChild(el('span', 'tag claim',
          '分歧性质：' + (CLAIM_LABEL[item.claim_type] || item.claim_type)));
      }
      card.appendChild(tags);

      if (item.focus) {
        card.appendChild(el('div', 'focus', item.focus));
      }

      if (item.consensus) {
        var cb = el('div', 'consensus-box');
        cb.appendChild(el('b', '', '共识'));
        cb.appendChild(document.createTextNode(item.consensus));
        card.appendChild(cb);
      }

      if (item.sides && item.sides.length === 2) {
        var sidesGrid = el('div', 'sides');
        item.sides.forEach(function (s, i) {
          var box = el('div', 'side ' + (i === 0 ? 'a' : 'b'));
          box.appendChild(el('b', '', s.label));
          box.appendChild(document.createTextNode(s.claim));
          sidesGrid.appendChild(box);
        });
        card.appendChild(sidesGrid);
      }

      if (item.gap) {
        var gapBox = el('div', 'gap');
        gapBox.appendChild(el('b', '', '缺的那块拼图'));
        gapBox.appendChild(document.createTextNode(item.gap));
        card.appendChild(gapBox);
      }
    } else {
      // 兼容：旧缓存没有 type 字段，退回三派渲染
      var vp = item.viewpoints || {};
      var grid = el('div', 'viewpoints');
      [['support', 'vp support', '支持'], ['oppose', 'vp oppose', '反对'], ['undecided', 'vp undecided', '未定论']]
        .forEach(function (cfg) {
          var block = el('div', cfg[1]);
          block.appendChild(el('b', '', cfg[2]));
          block.appendChild(document.createTextNode(vp[cfg[0]] || ''));
          grid.appendChild(block);
        });
      card.appendChild(grid);
    }
'''

HTML_5_OLD = '''  var cardsEl = document.getElementById('cards');
  data.items.forEach(function (item) {
'''

HTML_5_NEW = '''  // ---- v2：今日洞察 ----
  (function renderDigest() {
    var d = data.digest;
    if (!d) return;
    var box = document.getElementById('digest');
    if (!box) return;
    box.appendChild(el('h2', '', "TODAY'S INSIGHT"));
    box.appendChild(el('p', 'headline', d.headline));

    var counts = el('div', 'counts');
    [['dispute', d.dispute, '条真分歧'],
     ['consensus', d.consensus, '条已共识'],
     ['verify', d.verify, '条待验证']].forEach(function (c) {
      var cell = el('div', 'count-box ' + c[0]);
      cell.appendChild(el('div', 'n', String(c[1])));
      cell.appendChild(el('div', 'l', c[2]));
      counts.appendChild(cell);
    });
    box.appendChild(counts);

    var ul = el('ul');
    (d.insights || []).forEach(function (t) { ul.appendChild(el('li', '', t)); });
    box.appendChild(ul);
    box.hidden = false;
  })();

  var cardsEl = document.getElementById('cards');
  data.items.forEach(function (item) {
'''


# ---- [F] 页脚披露补全（合规 P0）----
# 原页脚写「使用的开源组件仅为 Flask」= 否认了 README §8 与计划书 §6.2 已披露的 unslop，
# 属披露不完整（比错别字严重），必须补上。
HTML_6_OLD = '''    <p>参考与引用披露：本项目由 3 人大学生团队在 AI 辅助下完成开发；数据来自知乎开放平台 API（热榜 / 站内搜索 / 知乎直答），延伸阅读链接全部跳转回知乎站内；使用的开源组件仅为 Flask（BSD-3-Clause 许可证）。</p>'''
HTML_6_NEW = '''    <p>参考与引用披露：本项目由 3 人大学生团队在 AI 辅助下完成开发；数据来自知乎开放平台 API（热榜 / 站内搜索 / 知乎直答），延伸阅读链接全部跳转回知乎站内；后端框架为 Flask（BSD-3-Clause 许可证）；页面视觉经开源 skill unslop（作者 Damir Sokolovsky，CC BY 4.0）辅助审查，特此署名。</p>'''


APP_REPLACEMENTS = [
    ("app.py [1] 常量区", APP_1_OLD, APP_1_NEW),
    ("app.py [2] PROMPT_TEMPLATE", APP_2_OLD, APP_2_NEW),
    ("app.py [3] 清洗函数", APP_3_OLD, APP_3_NEW),
    ("app.py [4] process_item", APP_4_OLD, APP_4_NEW),
    ("app.py [5] build_brief 返回体", APP_5_OLD, APP_5_NEW),
    ("app.py [6] mock_generate", APP_6_OLD, APP_6_NEW),
    ("app.py [7] build_brief_mock 返回体", APP_7_OLD, APP_7_NEW),
    ("app.py [8] build_digest 插入", APP_8_OLD, APP_8_NEW),
]

HTML_REPLACEMENTS = [
    ("index.html [A] CSS 三派→六字段+洞察", HTML_1_OLD, HTML_1_NEW),
    ("index.html [B] media 断点", HTML_2_OLD, HTML_2_NEW),
    ("index.html [C] digest 容器", HTML_3_OLD, HTML_3_NEW),
    ("index.html [D] 卡片渲染分派", HTML_4_OLD, HTML_4_NEW),
    ("index.html [E] 洞察渲染", HTML_5_OLD, HTML_5_NEW),
    ("index.html [F] 页脚披露补全", HTML_6_OLD, HTML_6_NEW),
]


def apply(text: str, replacements, label: str) -> str:
    for name, old, new in replacements:
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"[FAIL] {name}: 锚点出现 {n} 次（期望 1 次）——原文已变动，请先核对")
        text = text.replace(old, new, 1)
        print(f"  OK  {name}")
    print(f"[{label}] 全部 {len(replacements)} 处替换成功")
    return text


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("用法: python merge_v2.py <项目目录> <输出目录>")

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "demo").mkdir(exist_ok=True)

    app_src = (src / "app.py").read_text(encoding="utf-8")
    idx_src = (src / "demo/index.html").read_text(encoding="utf-8")

    print(f"源 app.py      {len(app_src)} 字符 / {app_src.count(chr(10)) + 1} 行")
    print(f"源 index.html  {len(idx_src)} 字符 / {idx_src.count(chr(10)) + 1} 行\n")

    app_new = apply(app_src, APP_REPLACEMENTS, "app.py")
    idx_new = apply(idx_src, HTML_REPLACEMENTS, "index.html")

    (dst / "app.py").write_text(app_new, encoding="utf-8")
    (dst / "demo/index.html").write_text(idx_new, encoding="utf-8")

    print(f"\n输出 app.py      {len(app_new)} 字符 / {app_new.count(chr(10)) + 1} 行")
    print(f"输出 index.html  {len(idx_new)} 字符 / {idx_new.count(chr(10)) + 1} 行")

    import ast
    ast.parse(app_new)
    print("\nAST 解析通过：app.py 语法正确")


if __name__ == "__main__":
    main()
