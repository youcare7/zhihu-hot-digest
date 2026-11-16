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

EMPTY_VIEWPOINT = "站内暂无明确该立场声音"
NO_BACKGROUND = "暂无解读。"

# ---------------------------------------------------------------------------
# 大模型 Prompt（与任务书 4.7 一致，不要改动输出要求）
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = (
    "你是「热榜深潜简报」的内容编辑。请基于我提供的知乎站内回答素材，为一条热点生成简报内容。\n\n"
    "【热点标题】\n{title}\n\n"
    "【候选回答素材】（编号 | 标题 | 摘要）\n{candidates_text}\n\n"
    "【输出要求】\n"
    "1. background：≤150 个中文字符，讲清事件是什么、来龙去脉，不要评价、不要复述标题。\n"
    "2. viewpoints：support / oppose / undecided 三派各一句（每句 ≤60 字），"
    "必须能从上面的素材中找到依据；某派素材不足时，该字段填「站内暂无明确该立场声音」，"
    "不要凭空编造立场。\n"
    "3. reading：从候选编号中选 2 个最值得延伸阅读的，返回编号 index 和一句推荐理由 reason"
    "（reason ≤30 字，说明“为什么值得点开”，不要复述标题）。\n"
    "4. 只输出一个 JSON 对象，不要 markdown 代码块、不要解释文字、不要输出任何 URL。\n\n"
    "【输出 JSON 结构】\n"
    '{{"background":"...","viewpoints":{{"support":"...","oppose":"...","undecided":"..."}},'
    ' "reading":[{{"index":1,"reason":"..."}},{{"index":2,"reason":"..."}}]}}'
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
            return _chat(url, headers, payload)
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


def assemble_reading(model_output: dict, candidates: list) -> list:
    """把大模型返回的候选编号映射回搜索 API 的真实返回，并做二次 URL 校验。"""
    allowed = {c.get("Url") for c in candidates}
    reading = []
    picks = model_output.get("reading")
    if isinstance(picks, list):
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            idx = pick.get("index")
            if not isinstance(idx, int) or not (1 <= idx <= len(candidates)):
                continue  # 编号越界，丢弃
            src = candidates[idx - 1]
            url = src.get("Url")
            if not url or url not in allowed:
                continue  # 二次校验，丢弃
            reading.append({
                "title": str(src.get("Title", "")),
                "url": url,
                "reason": str(pick.get("reason", ""))[:60],
            })
    return reading[:3]


def sanitize_viewpoint(value) -> str:
    value = str(value or "").strip()
    return value if value else EMPTY_VIEWPOINT


# ---------------------------------------------------------------------------
# 单条处理与整份组装
# ---------------------------------------------------------------------------
def process_item(rank: int, hot: dict) -> dict:
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
        "items": items,
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
