# -*- coding: utf-8 -*-
"""热榜深潜简报 · Flask 后端

职责：调用知乎热榜 API / 搜索 API / 直答 API，组装当日简报，
      提供「当日缓存 + 静态兜底」，并通过 /data.js 把数据交给前端。

密钥只从环境变量读取，绝不写进代码。无密钥时可设 MOCK_MODE=1
用本地模拟数据跑通全链路（页面会明确标注「模拟数据」）。
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, Response, send_file

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
BASE_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEMO_DIR = BASE_DIR / "demo"
INDEX_PATH = DEMO_DIR / "index.html"
FALLBACK_PATH = DATA_DIR / "fallback.json"

# ---------------------------------------------------------------------------
# 配置（全部来自环境变量）
# ---------------------------------------------------------------------------
ZHIHU_BASE = os.environ.get("ZHIHU_BASE", "https://developer.zhihu.com").rstrip("/")
ZHIHU_ACCESS_SECRET = os.environ.get("ZHIHU_ACCESS_SECRET", "").strip()
MOCK_MODE = os.environ.get("MOCK_MODE", "0") == "1"
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))

# 备选通用大模型（仅当知乎直答不可用时才走，见 generate_brief）
MODEL_API_KEY = os.environ.get("MODEL_API_KEY", "").strip()
MODEL_BASE = os.environ.get("MODEL_BASE", "").rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "").strip()

HOT_LIST_LIMIT = 10          # 热榜固定取 10 条
SEARCH_COUNT = 10            # 每条热点的搜索素材上限
ZHIDA_MODEL = os.environ.get("ZHIDA_MODEL", "zhida-fast-1p5")

NOTICE_LIVE = "由知乎开放平台 API 实时生成，AI 内容仅供参考。"
NOTICE_FALLBACK = "当前为演示兜底数据，接口暂不可用。"
NOTICE_MOCK = "【模拟数据】当前为 mock 模式：全部内容来自本地示例，并非知乎 API 真实返回，链接不可点击。"

EMPTY_VIEWPOINT = "站内暂无明确该立场声音"   # 保留：历史缓存 / fallback.json 兼容
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
REASON_MAX = 30                # 延伸阅读推荐理由上限，与 PROMPT「reason ≤30 字」一致

# ---------------------------------------------------------------------------
# 大模型 Prompt（v2：先判定争议类型，再按类型填字段）
# 与「知识洞察 v2」契约一致；改动此段必须同步 process_item 与前端渲染。
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = (
    "你是「热榜深潜简报」的内容编辑。你的任务不是复述这条热点，"
    "而是判断这条热榜里的争议到底属于哪一种，并指出它还缺什么。\n\n"
    "【热点标题】\n{title}\n\n"
    "【候选回答素材】（编号 | 标题 | 摘要）\n{candidates_text}\n\n"
    "【第一步：判定这条热点的类型 type】\n"
    "· dispute（有真分歧）：站内存在两种或以上可辨认的对立立场，双方都在为各自立场辩护。\n"
    "· consensus（已形成共识）：站内对核心结论基本无异议，或只有一个声音。\n"
    "· verify（待验证）：争议卡在事实/证据缺失上，或问题本身的前提就是错的，"
    "读者真正需要的是补齐一手材料或纠正前提。\n"
    "注意：如果站内对某个立场只有一种声音，不要为了凑三派而把它拆成正反两方——"
    "请如实判为 consensus 或 verify。\n\n"
    "【第二步：按 type 填字段】\n"
    "所有类型都必须填：\n"
    "  · background：≤150 个中文字符，讲清事件是什么、来龙去脉。不评价、不复述标题。\n"
    "  · focus：≤60 字，把这条热点里真正值得回答的那个问题抽出来。"
    "不要照抄标题，要指出争议的焦点在哪。\n"
    "  · gap：≤100 字，说清「还缺哪一块拼图」。必须写成「读者明天就能去找的东西」，"
    "格式 = 具体材料名（精确到机构/文件/日期的优先，如「当事方 11 月财报」「期刊编辑部"
    "撤稿决定」）＋它可能的出处（哪个机构、哪份文件、哪个数据库）。"
    "若确实不存在任何可核验的公开材料，必须原样写「目前无公开可核验材料」，"
    "不要写「需要更多信息」「缺乏一手材料」这类无法行动的描述；"
    "写时间性边界（如「正片开播前无法判定」）也算合格。\n\n"
    "仅当 type=dispute 时额外填：\n"
    "  · claim_type：这条分歧的性质，四选一：\n"
    "      fact（可裁决）：双方对同一事实给出不同版本，补齐数据/官方口径即可判定谁对。\n"
    "      inference（推测对推测）：双方都在推测内部机制/动机，谁都没有公开证据。\n"
    "      impression（观感对观感）：双方基于各自观感下判断，缺客观指标。\n"
    "      value（价值判断）：分歧源于价值观不同，再多事实也裁决不了。\n"
    "  · sides：恰好 2 个，每个含 label（≤12 字，给这一派起个名字，如「技术派」「运力派」）"
    "和 claim（≤80 字，用这一派自己的逻辑复述其主张）。两派必须真的对立。\n"
    "  · consensus：≤80 字，写双方都不否认的事实底座；若确实没有，填空字符串。\n\n"
    "仅当 type=consensus 或 verify 时：\n"
    "  · consensus：≤80 字，写站内无争议的那个结论（verify 类型下写「已被证实的部分」）。\n"
    "  · sides 与 claim_type 不要输出，或输出 null。\n\n"
    "【第三步：延伸阅读】\n"
    "reading：从候选编号中最多选 2 个最值得点开的，返回 index 和 reason（≤30 字，"
    "说明「为什么值得点开」，不要复述标题）。素材不足或只有一条值得推荐时可以只选 1 个，不要凑数。\n\n"
    "【硬性要求】\n"
    "1. 所有内容必须能在上面的素材中找到依据，不要编造立场、数据、机构名或人名。\n"
    "2. 只输出一个 JSON 对象，不要 markdown 代码块、不要解释文字、不要输出任何 URL。\n\n"
    "【输出 JSON 结构】\n"
    '{{"type":"dispute|consensus|verify",'
    '"claim_type":"fact|inference|impression|value 或 null",'
    '"background":"...","focus":"...",'
    '"consensus":"...",'
    '"sides":[{{"label":"...","claim":"..."}},{{"label":"...","claim":"..."}}],'
    '"gap":"...",'
    '"reading":[{{"index":1,"reason":"..."}},{{"index":2,"reason":"..."}}]}}'
)


class UpstreamError(Exception):
    """上游（知乎 API / 大模型）调用失败。"""


def _log(msg: str) -> None:
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


try:  # 显式用北京时间，避免容器 UTC 导致缓存日期键/展示日期与读者差一天
    from zoneinfo import ZoneInfo
    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # 极简镜像缺 tzdata 时退化为固定 UTC+8
    CN_TZ = datetime.timezone(datetime.timedelta(hours=8))


def today_str() -> str:
    return datetime.datetime.now(CN_TZ).strftime("%Y-%m-%d")


def now_str() -> str:
    return datetime.datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 知乎开放平台 API
# ---------------------------------------------------------------------------
def zhihu_headers() -> dict:
    return {
        "Authorization": f"Bearer {ZHIHU_ACCESS_SECRET}",
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
    }


def zhihu_get(path: str, params: dict | None = None) -> dict:
    """GET 知乎开放平台接口，返回 Data 字段；任何异常都抛 UpstreamError。"""
    url = ZHIHU_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=zhihu_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except UpstreamError:
        raise
    except Exception as exc:
        raise UpstreamError(f"知乎接口请求失败 {path}：{exc}") from exc
    if body.get("Code") != 0:
        raise UpstreamError(
            f"知乎接口返回错误码 {body.get('Code')}（{body.get('Message', '')}）：{path}"
        )
    data = body.get("Data")
    return data if isinstance(data, dict) else {}


def api_hot_list() -> list:
    """FR-01：取当日热榜 10 条。"""
    data = zhihu_get("/api/v1/content/hot_list", {"Limit": HOT_LIST_LIMIT})
    items = data.get("Items") or []
    _log(f"热榜接口返回 {len(items)} 条")
    return items[:HOT_LIST_LIMIT]


def api_search(query: str, count: int = SEARCH_COUNT) -> list:
    """FR-02：站内搜索该热点的回答素材；Data 为 null 时返回空列表，不崩溃。"""
    data = zhihu_get("/api/v1/content/zhihu_search", {"Query": query, "Count": count})
    items = data.get("Items") or []
    return items


# ---------------------------------------------------------------------------
# 大模型调用（首选知乎直答，备选通用 OpenAI 兼容接口）
# ---------------------------------------------------------------------------
def _http_post_json(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _chat(url: str, headers: dict, payload: dict, label: str) -> str:
    try:
        body = _http_post_json(url, headers, payload)
    except Exception as exc:
        raise UpstreamError(f"{label} 请求失败：{exc}") from exc
    try:
        return body["choices"][0]["message"]["content"]
    except Exception as exc:
        raise UpstreamError(f"{label} 返回结构异常：{str(body)[:200]}") from exc


RETRY_WAIT = int(os.environ.get("RETRY_WAIT", "30"))   # 429 限流后的等待秒数
RETRY_TIMES = int(os.environ.get("RETRY_TIMES", "6"))  # 含首次共尝试次数

# 缓存有效期（小时）：过期后由访问流量惰性触发后台刷新（stale-while-revalidate），
# 不引入定时任务；<=0 时恢复「每日只生成一次」的旧行为。
CACHE_TTL_SECONDS = int(float(os.environ.get("CACHE_TTL_HOURS", "3")) * 3600)
_REFRESH_LOCK = threading.Lock()   # 保证同时只有一个后台刷新线程


def _chat_with_retry(url: str, headers: dict, payload: dict, label: str) -> str:
    """限流友好：遇 429 按 RETRY_WAIT 秒退避重试。简报每日只生成一次，慢一点没关系。"""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            return _chat(url, headers, payload, label)
        except UpstreamError as exc:
            if "429" in str(exc) and attempt < RETRY_TIMES:
                _log(f"{label} 触发限流（429），{RETRY_WAIT} 秒后重试（第 {attempt}/{RETRY_TIMES - 1} 次）")
                time.sleep(RETRY_WAIT)
                continue
            raise


def generate_brief(title: str, candidates_text: str) -> dict:
    """调大模型生成一条热点的简报内容，返回解析后的 dict。

    首选知乎直答（与热榜/搜索同一套密钥域名）；失败且配置了备选模型时再尝试备选。
    """
    prompt = PROMPT_TEMPLATE.format(title=title, candidates_text=candidates_text)
    messages = [{"role": "user", "content": prompt}]

    if not (MODEL_API_KEY and MODEL_BASE and MODEL_NAME):
        text = _chat_with_retry(
            ZHIHU_BASE + "/v1/chat/completions",
            zhihu_headers(),
            {"model": ZHIDA_MODEL, "messages": messages, "stream": False},
            "知乎直答",
        )
        return extract_json(text)

    try:
        text = _chat_with_retry(
            ZHIHU_BASE + "/v1/chat/completions",
            zhihu_headers(),
            {"model": ZHIDA_MODEL, "messages": messages, "stream": False},
            "知乎直答",
        )
        return extract_json(text)
    except UpstreamError as exc:
        _log(f"知乎直答调用失败，尝试备选通用大模型：{exc}")
        text = _chat(
            MODEL_BASE + "/v1/chat/completions",
            {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"},
            {"model": MODEL_NAME, "messages": messages, "stream": False},
            "通用大模型",
        )
        return extract_json(text)


def extract_json(text: str) -> dict:
    """严格解析大模型输出：先剥 markdown 围栏，再 json.loads，最后做类型检查。"""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
    except Exception as exc:
        raise UpstreamError(f"大模型输出不是合法 JSON：{str(exc)}；原文前 100 字：{cleaned[:100]}") from exc
    if not isinstance(obj, dict):
        raise UpstreamError("大模型输出不是 JSON 对象")
    return obj


# ---------------------------------------------------------------------------
# 数据组装（编号映射 + URL 白名单，任务书 4.6，不可简化）
# ---------------------------------------------------------------------------
def build_candidates_text(candidates: list) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        summary = re.sub(r"\s+", " ", str(c.get("ContentText") or ""))[:80]
        lines.append(f"{i} | {c.get('Title', '')} | {summary}")
    return "\n".join(lines)


def _is_zhihu_url(url) -> bool:
    """只允许 HTTP(S) 的知乎主域名及其子域名，排除 lookalike 域名。"""
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme in ("http", "https")
        and (host == "zhihu.com" or host.endswith(".zhihu.com"))
    )


def assemble_reading(model_output: dict, candidates: list) -> list:
    """候选编号映射 + 候选池校验 + 知乎主机名白名单；无效项丢弃且不补选。"""
    allowed = {
        c.get("Url") for c in candidates
        if _is_zhihu_url(c.get("Url"))
    }
    reading = []
    seen_urls = set()
    picks = model_output.get("reading")
    if isinstance(picks, list):
        for pick in picks:
            if len(reading) >= 2:
                break
            if not isinstance(pick, dict):
                continue
            idx = pick.get("index")
            if not isinstance(idx, int) or not (1 <= idx <= len(candidates)):
                continue  # 编号越界，丢弃
            src = candidates[idx - 1]
            url = src.get("Url")
            if not url or url not in allowed or not _is_zhihu_url(url) or url in seen_urls:
                continue  # 池外、非知乎域名或重复引用均丢弃
            reading.append({
                "title": str(src.get("Title", "")),
                "url": url,
                "reason": _clip(pick.get("reason", ""), REASON_MAX),
            })
            seen_urls.add(url)
    return reading[:2]


def sanitize_viewpoint(value) -> str:
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

    这是结构完整性闸门，不是事实核验器：它只检查两个阵营的 label / claim 非空，
    不能证明主张真实、两派真正对立，或观点已逐条绑定到某个候选回答。
    内容判断仍需读者点击原文复核。
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


# ---------------------------------------------------------------------------
# 单条处理与整份组装
# ---------------------------------------------------------------------------
def process_item(rank: int, hot: dict) -> dict:
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


def build_brief() -> dict:
    """实时生成整份简报（含 mock 分支）。"""
    if MOCK_MODE:
        return build_brief_mock()

    hot_items = api_hot_list()
    if not hot_items:
        raise UpstreamError("热榜接口返回 0 条")

    notice = NOTICE_LIVE
    if len(hot_items) < HOT_LIST_LIMIT:
        notice += f" 热榜接口本次仅返回 {len(hot_items)} 条，按实际条数展示。"

    items = [process_item(i + 1, hot) for i, hot in enumerate(hot_items)]

    # 收尾补跑：首轮仍处降级态的条目再试一轮。429 突发通常几十秒内过去，
    # 补跑能把「9/10」这类单条永久降级压到最低，且不改变 PRD 锁定的功能边界。
    degraded = [i for i, it in enumerate(items) if it["background"] == NO_BACKGROUND]
    if degraded:
        _log(f"首轮生成完成，{len(degraded)} 条降级，收尾补跑")
        time.sleep(RETRY_WAIT)  # 先让限流窗口过去再补跑
        for idx in degraded:
            items[idx] = process_item(items[idx]["rank"], hot_items[idx])
        ok = sum(1 for it in items if it["background"] != NO_BACKGROUND)
        _log(f"补跑完成：{ok}/{len(items)} 条生成成功")

    return {
        "date": today_str(),
        "source": "live",
        "generated_at": now_str(),
        "notice": notice,
        "digest": build_digest(items),
        "items": items,
    }


def build_digest(items: list) -> dict:
    """
    今日洞察：全部由后端确定性计算，不调用大模型。

    宁可少一点文采，也不要多一次幻觉机会——这是「知识洞察奖」最不该翻车的地方。
    """
    # 防御性清洗：build_digest 也可能直接读到缓存直写/旧版残留的脏数据
    # （正常链路由 sanitize_type / sanitize_sides / sanitize_claim_type 保证，此为第二道防线）
    _cleaned = []
    for _it in items:
        _it = dict(_it)
        if _it.get("type") == TYPE_DISPUTE and len(_it.get("sides") or []) != 2:
            _it["type"] = TYPE_VERIFY
        if _it.get("type") != TYPE_DISPUTE:
            _it["claim_type"] = ""
        _cleaned.append(_it)
    items = _cleaned

    total = len(items)
    dispute = sum(1 for it in items if it.get("type") == TYPE_DISPUTE)
    consensus = sum(1 for it in items if it.get("type") == TYPE_CONSENSUS)
    verify = sum(1 for it in items if it.get("type") == TYPE_VERIFY)

    claim_counts = {k: 0 for k in VALID_CLAIM_TYPES}
    for it in items:
        ct = it.get("claim_type") or ""
        if ct in claim_counts:
            claim_counts[ct] += 1

    decidable = claim_counts["fact"]
    headline = (
        f"本次 {total} 条热榜的检索样本中，模型判为真分歧 {dispute} 条、"
        f"已形成共识 {consensus} 条、待验证 {verify} 条。"
    )
    if dispute and decidable:
        # 第一得分点前置：评委第一眼就要看到「缺的拼图」，而不是只看三态计数。
        headline += f"其中 {decidable} 条争论只差一手数据就能裁决。"

    insights = []
    if dispute:
        soft = max(0, dispute - decidable)
        if decidable and soft:
            # 按 claim_counts 里实际非零的类别动态拼句：
            # 曾出现 impression=0 仍写「观感对观感」的情况——后端确定性文案不允许说谎。
            kinds = [
                CLAIM_TYPE_NAMES[k] for k in VALID_CLAIM_TYPES
                if k != "fact" and claim_counts[k]
            ]
            kinds_text = "、".join(kinds) if kinds else "非事实之争"
            insights.append(
                f"{dispute} 条真分歧里，{decidable} 条被标为可裁决的事实之争；"
                f"其余 {soft} 条属于{kinds_text}，分歧不只是一组待核数据，还涉及推测、观感或价值判断。"
            )
        elif decidable:
            insights.append(
                f"{dispute} 条真分歧均被标为可裁决的事实之争，"
                "更适合通过补充材料推进；最终仍应结合原文核验。"
            )
        else:
            # 同样按实际非零类别拼句：曾出现 impression=0 仍写「观感」的情况。
            kinds = [
                CLAIM_TYPE_NAMES[k] for k in VALID_CLAIM_TYPES
                if k != "fact" and claim_counts[k]
            ]
            kinds_text = "、".join(kinds) if kinds else "非事实之争"
            insights.append(
                f"{dispute} 条真分歧均被标为非事实类分歧，涉及{kinds_text}；"
                "这类判断需要结合具体主张与价值观差异理解。"
            )
    if verify:
        insights.append(
            f"{verify} 条待验证：可能缺一手材料、问题前提需纠正，或生成与结构校验未通过；"
            "请结合原问题、共识与缺口逐项判断。"
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
            f"{consensus} 条已形成共识——仅指本次检索样本中未见清晰对立，不代表全站民调。"
        )

    insights = insights[:3]
    if consensus and not any("已形成共识" in s for s in insights):
        _consensus_line = (
            f"{consensus} 条已形成共识——仅指本次检索样本中未见清晰对立，不代表全站民调。"
        )
        if insights:
            insights[-1] = _consensus_line
        else:
            insights = [_consensus_line]

    return {
        "total": total,
        "dispute": dispute,
        "consensus": consensus,
        "verify": verify,
        "claim_counts": claim_counts,
        "headline": headline,
        "insights": insights,
    }


# ---------------------------------------------------------------------------
# Mock 模式：无密钥/无网络时跑通全链路，页面会标注「模拟数据」
# ---------------------------------------------------------------------------
MOCK_HOT_TITLES = [
    "如何看待越来越多大学生用 AI 辅助完成课程作业？",
    "国产大模型接连降价，算力成本是否已经逼近拐点？",
    "「四天工作制」试点扩大，你支持在全国推广吗？",
    "年轻人「反向旅游」涌向小县城，是清醒还是逃避？",
    "新能源车续航突破 1000 公里，为什么仍遭质疑？",
    "AI 编程工具能独立完成需求，程序员还需要学写代码吗？",
    "多地中小学试点「春秋假」，落地的难点在哪里？",
    "短剧出海持续爆火，是文化输出还是流量生意？",
    "「数字游民」群体规模不断扩大，如何看待这种生活方式？",
    "首届人形机器人运动会落幕，距离产业化还有多远？",
]


def mock_hot_list() -> list:
    return [
        {"Title": t, "Url": f"https://www.zhihu.com/question/mock-{i}", "ThumbnailUrl": "", "Summary": ""}
        for i, t in enumerate(MOCK_HOT_TITLES, 1)
    ]


def mock_search(title: str, rank: int) -> list:
    stances = ["支持方", "反对方", "观望者"]
    return [
        {
            "Title": f"{title[:14]}｜{stances[k]}怎么看",
            "Url": f"https://www.zhihu.com/question/mock-{rank}/answer/mock-{rank}0{k}",
            "ContentText": f"这是 mock 模式生成的第 {k + 1} 条模拟摘要，仅用于本地联调，不代表真实知乎内容。",
            "VoteUpCount": 3200 - k * 600,
            "AuthorName": f"模拟答主{k + 1}",
            "AuthorityLevel": "",
        }
        for k in range(3)
    ]


def mock_generate(title: str, candidates_text: str) -> str:
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


def build_brief_mock() -> dict:
    _log("mock 模式：使用本地模拟数据（页面已标注「模拟数据」）")
    items = []
    for rank, hot in enumerate(mock_hot_list(), 1):
        item = process_item(rank, hot)  # 内部自动走 mock 搜索 / mock 大模型
        items.append(item)
    return {
        "date": today_str(),
        "source": "demo",  # 与静态示例同等对待：链接不可点、徽章显示「示例数据」
        "generated_at": now_str() + "（模拟）",
        "notice": NOTICE_MOCK,
        "digest": build_digest(items),
        "items": items,
    }


# fetch / generate 两个分发口，集中处理 mock 分支
def fetch_search(title: str, rank: int, count: int = SEARCH_COUNT) -> list:
    if MOCK_MODE:
        return mock_search(title, rank)
    return api_search(title, count)


def generate(title: str, candidates_text: str) -> dict:
    if MOCK_MODE:
        return extract_json(mock_generate(title, candidates_text))
    return generate_brief(title, candidates_text)


# ---------------------------------------------------------------------------
# 缓存 / 兜底
# ---------------------------------------------------------------------------
_GEN_LOCK = threading.Lock()   # 防止并发请求同时触发双倍生成（额度保护）


def cache_path() -> pathlib.Path:
    return DATA_DIR / f"brief_{today_str()}.json"


def cache_age_seconds() -> float | None:
    """当前当日缓存的年龄（秒）；无缓存返回 None。"""
    path = cache_path()
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def load_today_cache() -> dict | None:
    """FR-07：当天已有缓存则直接读取，秒开。"""
    path = cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log(f"缓存文件损坏，忽略并重新生成：{exc}")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    data["source"] = "cache"
    data["generated_at"] = "当日缓存"
    _log(f"命中当日缓存：{path.name}")
    return data


def generate_and_cache() -> dict:
    data = build_brief()
    DATA_DIR.mkdir(exist_ok=True)
    tmp = cache_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_path())  # 原子写入，避免并发读到半截文件
    _log(f"实时生成完成，共 {len(data['items'])} 条，已写入 {cache_path().name}")
    return data


def load_fallback() -> dict:
    """FR-08：任何异常下读取静态兜底，保证页面不白屏。"""
    data = json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise UpstreamError("fallback.json 结构不合法")
    data["source"] = "fallback"
    data["date"] = data.get("date") or today_str()
    data["generated_at"] = data.get("generated_at") or now_str()
    data["notice"] = NOTICE_FALLBACK
    return data


def _maybe_background_refresh() -> None:
    """缓存超过 TTL 则后台静默刷新（stale-while-revalidate）：
    本次请求照常秒回旧缓存，生成完成后后续访客自动拿到新版。"""
    if CACHE_TTL_SECONDS <= 0:
        return
    age = cache_age_seconds()
    if age is None or age <= CACHE_TTL_SECONDS:
        return
    if not _REFRESH_LOCK.acquire(blocking=False):
        return  # 已有刷新线程在跑
    _log(f"缓存已 {int(age // 60)} 分钟未更新（TTL {CACHE_TTL_SECONDS // 3600} 小时），触发后台自动刷新")
    threading.Thread(target=_background_refresh, daemon=True).start()


def _count_ok(data: dict) -> int:
    """简报中有背景解读（未降级）的条目数。"""
    return sum(1 for it in (data or {}).get("items", []) if it.get("background") != NO_BACKGROUND)


def _background_refresh() -> None:
    try:
        with _GEN_LOCK:
            age = cache_age_seconds()
            if age is not None and age <= CACHE_TTL_SECONDS:
                return  # 等锁期间已被别的请求刷新过
            # 防劣化保护：先读出旧缓存的完整度，刷新结果若劣于旧缓存（成功条目变少，
            # 如直答额度触顶导致大面积降级），丢弃新结果、沿用旧缓存——宁留旧全绿，不覆新残次。
            old_ok = 0
            if cache_path().exists():
                try:
                    old_ok = _count_ok(json.loads(cache_path().read_text(encoding="utf-8")))
                except Exception:
                    old_ok = 0
            _log("后台自动刷新开始")
            data = build_brief()
            new_ok = _count_ok(data)
            if old_ok > 0 and new_ok < old_ok:
                _log(f"刷新结果劣于现有缓存（{new_ok} < {old_ok} 条成功），丢弃本次结果，沿用旧缓存")
                return
            DATA_DIR.mkdir(exist_ok=True)
            tmp = cache_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(cache_path())
            _log(f"后台自动刷新完成：{new_ok} 条成功，已写入 {cache_path().name}")
    except Exception as exc:
        _log(f"后台自动刷新失败（旧缓存继续服务，页面不受影响）：{exc}")
    finally:
        _REFRESH_LOCK.release()


def get_brief() -> dict:
    """路由核心逻辑（任务书 4.8）：缓存 → 实时生成 → 兜底。

    生成全程加锁：首个请求生成期间，其余请求阻塞等待，拿到锁后
    先复查缓存（等待期间可能已生成完），杜绝并发双倍生成吃掉每日额度。
    缓存命中但超过 TTL 时，后台静默刷新，本次请求不受影响。
    """
    try:
        cached = load_today_cache()
        if cached:
            _maybe_background_refresh()
            return cached
        with _GEN_LOCK:
            cached = load_today_cache()
            if cached:
                return cached
            return generate_and_cache()
    except Exception as exc:
        _log(f"生成失败，切换到静态兜底：{exc}")
        return load_fallback()


# ---------------------------------------------------------------------------
# Flask 路由
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/")
def index():
    return send_file(INDEX_PATH)


@app.route("/data.js")
def data_js():
    brief = get_brief()
    payload = json.dumps(brief, ensure_ascii=False)
    return Response(f"window.BRIEF_DATA = {payload};", mimetype="application/javascript")


# ---------------------------------------------------------------------------
# 启动检查
# ---------------------------------------------------------------------------
def check_config() -> bool:
    if MOCK_MODE:
        _log("已开启 mock 模式：不会请求知乎 API，数据为本地模拟（页面已标注「模拟数据」）")
        return True
    if ZHIHU_ACCESS_SECRET:
        return True
    print("启动失败：缺少环境变量 ZHIHU_ACCESS_SECRET（知乎开放平台 Access Secret，等同密码，请勿泄露）。")
    print("请先在终端设置密钥，再重新运行：")
    print('  Windows PowerShell:  $env:ZHIHU_ACCESS_SECRET="你的密钥"')
    print("  Windows CMD:         set ZHIHU_ACCESS_SECRET=你的密钥")
    print("  macOS / Linux:       export ZHIHU_ACCESS_SECRET=你的密钥")
    print("如果暂时没有密钥，可先用 mock 模式体验全链路：")
    print('  Windows PowerShell:  $env:MOCK_MODE="1"')
    print("  Windows CMD:         set MOCK_MODE=1")
    print("  macOS / Linux:       export MOCK_MODE=1")
    return False


if __name__ == "__main__":
    if not check_config():
        sys.exit(1)
    DATA_DIR.mkdir(exist_ok=True)
    port = int(os.environ.get("PORT", "5000"))
    _log(f"热榜深潜简报已启动：http://127.0.0.1:{port}/")
    app.run(host="0.0.0.0", port=port)
