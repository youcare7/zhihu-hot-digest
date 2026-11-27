# -*- coding: utf-8 -*-
"""热榜深潜简报 · Flask 后端

职责：调用知乎热榜 API / 搜索 API / 直答 API，组装当日简报，
      提供「当日缓存 + 静态兜底」，并通过 /data.js 把数据交给前端。

密钥只从环境变量读取，绝不写进代码。无密钥时可设 MOCK_MODE=1
用本地模拟数据跑通全链路（页面会明确标注「模拟数据」）。
"""

from __future__ import annotations

import copy
import datetime
import ipaddress
import json
import math
import os
import pathlib
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, Response, send_file, send_from_directory


def _log(msg: str) -> None:
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    """读取整数型环境变量；空值/非法值一律回退默认值，绝不让一条配置拖垮启动。

    云控制台把 QUOTA_WEB_SEARCH_DAILY 之类留空、误填单位或写了中文说明，
    旧写法会在模块导入阶段直接 ValueError —— 整个服务起不来。
    配置错误应该降级并留日志，而不是变成线上 5xx。
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        _log(f"环境变量 {name}={raw!r} 不是合法整数，回退默认值 {default}")
        return default
    if minimum is not None and value < minimum:
        _log(f"环境变量 {name}={value} 低于下限 {minimum}，按 {minimum} 处理")
        return minimum
    return value


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    """读取浮点型环境变量，语义同 _env_int。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
        # nan/inf 能通过 float()，但后面 int(nan*3600) 会在导入阶段炸，必须挡住。
        if not math.isfinite(value):
            raise ValueError("not finite")
    except ValueError:
        _log(f"环境变量 {name}={raw!r} 不是合法数字，回退默认值 {default}")
        return default
    if minimum is not None and value < minimum:
        _log(f"环境变量 {name}={value} 低于下限 {minimum}，按 {minimum} 处理")
        return minimum
    return value


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
BASE_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEMO_DIR = BASE_DIR / "demo"
INDEX_PATH = DEMO_DIR / "index.html"
TIMELINE_PATH = DEMO_DIR / "timeline.js"
FALLBACK_PATH = DATA_DIR / "fallback.json"

# ---------------------------------------------------------------------------
# 配置（全部来自环境变量）
# ---------------------------------------------------------------------------
ZHIHU_BASE = os.environ.get("ZHIHU_BASE", "https://developer.zhihu.com").rstrip("/")
ZHIHU_ACCESS_SECRET = os.environ.get("ZHIHU_ACCESS_SECRET", "").strip()
MOCK_MODE = os.environ.get("MOCK_MODE", "0") == "1"
REQUEST_TIMEOUT = _env_int("REQUEST_TIMEOUT", 30, minimum=1)

# 备选通用大模型（仅当知乎直答不可用时才走，见 generate_brief）
MODEL_API_KEY = os.environ.get("MODEL_API_KEY", "").strip()
MODEL_BASE = os.environ.get("MODEL_BASE", "").rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "").strip()

# 可选远程时间线 JSON（未配置或拉取失败时自动回落本地 DATA_DIR/topic_timeline.json）
TIMELINE_REMOTE_URL = os.environ.get("TIMELINE_REMOTE_URL", "").strip()
TIMELINE_CACHE_TTL_SECONDS = _env_int("TIMELINE_CACHE_TTL_SECONDS", 600, minimum=0)

HOT_LIST_LIMIT = 10          # 热榜固定取 10 条
SEARCH_COUNT = 10            # 每条热点的搜索素材上限
ZHIDA_MODEL = os.environ.get("ZHIDA_MODEL", "zhida-fast-1p5")

NOTICE_LIVE = "由知乎开放平台 API 实时生成，AI 内容仅供参考。"
NOTICE_FALLBACK = "当前展示缓存快照，知乎实时接口暂不可用，恢复后自动切换。"
NOTICE_MOCK = "【模拟数据】当前为 mock 模式：全部内容来自本地示例，并非知乎 API 真实返回，链接不可点击。"
# 人工整理快照（如 v2.8 富化快照、静态兜底）：内容在管线结果上经编辑补充，
# 部分链接来自站外媒体、未经过 _is_zhihu_url 白名单——不能再沿用「实时生成」的说法。
NOTICE_CURATED = (
    "当前展示的是人工整理快照，并非程序实时生成："
    "部分链接来自站外媒体，未经过程序的知乎域名校验，请以原文为准。"
)

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

# ---- 全网证据核验（单条试点，任务书 2026-11-20）----
# 每份简报只选 1 条 verify 热点做核验，每次只展示 0–1 条站外结果；
# 站外链接只能来自知乎全网搜索接口的原始返回，大模型不得生成任何站外 URL。
WEB_SEARCH_COUNT = 5           # 每次全网检索取回的候选条数（卡片最多只展示 1 条）
WEB_QUERY_MAX = 80             # 检索词 = 热点标题 + gap 关键词，超长按句收束

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
    # 有些网关会把数字 Code 序列化成字符串 "0"；按值比较而不是按类型，
    # 避免把成功响应误判成「错误码 0」直接失败。
    if str(body.get("Code")).strip() != "0":
        raise UpstreamError(
            f"知乎接口返回错误码 {body.get('Code')}（{body.get('Message', '')}）：{path}"
        )
    data = body.get("Data")
    return data if isinstance(data, dict) else {}


def _items_of(data: dict) -> list:
    """Data.Items 必须是数组：上游契约漂移（返回对象/字符串）时按空列表处理。

    旧代码直接 items[:N] / for c in items，遇到 dict 会 KeyError/TypeError 整批失败，
    遇到字符串会在 c.get() 处静默单条降级，且日志看不出真实原因。
    """
    items = data.get("Items")
    return items if isinstance(items, list) else []


def api_hot_list() -> list:
    """FR-01：取当日热榜 10 条。"""
    if not _QUOTA.attempt("hot_list"):
        raise UpstreamError("热榜接口当日配额已达上限（配额守卫）")
    data = zhihu_get("/api/v1/content/hot_list", {"Limit": HOT_LIST_LIMIT})
    items = [x for x in _items_of(data) if isinstance(x, dict)]
    _log(f"热榜接口返回 {len(items)} 条")
    return items[:HOT_LIST_LIMIT]


def api_search(query: str, count: int = SEARCH_COUNT) -> list:
    """FR-02：站内搜索该热点的回答素材；Data 为 null 时返回空列表，不崩溃。"""
    if not _QUOTA.attempt("search"):
        raise UpstreamError("搜索接口当日配额已达上限（配额守卫）")
    data = zhihu_get("/api/v1/content/zhihu_search", {"Query": query, "Count": count})
    return [x for x in _items_of(data) if isinstance(x, dict)]


def api_web_search(query: str, count: int = WEB_SEARCH_COUNT) -> list:
    """试点（任务书 2026-11-20 §5.1）：全网搜索该热点的站外一手材料。

    接口事实已按任务书 §1 要求核实（来源：developer.zhihu.com 官方文档 JSON，
    2026-11-20 抓取）：GET /api/v1/content/global_search，Query 必填、Count 默认
    10 最大 20、SearchDB 可选（all/realtime/static）；Data.Items[] 每条含
    Title/Url/ContentText/AuthorName/EditTime 等字段；Code 非 0 即错误
    （10001 参数 / 20001 鉴权 / 30001 限流额度 / 30003 风控 / 90001 内部）。
    官方不支持用 Filter 限定知乎域名的反向用法——站内链接由站内搜索链路承担。
    """
    if not _QUOTA.attempt("web_search"):
        raise UpstreamError("全网搜索当日配额已达上限（配额守卫）")
    data = zhihu_get("/api/v1/content/global_search", {"Query": query, "Count": count})
    # 站外候选保留原始元素类型（build_web_evidence 自行跳过非 dict 条目并用
    # for/else 区分 no_result / error）；但 Items 非数组时必须归一为 []，
    # 否则遍历字符串会打出「返回 N 条」的误导日志并记成 error 而非 no_result。
    return _items_of(data)


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


RETRY_WAIT = _env_int("RETRY_WAIT", 30, minimum=0)   # 429 指数退避的单次等待上限（秒）
RETRY_TIMES = _env_int("RETRY_TIMES", 6, minimum=1)  # 含首次共尝试次数；至少 1 次
RETRY_BACKOFF_BASE = _env_int("RETRY_BACKOFF_BASE", 2, minimum=1)  # 首次 429 等待，之后按 2 倍递增
# 整批生成软预算（秒）：耗尽后停止发起新的重试/补跑，保留已生成的部分结果，
# 不杀线程；0 = 关闭预算。保留策略、冷却与劣化结果覆盖规则沿用 generate_and_cache 既有逻辑。
GEN_BUDGET_SECONDS = _env_int("GEN_BUDGET_SECONDS", 180, minimum=0)
# 富化 pass 的独立追加预算（秒）：主生成慢时整批预算会被耗尽，
# 富化与主生成共享 deadline 会一启动就被「预算耗尽」打断（065 线上实测 0 条富化）。
ENRICH_BUDGET_SECONDS = _env_int("ENRICH_BUDGET_SECONDS", 120, minimum=0)
_GEN_DEADLINE = 0.0  # monotonic 时间戳；0 表示本批不设预算；仅由 build_brief 写入/清除

# 缓存有效期（小时）：过期后由访问流量惰性触发后台刷新（stale-while-revalidate），
# 不引入定时任务；<=0 时恢复「每日只生成一次」的旧行为。
CACHE_TTL_SECONDS = int(_env_float("CACHE_TTL_HOURS", 6, minimum=0) * 3600)
# 刷新失败或结果被防劣化门丢弃后，暂停后续整批生成，避免访客流量形成重试风暴。
REFRESH_COOLDOWN_SECONDS = _env_int("REFRESH_COOLDOWN_SECONDS", 900, minimum=0)
# 增量刷新：按「知乎问题 ID → 规范化标题」复用当日已成功生成的条目，
# 只给新上榜 / 上次失败的条目调用直答，减少重复消耗直答配额。
# 0 = 关闭（每次整批重算，旧行为）。
INCREMENTAL_REFRESH = os.environ.get("INCREMENTAL_REFRESH", "1") != "0"

# ---- 配额韧性（2026-11-28）：残缺数据不顶掉满配画面 + 分层预算 + 榜单签名短路 ----
# 防劣化加权：同榜单重生成时，新简报字段覆盖分低于旧缓存超过容差则保留旧缓存。
# 榜单变了（新话题）时不设此门——新鲜度优先，不能为保字段覆盖而展示昨日新闻。
COVERAGE_TOLERANCE = _env_int("COVERAGE_TOLERANCE", 4, minimum=0)
# 榜单签名短路：热榜完全没变且旧缓存接近满配时跳过重生成（省主生成全部配额）。
# 旧缓存本身残缺时不短路——继续生成补字段，残缺画面不能被冻结。
COVERAGE_SKIP_REGEN_RATIO = _env_float("COVERAGE_SKIP_REGEN_RATIO", 0.6, minimum=0)
# 分层配额预算：单次简报内各阶段 zhida 调用上限（0 = 不限）。默认 45+20+15 = 80 = 日限，
# 防止主生成吃光全部配额后富化/核验静默归零（11-27 069 实测 punch 1/10、crosscheck 0）。
ZHIDA_MAIN_BUDGET = _env_int("ZHIDA_MAIN_BUDGET", 45, minimum=0)
ZHIDA_ENRICH_BUDGET = _env_int("ZHIDA_ENRICH_BUDGET", 20, minimum=0)
ZHIDA_CROSSCHECK_BUDGET = _env_int("ZHIDA_CROSSCHECK_BUDGET", 15, minimum=0)
CROSSCHECK_BUDGET_SECONDS = _env_int("CROSSCHECK_BUDGET_SECONDS", 90, minimum=0)
# 单条复用的最长年龄（小时）：超过则重新生成该条，避免快速演进的事件背景过旧；
# 0 = 当日内不限（复用池本身按北京时间自然日隔离，跨日自动失效）。
ITEM_REUSE_MAX_HOURS = _env_float("ITEM_REUSE_MAX_HOURS", 0, minimum=0)

# 跨线程共享的刷新状态与当日缓存内存镜像：只在这把短临界区锁内读写。
# 旧实现让请求线程 acquire、后台线程 release，threading.Lock 跨线程释放属未定义行为，
# 这里改为「标志位 + 短临界区」，所有加锁/解锁都发生在同一线程内。
_STATE_LOCK = threading.Lock()
_REFRESH_IN_PROGRESS = False
_REFRESH_NOT_BEFORE = 0.0          # monotonic 时间；仅进程内状态，不假装持久化
_BRIEF_CACHE_KEY = None            # (str(path), date_str, mtime_ns)
_BRIEF_CACHE_DATA = None


def _refresh_active() -> bool:
    with _STATE_LOCK:
        return _REFRESH_IN_PROGRESS


def _refresh_claim() -> bool:
    """短临界区内抢占刷新权；已有线程在跑或仍在冷却期返回 False。"""
    global _REFRESH_IN_PROGRESS
    with _STATE_LOCK:
        if _REFRESH_IN_PROGRESS:
            return False
        if _REFRESH_NOT_BEFORE > time.monotonic():
            return False
        _REFRESH_IN_PROGRESS = True
        return True


def _refresh_release() -> None:
    global _REFRESH_IN_PROGRESS
    with _STATE_LOCK:
        _REFRESH_IN_PROGRESS = False


class _RefreshStateView:
    """兼容旧测试：.locked() 暴露刷新状态；acquire/release 复用同一临界区。"""

    def locked(self) -> bool:
        return _refresh_active()

    def acquire(self, blocking: bool = True) -> bool:
        return _refresh_claim()

    def release(self) -> None:
        _refresh_release()


_REFRESH_LOCK = _RefreshStateView()


def _atomic_write_text(path: pathlib.Path, content: str) -> None:
    """使用同目录唯一临时文件 + os.replace() 原子落盘，避免固定 .tmp 并发写冲突。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            tmp_name = fh.name
            fh.write(content)
        os.replace(tmp_name, path)
    except Exception:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


# ---- 配额守卫：按接口、按自然日统计「请求尝试次数」，失败/超时/429 全部计入 ----
# 修订版方案 P1 前置（reviews/2026-11-19/zhihu_top15_optimization_plan_审查修订版_2026-11-19.md）：
# 接入任何新接口（如全网搜索）之前，必须先有按日计数与上限熔断，
# 防止补跑/重试连带打满官方配额（热榜 100 / 搜索 5000 / 直答 100，次/用户/天）。
QUOTA_LIMITS = {
    "hot_list": _env_int("QUOTA_HOT_LIST_DAILY", 100, minimum=0),
    "search": _env_int("QUOTA_SEARCH_DAILY", 2000, minimum=0),
    "zhida": _env_int("QUOTA_ZHIDA_DAILY", 80, minimum=0),
    "model_alt": _env_int("QUOTA_MODEL_ALT_DAILY", 500, minimum=0),
    # 全网搜索试点前保持 0 = 完全禁用；试点时按需调大并配合试点规范上线
    "web_search": _env_int("QUOTA_WEB_SEARCH_DAILY", 0, minimum=0),
}
QUOTA_STATE_PATH = DATA_DIR / "quota_counters.json"
QUOTA_GUARD_DISABLED = os.environ.get("QUOTA_GUARD_DISABLED", "0") == "1"


class QuotaGuard:
    """按接口、按自然日统计请求尝试次数（成功/失败/429 全部计入）。

    计数按日落盘（原子写），新进程实例读回同一天已累计次数；
    只保留当天，不假装跨日持久化。达上限后 attempt() 返回 False，
    调用方必须放弃本次请求，不得重试。
    """

    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._day = today_str()
        self._counts = {k: 0 for k in QUOTA_LIMITS}
        self._load()

    def _load(self):
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        if raw.get("date") == self._day and isinstance(raw.get("counts"), dict):
            for k in self._counts:
                v = raw["counts"].get(k)
                # bool 是 int 的子类：True 不能被当成「已用 1 次」，
                # 否则损坏/被篡改的计数文件会直接熔断接口（与编号守卫同类 bug）。
                if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                    self._counts[k] = v

    def _save(self):
        """把当日计数原子落盘；目录不存在时先创建。

        异常统一由 attempt() 兜住，这里保持纯 IO，便于测试注入真实磁盘错误。
        """
        _atomic_write_text(
            self._path,
            json.dumps({"date": self._day, "counts": self._counts}, ensure_ascii=False),
        )

    def attempt(self, kind: str) -> bool:
        """记录一次请求尝试并返回是否允许；False = 已达当日上限，调用方必须放弃。"""
        if QUOTA_GUARD_DISABLED or kind not in QUOTA_LIMITS:
            return True
        with self._lock:
            if today_str() != self._day:
                self._day = today_str()
                self._counts = {k: 0 for k in QUOTA_LIMITS}
            limit = QUOTA_LIMITS[kind]
            if self._counts[kind] >= limit:
                _log(f"配额守卫：{kind} 已达当日上限 {limit}，跳过本次调用")
                return False
            self._counts[kind] += 1
            # 守卫是保护性旁路，绝不能反过来把生成链路打断：磁盘只读/写满、
            # 目录不可创建时只记日志，内存计数仍准确、本次请求照常放行。
            # 曾因落盘抛 OSError 导致整批生成失败 → 后台冷却 15 分钟 → 线上长期兜底。
            try:
                self._save()
            except Exception as exc:
                _log(f"配额计数落盘失败（内存计数仍生效，不影响本次请求）：{exc}")
            return True

    def used(self, kind: str) -> int:
        with self._lock:
            if today_str() != self._day:
                return 0
            return self._counts.get(kind, 0)

    def exhausted(self, kind: str) -> bool:
        if QUOTA_GUARD_DISABLED or kind not in QUOTA_LIMITS:
            return False
        limit = QUOTA_LIMITS[kind]
        return self.used(kind) >= limit


_QUOTA = QuotaGuard(QUOTA_STATE_PATH)


def _chat_with_retry(url: str, headers: dict, payload: dict, label: str) -> str:
    """429 采用有界指数退避（RETRY_BACKOFF_BASE 起步、RETRY_WAIT 封顶）。

    旧实现每条都固定 sleep(RETRY_WAIT=30)，10 条串行只要各遇一次 429 就是 5 分钟，
    这正是线上冷启动 3–5 分钟的主因（上游本身瞬时返回）。
    整批生成预算 GEN_BUDGET_SECONDS 耗尽后停止新尝试，保留已有部分结果，不杀线程。
    每次尝试（含 429 重试）都经配额守卫计数；达当日上限立即放弃，不再重试。"""
    for attempt in range(1, RETRY_TIMES + 1):
        if _GEN_DEADLINE and time.monotonic() >= _GEN_DEADLINE:
            raise UpstreamError(f"{label} 整批生成预算耗尽，跳过剩余尝试")
        if not _QUOTA.attempt("zhida"):
            raise UpstreamError(f"{label} 当日配额已达上限，停止调用（配额守卫）")
        try:
            return _chat(url, headers, payload, label)
        except UpstreamError as exc:
            if "429" in str(exc) and attempt < RETRY_TIMES:
                wait = max(0, min(RETRY_WAIT, RETRY_BACKOFF_BASE * (2 ** (attempt - 1))))
                if _GEN_DEADLINE and time.monotonic() + wait > _GEN_DEADLINE:
                    raise UpstreamError(f"{label} 整批生成预算耗尽，停止等待重试") from exc
                _log(f"{label} 触发限流（429），{wait} 秒后重试（第 {attempt}/{RETRY_TIMES - 1} 次）")
                if wait:
                    time.sleep(wait)
                continue
            raise
    # 兜底：RETRY_TIMES 被改成 <=0 时循环空转，旧写法会静默返回 None，
    # 调用方 extract_json(None) 才炸；这里直接给出明确错误。
    raise UpstreamError(f"{label} 未执行任何调用（RETRY_TIMES={RETRY_TIMES}）")


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
        # 只有「调用失败」才应落到下面的备选模型分支。extract_json 解析失败
        # 同样抛 UpstreamError，但那是内容质量问题，必须与无备选配置分支一致地
        # 直接降级：不得换模型重跑、多烧一次 model_alt 配额，也不得把解析错误
        # 记成「直答调用失败」误导排查。故把解析移到 try 之外统一执行。
        text = _chat_with_retry(
            ZHIHU_BASE + "/v1/chat/completions",
            zhihu_headers(),
            {"model": ZHIDA_MODEL, "messages": messages, "stream": False},
            "知乎直答",
        )
    except UpstreamError as exc:
        _log(f"知乎直答调用失败，尝试备选通用大模型：{exc}")
        if not _QUOTA.attempt("model_alt"):
            raise UpstreamError("备选通用大模型当日配额已达上限（配额守卫）")
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
            # bool 是 int 的子类：True/False 不是合法编号，必须显式排除，
            # 否则模型没给出编号时程序会静默替它选中第 1/0 条候选。
            if isinstance(idx, bool) or not isinstance(idx, int) or not (1 <= idx <= len(candidates)):
                continue  # 非整数编号或越界，丢弃
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
    """按字符数安全收束：超长时优先保留完整句/分句，不直接切断句子。

    模型输出的自由文本字段本应是字符串；若返回 dict/list/数字，str() 会把
    Python repr 静默写进正文，还会让 _count_ok / 防劣化门误判为「生成成功」。
    脏类型一律按缺失处理，交给各调用点已有的降级哨兵，避免静默数据错。
    """
    if not isinstance(value, str):
        value = ""
    text = value.strip()
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""

    window = text[:limit]
    # 句号/问号/叹号可直接收尾；分号说明前后分句相对完整，收尾时改为句号。
    boundaries = ("。", "！", "？", "!", "?", "；", ";")
    cut = max(window.rfind(mark) for mark in boundaries)
    # 避免为了找标点只保留过短的开头；没有合适边界时显式标省略。
    if cut >= max(0, limit // 2 - 1):
        mark = window[cut]
        ending = "。" if mark in ("；", ";") else mark
        return (window[:cut] + ending).rstrip()

    kept = window[:max(0, limit - 1)].rstrip("，、：；,.;:!? \n\t")
    return kept + "…"


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
# 全网证据核验（单条试点，任务书 2026-11-20 §3）
# 每份简报只选 1 条 verify 且 gap 有效的热点；每次只展示 0–1 条站外结果；
# 站外链接只能来自接口原始返回，标题/链接原样保存不改写。
# ---------------------------------------------------------------------------
# 同一热点在同一次生成中不重复发起全网检索：首轮与收尾补跑之间用进程内
# 内存标记去重（_WEB_SEARCH_TRIED）；跨进程不保证（重启/并发进程可能各搜一次），
# 由配额守卫按日熔断兜底——此边界为任务书 §2 明确写明可接受。
_WEB_SEARCH_TRIED: dict = {}
# 每份简报的核验配额：按 found 结果消耗，最多 3 条满足条件（verify 或 fact 型 dispute 且 gap 有效）的热点。
_WEB_EVIDENCE_BUDGET = 3

_WEB_BOILERPLATE_RE = re.compile(
    r"[？?！!。，,、；;：:「」『』“”\"'‘’（）()【】\[\]《》<>·—\-…]+"
)
_WEB_TOKEN_RE = re.compile(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}")
_WEB_TIER_HIGH_DOMAINS = (
    "gov.cn", "edu.cn", "news.cn", "xinhuanet.com", "people.com.cn",
    "cctv.com", "chinanews.com.cn", "gmw.cn", "ce.cn", "legaldaily.com.cn",
    "gov.hk", "reuters.com", "apnews.com", "bbc.com", "nature.com", "science.org",
)
_WEB_TIER_MID_DOMAINS = (
    "thepaper.cn", "caixin.com", "jiemian.com", "yicai.com", "21jingji.com",
    "cls.cn", "163.com", "sina.com.cn", "qq.com", "sohu.com", "ifeng.com",
    "baidu.com", "36kr.com", "ithome.com", "wikipedia.org",
)


def _clean_web_terms(text: str) -> str:
    """剥离标点与多余空白，提炼用于全网检索的紧凑词串。"""
    cleaned = _WEB_BOILERPLATE_RE.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_web_query(title: str, gap: str = "") -> str:
    """构造全网检索词：核心标题 + gap 关键词（受 WEB_QUERY_MAX 约束）。"""
    core_title = _clean_web_terms(title) or str(title or "").strip()
    core_gap = _clean_web_terms(gap)
    if not core_gap:
        return _clip(core_title, WEB_QUERY_MAX)
    if len(core_title) + 1 + len(core_gap) <= WEB_QUERY_MAX:
        return f"{core_title} {core_gap}".strip()
    gap_budget = min(len(core_gap), max(16, WEB_QUERY_MAX // 2))
    title_budget = max(16, WEB_QUERY_MAX - gap_budget - 1)
    clipped_title = _clip(core_title, title_budget)
    rem = max(0, WEB_QUERY_MAX - len(clipped_title) - 1)
    clipped_gap = _clip(core_gap, rem) if rem > 0 else ""
    return _clip(f"{clipped_title} {clipped_gap}".strip(), WEB_QUERY_MAX)


def _web_tokens(text: str) -> set[str]:
    """提取中英文检索词元（含中文双字切片）用于相关性打分。"""
    tokens: set[str] = set()
    for part in _WEB_TOKEN_RE.findall(str(text or "").lower()):
        if part.isascii():
            tokens.add(part)
        elif len(part) <= 4:
            tokens.add(part)
        else:
            for i in range(len(part) - 1):
                tokens.add(part[i:i + 2])
    return tokens


def _score_web_candidate(it: dict, item_title: str, url: str, title: str, gap: str) -> int:
    """按域名权威层级 + 标题/缺口词元重合度对合法站外候选排序。"""
    host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    tier_score = 0
    if any(host == d or host.endswith("." + d) for d in _WEB_TIER_HIGH_DOMAINS):
        tier_score = 30
    elif any(host == d or host.endswith("." + d) for d in _WEB_TIER_MID_DOMAINS):
        tier_score = 15

    query_tokens = _web_tokens(f"{title} {gap}")
    cand_text = f"{item_title} {str(it.get('ContentText') or '')}"
    overlap = len(query_tokens & _web_tokens(cand_text)) if query_tokens else 0
    return tier_score + min(overlap, 10) * 3


def _is_valid_web_url(url) -> bool:
    """站外链接的独立校验（任务书 §3 url 规则）：scheme 必须 http(s)、host 合法，
    且非知乎域名——站内链接已由站内搜索/延伸阅读链路承担，不得重复展示。"""
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not host or "." not in host:
        return False
    # IP 字面量（loopback / 私网 / 云元数据地址等）不是可溯源的一手材料，
    # 且会产出可点击的无效链接；只接受真实域名。IPv6 的 [::1] 由上面的
    # "." not in host 已挡住，这里补齐 IPv4（含十进制/八进制写法的解析结果）。
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    return not (host == "zhihu.com" or host.endswith(".zhihu.com"))


def build_web_evidence(title: str, gap: str) -> dict:
    """对一条热点发起全网核验，返回可落盘的 web_evidence 对象。

    status 四值之一必落盘：found / no_result / error / skipped。
    任何失败只影响本字段（error/skipped），绝不使该条站内内容降级——
    核验是增强不是依赖。同一热点在同一次生成中只真正发起一次检索（no_result 时
    在时间预算内最多用精简标题重试一次）。
    """
    query = _build_web_query(title, gap)
    cached = _WEB_SEARCH_TRIED.get(title)
    if cached is not None:
        return dict(cached)  # 补跑复用首轮结论，不重复发起全网检索

    evidence = {"query": query, "checked_at": now_str()}
    try:
        items = api_web_search(query)
    except UpstreamError as exc:
        # 配额守卫拦截与接口失败分开记账：守卫拦截 = skipped（未发生上游请求），
        # 403/429/超时等接口失败 = error。
        evidence["status"] = "skipped" if "配额" in str(exc) else "error"
        _log(f"  全网核验未执行[{evidence['status']}]：{title[:24]}（{exc}）")
        _WEB_SEARCH_TRIED[title] = dict(evidence)
        return evidence
    except Exception as exc:  # 绝不因核验异常中断该条站内内容
        evidence["status"] = "error"
        _log(f"  全网核验异常[error]：{title[:24]}（{exc}）")
        _WEB_SEARCH_TRIED[title] = dict(evidence)
        return evidence

    if not items:
        fallback_query = _build_web_query(title, "")
        within_deadline = not _GEN_DEADLINE or time.monotonic() < _GEN_DEADLINE
        if fallback_query and fallback_query != query and within_deadline:
            try:
                retry_items = api_web_search(fallback_query)
            except Exception:
                retry_items = []
            if retry_items:
                items = retry_items
                evidence["query"] = fallback_query

    if not items:
        # 「没搜到」只说这一次检索没返回，绝不说「全网没有」（任务书 §4 禁语）
        evidence["status"] = "no_result"
        _WEB_SEARCH_TRIED[title] = dict(evidence)
        return evidence

    try:
        valid_items = []
        for idx, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            url = str(it.get("Url") or "").strip()
            item_title = str(it.get("Title") or "").strip()
            if not item_title or not _is_valid_web_url(url):
                continue  # 缺字段或链接未过独立校验：丢弃该条，不补选为虚假结果
            valid_items.append((idx, it, item_title, url))

        if valid_items:
            _, it, item_title, url = max(
                valid_items,
                key=lambda entry: (
                    _score_web_candidate(entry[1], entry[2], entry[3], title, gap),
                    -entry[0],
                ),
            )
            evidence["status"] = "found"
            evidence["title"] = item_title        # 接口原始标题，不改写
            evidence["url"] = url                 # 接口原始链接（含溯源参数），原样保存
            # 接口无「来源名」字段：取链接主机名作为 source，同为接口原始返回的一部分
            evidence["source"] = urllib.parse.urlparse(url).hostname or ""
            edit_time = it.get("EditTime")
            # 上游 EditTime 口径可能漂移（毫秒级 13 位 / 超大值），换算失败只能
            # 丢掉这个可选日期字段，绝不能击穿「核验绝不使该条降级」的契约。
            if isinstance(edit_time, int) and not isinstance(edit_time, bool):
                if 10 ** 11 <= edit_time < 10 ** 14:
                    edit_time = edit_time // 1000
                if 0 < edit_time < 2 ** 32:
                    try:
                        evidence["published_at"] = datetime.datetime.fromtimestamp(
                            edit_time, CN_TZ).strftime("%Y-%m-%d")
                    except (OSError, OverflowError, ValueError):
                        pass
        else:
            # 接口返回了条目但全部不可用（缺字段/链接非法）：结构异常记 error，
            # 与「接口正常返回 0 条」的 no_result 区分，便于排查上游变化。
            evidence["status"] = "error"
            _log(f"  全网核验返回 {len(items)} 条但均不可用（缺字段/链接非法）[error]：{title[:24]}")
    except Exception as exc:  # 任何未预期的字段异常也只影响本字段，不冒泡使整条降级
        evidence["status"] = "error"
        _log(f"  全网核验结果解析异常[error]：{title[:24]}（{exc}）")

    _WEB_SEARCH_TRIED[title] = dict(evidence)
    return evidence


# ---------------------------------------------------------------------------
# 单条处理与整份组装
# ---------------------------------------------------------------------------
def _base_item(rank: int, hot: dict) -> dict:
    """降级态骨架：任何条目在生成失败/预算耗尽时都能安全渲染，不编造内容。"""
    raw_url = str(hot.get("Url") or "").strip()
    return {
        "rank": rank,
        "title": str(hot.get("Title") or "").strip(),
        "question_url": raw_url if _is_zhihu_url(raw_url) else "",
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


def process_item(rank: int, hot: dict) -> dict:
    """处理一条热点：搜索素材 → 大模型 → 组装成 v2 六字段。单条失败只降级该条。"""
    item = _base_item(rank, hot)
    title = item["title"]
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

        # 全网证据核验：verify 或 fact 型 dispute（且 gap 有效）触发；每份简报最多消耗
        # 3 个 found 配额。任何异常只影响 web_evidence 字段，绝不使该条站内内容降级；
        # 补跑时复用首轮结论，不重复检索。
        needs_web = (
            item_type == TYPE_VERIFY
            or (item_type == TYPE_DISPUTE and item["claim_type"] == "fact")
        )
        if needs_web and item["gap"] and item["gap"] != EMPTY_GAP:
            global _WEB_EVIDENCE_BUDGET
            if title in _WEB_SEARCH_TRIED:
                item["web_evidence"] = dict(_WEB_SEARCH_TRIED[title])
            elif _WEB_EVIDENCE_BUDGET > 0:
                ev = build_web_evidence(title, item["gap"])
                item["web_evidence"] = ev
                if ev.get("status") == "found":
                    _WEB_EVIDENCE_BUDGET -= 1

        # 兼容字段：由 v2 字段回填，旧前端仍能渲染出大致正确的三派
        if item_type == TYPE_DISPUTE and sides:
            item["viewpoints"] = {
                "support": sides[0]["claim"],
                "oppose": sides[1]["claim"],
                "undecided": consensus or EMPTY_VIEWPOINT,
            }

        item["kanshan"] = _build_kanshan(item_type, item["focus"], item["gap"], rank)
        item["item_generated_at"] = now_str()

        _log(f"  第 {rank} 条生成成功[{item_type}]：{title[:24]}")
        # 立即入池：即使本批之后被防劣化门丢弃，这次配额也不白花
        _pool_put([item])
    except Exception as exc:
        _log(f"  第 {rank} 条生成失败（单条降级，不影响整份简报）：{exc}")
    return item


# ---------------------------------------------------------------------------
# 增量刷新：当日条目复用池
# 复用来源 = 当日缓存（load_today_cache 已排除模拟/坏缓存）+ 当日条目池
# data/item_pool_<日期>.json。条目池在每条生成成功时立即落盘，所以即便整批
# 结果被防劣化门丢弃、进程被 SIGTERM 杀掉，已消耗配额换来的条目也不会白费。
# ---------------------------------------------------------------------------
_QID_RE = re.compile(r"/question/(\d+)")
_POOL_LOCK = threading.Lock()
_POOL_MAX_ITEMS = 200  # 单日上限：热榜一天也就几十个不同问题，防止异常增长


def item_pool_path() -> pathlib.Path:
    return DATA_DIR / f"item_pool_{today_str()}.json"


def _norm_title(title) -> str:
    return re.sub(r"\s+", "", str(title or "")).strip()


def _item_keys(title, url) -> list:
    """复用键：优先知乎问题 ID（标题被编辑也能对上），其次规范化标题。"""
    keys = []
    if _is_zhihu_url(url):
        m = _QID_RE.search(str(url))
        if m:
            keys.append("q:" + m.group(1))
    t = _norm_title(title)
    if t:
        keys.append("t:" + t)
    return keys


def _parse_cn_time(value) -> datetime.datetime | None:
    m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", str(value or ""))
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)
    except ValueError:
        return None


def _is_reusable(item, fallback_time: str = "") -> bool:
    """只复用「健康」条目：有真实背景、有标题、三态合法；dispute 必须两派成型。"""
    if not isinstance(item, dict):
        return False
    if not str(item.get("title") or "").strip():
        return False
    bg = item.get("background")
    if not isinstance(bg, str) or not bg.strip() or bg == NO_BACKGROUND:
        return False
    if item.get("type") not in VALID_TYPES:
        return False
    if item.get("type") == TYPE_DISPUTE and len(item.get("sides") or []) != 2:
        return False
    # 人工整理条目（带站外原问题 / 延伸阅读链接）不得混进管线生成的简报
    q = str(item.get("question_url") or "").strip()
    if q and not _is_zhihu_url(q):
        return False
    for r in item.get("reading") or []:
        if isinstance(r, dict) and str(r.get("url") or "").strip() \
                and not _is_zhihu_url(str(r.get("url")).strip()):
            return False
    if ITEM_REUSE_MAX_HOURS > 0:
        born = _parse_cn_time(item.get("item_generated_at") or fallback_time)
        if born is None:
            return False  # 无法判断年龄时宁可重算，不让旧内容无限续命
        age_h = (datetime.datetime.now(CN_TZ) - born).total_seconds() / 3600
        if age_h > ITEM_REUSE_MAX_HOURS:
            return False
    return True


def _pool_load() -> list:
    path = item_pool_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        _log(f"条目池读取失败（下次将重新生成）：{exc}")
        return []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        _log(f"条目池 JSON 无效（下次将重新生成）：{exc}")
        return []
    if not isinstance(raw, dict) or raw.get("date") != today_str():
        return []
    items = raw.get("items")
    if not isinstance(items, list):
        _log(f"条目池结构无效（下次将重新生成）：{path.name}")
        return []
    return [it for it in items if isinstance(it, dict)]


def _pool_put(items: list) -> None:
    """把健康条目写进当日条目池（同键覆盖为最新版本）。尽力而为，绝不抛异常。"""
    if MOCK_MODE or not INCREMENTAL_REFRESH:
        return  # mock 条目绝不能进入真实模式的复用池
    fresh = [copy.deepcopy(it) for it in items if _is_reusable(it)]
    if not fresh:
        return
    try:
        with _POOL_LOCK:
            pool = _pool_load()
            new_keys = set()
            for it in fresh:
                new_keys.update(_item_keys(it.get("title"), it.get("question_url")))
            # 同键旧版本剔除，再把新版本追加到末尾（末尾 = 最新）
            pool = [
                it for it in pool
                if not (set(_item_keys(it.get("title"), it.get("question_url"))) & new_keys)
            ]
            pool.extend(fresh)
            pool = pool[-_POOL_MAX_ITEMS:]
            _atomic_write_text(
                item_pool_path(),
                json.dumps({"date": today_str(), "items": pool}, ensure_ascii=False),
            )
    except Exception as exc:
        _log(f"条目池落盘失败（不影响本次生成，只是下次少复用）：{exc}")


_REUSE_ENRICH_FIELDS = ("punch", "vote", "quotes", "lou", "crosscheck", "web_evidence")


def _merge_reuse(cached, pooled: dict) -> dict:
    """池条目（较新正文）为基底，缓存里已有的富化/核验字段只补缺、不覆盖。

    生成中途失败时池里可能只有未富化版本；旧实现直接覆盖缓存，会把上一版
    punch/lou/crosscheck 整体回退。字段级合并保证已花配额换来的组件不丢。
    """
    merged = dict(pooled)
    if isinstance(cached, dict):
        for field in _REUSE_ENRICH_FIELDS:
            if not merged.get(field) and cached.get(field):
                merged[field] = copy.deepcopy(cached[field])
    return merged


def _build_reuse_index() -> dict:
    """键 → 可复用条目。条目池提供较新正文，当日缓存补齐富化/核验字段。"""
    if MOCK_MODE or not INCREMENTAL_REFRESH:
        return {}
    index = {}
    cached = load_today_cache() or {}
    cache_time = str(cached.get("generated_at") or "")
    for it in cached.get("items") or []:
        if _is_reusable(it, cache_time):
            for k in _item_keys(it.get("title"), it.get("question_url")):
                index[k] = it
    for it in _pool_load():
        if not _is_reusable(it):
            continue
        for k in _item_keys(it.get("title"), it.get("question_url")):
            index[k] = _merge_reuse(index.get(k), it)
    return index


def _lookup_reuse(index: dict, hot: dict):
    if not index:
        return None
    for k in _item_keys(hot.get("Title"), str(hot.get("Url") or "").strip()):
        if k in index:
            return index[k]
    return None


def _reuse_item(old: dict, rank: int, hot: dict) -> dict:
    """复用旧条目：内容原样保留，只按本次热榜刷新排名/标题/问题链接。"""
    item = copy.deepcopy(old)
    base = _base_item(rank, hot)
    item["rank"] = rank
    if base["title"]:
        item["title"] = base["title"]
    if base["question_url"]:
        item["question_url"] = base["question_url"]
    item.setdefault("item_generated_at", "")
    if not isinstance(item.get("kanshan"), dict):
        item["kanshan"] = _build_kanshan(item["type"], item.get("focus", ""), item.get("gap", ""), rank)
    return item


def build_brief() -> dict:
    """实时生成整份简报（含 mock 分支）。"""
    global _GEN_DEADLINE, _WEB_EVIDENCE_BUDGET
    if MOCK_MODE:
        return build_brief_mock()

    # 每份简报重置全网核验状态：最多 3 条 found 配额、同一热点只检索一次。
    # 进程内内存状态，不假装跨进程持久化（任务书 §2 明确此边界可接受）。
    _WEB_SEARCH_TRIED.clear()
    _WEB_EVIDENCE_BUDGET = 3

    # 整批软预算：耗尽后 _chat_with_retry 停止新尝试、补跑跳过，已生成条目照常保留；
    # 不杀线程，冷却与劣化结果覆盖规则仍由 generate_and_cache 的既有逻辑负责。
    _GEN_DEADLINE = (time.monotonic() + GEN_BUDGET_SECONDS) if GEN_BUDGET_SECONDS > 0 else 0.0
    try:
        hot_items = api_hot_list()
        if not hot_items:
            raise UpstreamError("热榜接口返回 0 条")

        notice = NOTICE_LIVE
        if len(hot_items) < HOT_LIST_LIMIT:
            notice += f" 热榜接口本次仅返回 {len(hot_items)} 条，按实际条数展示。"

        # 增量刷新：先认领可复用条目（并先扣掉它们已占用的全网核验配额），
        # 再只为剩余条目走「搜索 → 直答」。
        reuse_index = _build_reuse_index()
        items = [None] * len(hot_items)
        for i, hot in enumerate(hot_items):
            old = _lookup_reuse(reuse_index, hot)
            if old is None:
                continue
            item = _reuse_item(old, i + 1, hot)
            items[i] = item
            ev = item.get("web_evidence")
            if isinstance(ev, dict):
                _WEB_SEARCH_TRIED[item["title"]] = dict(ev)
                if ev.get("status") == "found":
                    _WEB_EVIDENCE_BUDGET = max(0, _WEB_EVIDENCE_BUDGET - 1)
        reused = sum(1 for it in items if it is not None)
        if reused:
            _log(f"增量刷新：复用 {reused} 条，新生成 {len(hot_items) - reused} 条")
        zhida_main_start = _QUOTA.used("zhida")
        for i, hot in enumerate(hot_items):
            if items[i] is None:
                if ZHIDA_MAIN_BUDGET and _QUOTA.used("zhida") - zhida_main_start >= ZHIDA_MAIN_BUDGET:
                    items[i] = _base_item(i + 1, hot)
                    _log(f"主生成分层预算耗尽（{ZHIDA_MAIN_BUDGET} 次），第 {i + 1} 条降级保留，配额留给富化/核验")
                    continue
                items[i] = process_item(i + 1, hot)

        # 收尾补跑：首轮仍处降级态的条目再试一轮。429 突发通常几十秒内过去，
        # 补跑能把「9/10」这类单条永久降级压到最低，且不改变 PRD 锁定的功能边界。
        # 不再无条件 sleep(RETRY_WAIT)：_chat_with_retry 内部已有指数退避，
        # 且失败原因常是配额/超时/坏 JSON，等 30 秒纯属重复惩罚（旧实现每次冷启动白等）。
        degraded = [i for i, it in enumerate(items) if it["background"] == NO_BACKGROUND]
        if degraded and (not _GEN_DEADLINE or time.monotonic() < _GEN_DEADLINE):
            _log(f"首轮生成完成，{len(degraded)} 条降级，收尾补跑")
            for idx in degraded:
                if ZHIDA_MAIN_BUDGET and _QUOTA.used("zhida") - zhida_main_start >= ZHIDA_MAIN_BUDGET:
                    _log("主生成分层预算耗尽，跳过后续补跑，配额留给富化/核验")
                    break
                items[idx] = process_item(items[idx]["rank"], hot_items[idx])
            ok = sum(1 for it in items if it["background"] != NO_BACKGROUND)
            _log(f"补跑完成：{ok}/{len(items)} 条生成成功")
        elif degraded:
            _log(f"首轮生成完成，{len(degraded)} 条降级；预算已耗尽，跳过补跑并保留现有结果")

        # 互动组件富化（锐评/站队/吃瓜/楼上导读）：补跑之后统一处理；
        # 单条失败只关组件不降级条目，配额耗尽自动提前结束。
        # 富化使用独立追加预算：主生成耗尽整批预算后仍可执行。
        _extend_deadline_for_enrichment()
        _run_enrichment_pass(items)
        # 跨平台核验（微博情绪/知乎切面/事实信源）：依赖 web_search 配额（默认 0），
        # 配额为 0 时本 pass 静默跳过，不影响任何已有功能。
        _extend_deadline_for_crosscheck()
        _run_crosscheck_pass(items)
        # 富化与跨平台核验后的版本回写条目池，供后续增量刷新复用
        _pool_put(items)

        return {
            "date": today_str(),
            "source": "live",
            "generated_at": now_str(),
            "notice": notice,
            "digest": build_digest(items),
            "items": items,
        }
    finally:
        _GEN_DEADLINE = 0.0


# ---------------------------------------------------------------------------
# 互动组件富化 pass（2026-11-26）：主生成完成后补采锐评/站队/吃瓜/楼上导读。
# 这些创作性字段此前只存在于人工富化快照，管线不产出导致功能静默关闭；
# 现按缺补采：每条有效条目最多 2 次直答调用，配额耗尽提前结束，
# 任何失败只关闭对应组件，绝不使条目本身降级。
# ---------------------------------------------------------------------------
PUNCH_MAX = 40
VOTE_LINE_MAX = 60
QUOTE_MAX = 30
LOU_ANGLE_MAX = 60
LOU_TIP_MAX = 50
LOU_PERSONA_MAX = 50
LOU_PERSONAS = ("考研党", "上班族", "业内人士")


def _build_enrich_prompt(item: dict, want_vote: bool) -> str:
    """按条目类型拼装富化 prompt：站队/吃瓜字段只给真分歧（两派成型）的条目。"""
    sides = item.get("sides") or []
    lines = [
        "你是「热榜深潜简报」的互动文案编辑。一条榜单条目已完成事实分析（如下），",
        "请基于这些材料写组件文案：所有文案只能做表达加工与观点提炼，",
        "不得新增事实、数据、机构名、人名，不得输出任何 URL。",
        "",
        f"【标题】{item.get('title')}",
        f"【类型】{TYPE_NAMES.get(item.get('type'), '')}",
        f"【背景】{item.get('background')}",
        f"【焦点】{item.get('focus')}",
    ]
    if item.get("consensus"):
        lines.append(f"【共识底座】{item['consensus']}")
    if want_vote:
        lines.append(f"【甲方·{sides[0]['label']}】{sides[0]['claim']}")
        lines.append(f"【乙方·{sides[1]['label']}】{sides[1]['claim']}")
    gap = item.get("gap")
    if gap and gap != EMPTY_GAP:
        lines.append(f"【待补拼图】{gap}")
    lines += [
        "",
        "【输出 JSON 结构】只输出一个 JSON 对象，不要 markdown 代码块、不要解释文字：",
    ]
    fields = [
        '"punch":"≤40 字一句话锐评：有观点有锋利度，但必须能从材料推出，不喊口号、不编数据"',
    ]
    if want_vote:
        fields.append(
            '"vote":{"blue":"≤60 字，站在甲方立场说一句话（用自己的逻辑，不复述 claim 原文）",'
            '"orange":"≤60 字，站在乙方立场说一句话",'
            '"melon":"≤60 字，旁观者吃瓜吐槽一句，轻松但贴着事实"}'
        )
        fields.append('"quotes":["恰好 2 条吃瓜金句，每条 ≤30 字，口语化、像评论区高赞"]')
    fields.append(
        '"lou":{"angle":"≤60 字：给一个大家都在表层争吵、但还没人提过的切入角度",'
        '"tips":["恰好 3 条写作提纲（每条 ≤50 字），第一条必须是可直接引用的开篇金句"],'
        '"personas":{"考研党":"≤50 字代入视角","上班族":"≤50 字代入视角","业内人士":"≤50 字代入视角"}}'
    )
    lines.append("{" + ",".join(fields) + "}")
    return "\n".join(lines)


def _sanitize_enrichment(raw: dict, item: dict) -> dict:
    """校验并裁剪富化输出：结构不合格或超长截断后为空的一律丢弃该字段，
    脏类型交给 _clip 按缺失处理，绝不把 Python repr 写进正文。"""
    out = {}
    punch = _clip(raw.get("punch"), PUNCH_MAX)
    if punch:
        out["punch"] = punch
    sides = item.get("sides") or []
    if item.get("type") == TYPE_DISPUTE and len(sides) == 2:
        vote = raw.get("vote")
        if isinstance(vote, dict):
            blue = _clip(vote.get("blue"), VOTE_LINE_MAX)
            orange = _clip(vote.get("orange"), VOTE_LINE_MAX)
            melon = _clip(vote.get("melon"), VOTE_LINE_MAX)
            if blue and orange and melon:
                out["vote"] = {"blue": blue, "orange": orange, "melon": melon}
        quotes = raw.get("quotes")
        if isinstance(quotes, list):
            cleaned = [q for q in (_clip(x, QUOTE_MAX) for x in quotes) if q]
            if len(cleaned) >= 2:
                out["quotes"] = cleaned[:2]
    lou = raw.get("lou")
    if isinstance(lou, dict):
        angle = _clip(lou.get("angle"), LOU_ANGLE_MAX)
        tips_raw = lou.get("tips")
        tips = []
        if isinstance(tips_raw, list):
            tips = [t for t in (_clip(x, LOU_TIP_MAX) for x in tips_raw) if t][:3]
        personas_raw = lou.get("personas")
        personas = {}
        if isinstance(personas_raw, dict):
            for name in LOU_PERSONAS:
                value = _clip(personas_raw.get(name), LOU_PERSONA_MAX)
                if value:
                    personas[name] = value
        if angle and tips and len(personas) == len(LOU_PERSONAS):
            out["lou"] = {"angle": angle, "tips": tips, "personas": personas}
    return out


def _enrich_item(item: dict) -> bool:
    """单条富化。返回 False 表示配额/预算硬约束命中，调用方应停止整个 pass。"""
    if item.get("background") == NO_BACKGROUND:
        return True  # 降级条目没有可加工的材料，保持降级、跳过
    sides = item.get("sides") or []
    want_vote = item.get("type") == TYPE_DISPUTE and len(sides) == 2
    # lou（楼上导读）也必须纳入完整性：旧实现只看 punch，模型漏了 lou 时
    # 第一次调用即判定成功，之后 need=False 永远不再补采，组件静默永久缺失。
    need = (not item.get("punch") or not item.get("lou")
            or (want_vote and not (item.get("vote") and len(item.get("quotes") or []) == 2)))
    if not need:
        return True
    messages = [{"role": "user", "content": _build_enrich_prompt(item, want_vote)}]
    for attempt in (1, 2):
        try:
            text = _chat_with_retry(
                ZHIHU_BASE + "/v1/chat/completions",
                zhihu_headers(),
                {"model": ZHIDA_MODEL, "messages": messages, "stream": False},
                "富化直答",
            )
            got = _sanitize_enrichment(extract_json(text), item)
        except UpstreamError as exc:
            if "配额" in str(exc) or "预算" in str(exc):
                return False  # 硬约束：后续条目也会撞墙，停止整个 pass
            if attempt == 1:
                continue  # 坏 JSON / 网络抖动允许重试一次
            _log(f"  第 {item.get('rank')} 条富化失败（组件保持关闭，条目不受影响）：{exc}")
            return True
        except Exception as exc:
            # 头部构造等未预期异常同样只关组件（与 process_item 的宽容边界一致），
            # 富化绝不允许反过来让整条简报失败。
            _log(f"  第 {item.get('rank')} 条富化异常（组件保持关闭，条目不受影响）：{exc}")
            return True
        if got.get("punch"):
            item["punch"] = got["punch"]
        if want_vote and got.get("vote"):
            item["vote"] = got["vote"]
        if want_vote and got.get("quotes"):
            item["quotes"] = got["quotes"]
        if got.get("lou"):
            item["lou"] = got["lou"]
        if (item.get("punch") and item.get("lou")
                and (not want_vote or (item.get("vote") and item.get("quotes")))):
            return True
        if attempt == 2:
            _log(f"  第 {item.get('rank')} 条富化输出缺字段，重试后仍不完整，组件保持关闭")
            return True
    return True


def _extend_deadline_for_enrichment() -> None:
    """给富化 pass 追加独立预算。主生成可能已耗尽整批 GEN_BUDGET_SECONDS，
    共享 deadline 会让富化第一次调用就抛「预算耗尽」并整体停止（065 实测）。"""
    global _GEN_DEADLINE
    if _GEN_DEADLINE:
        _GEN_DEADLINE = time.monotonic() + ENRICH_BUDGET_SECONDS


def _extend_deadline_for_crosscheck() -> None:
    """给 crosscheck pass 追加独立预算，避免共享富化后的 deadline 导致全量跳过。"""
    global _GEN_DEADLINE
    if _GEN_DEADLINE and CROSSCHECK_BUDGET_SECONDS > 0:
        _GEN_DEADLINE = time.monotonic() + CROSSCHECK_BUDGET_SECONDS


def _run_enrichment_pass(items: list) -> None:
    """主生成完成后统一富化。mock 不联网直接跳过；逐条推进，
    配额耗尽提前结束——已生成条目照常服务，缺的只是可选组件。"""
    if MOCK_MODE or not items:
        return
    enriched = 0
    zhida_start = _QUOTA.used("zhida")
    for item in items:
        if ZHIDA_ENRICH_BUDGET and _QUOTA.used("zhida") - zhida_start >= ZHIDA_ENRICH_BUDGET:
            _log(f"富化分层预算耗尽（{ZHIDA_ENRICH_BUDGET} 次），提前结束富化 pass（已富化 {enriched} 条锐评）")
            return
        if not _enrich_item(item):
            _log(f"富化配额/预算耗尽，提前结束富化 pass（已富化 {enriched} 条锐评）")
            return
        if item.get("punch"):
            enriched += 1
    _log(f"富化 pass 完成：{enriched}/{len(items)} 条带锐评组件")


# ---------------------------------------------------------------------------
# 跨平台核验 crosscheck pass（2026-11-26）：059 线上快照的全功能对照中唯一
# 没有任何管线产出过的字段（此前只存在于人工富化数据）。用知乎全网搜索拿
# 真实站外信源（URL 白名单校验，模型不得自造链接），直答生成微博情绪 /
# 知乎切面 / 事实核验三段摘要。web_search 配额默认 0 时本 pass 静默跳过。
# ---------------------------------------------------------------------------
CROSSCHECK_MAX = _env_int("CROSSCHECK_MAX", 10, minimum=0)
CROSSCHECK_LINE_MAX = 40
CROSSCHECK_FACT_MAX = 80
CROSSCHECK_SOURCE_NAME_MAX = 20
CROSSCHECK_VERDICT_MAX = 60     # 跨平台综合判断上限
XP_ROLE_MAX = 8                 # 单条引证的角色标签上限（模型给，如 深度归因/当事人声明）
XP_AUTHOR_MAX = 20              # 引证作者/账号名上限（取自检索结果原值）
XP_EXCERPT_MAX = 80             # 引证摘要上限（取自检索结果原值）

# 多平台聚合（2026-11-27）：把白名单信源 URL 按域名确定性归类为平台与层级。
# 顺序敏感——先匹配权威，再匹配平台/门户，最后落 T3。tier 沿用原型三档：
# 1 权威（gov/官媒/通讯社）、2 专业（知乎/B站/主流财经媒体/门户/百科）、3 民间（UGC 社媒/未知站）。
_XP_PLATFORM_RULES = (
    ("gov.cn", "权威媒体", 1), ("edu.cn", "权威媒体", 1), ("news.cn", "权威媒体", 1),
    ("xinhuanet.com", "权威媒体", 1), ("people.com.cn", "权威媒体", 1),
    ("cctv.com", "权威媒体", 1), ("chinanews.com.cn", "权威媒体", 1),
    ("gmw.cn", "权威媒体", 1), ("ce.cn", "权威媒体", 1), ("legaldaily.com.cn", "权威媒体", 1),
    ("gov.hk", "权威媒体", 1), ("reuters.com", "权威媒体", 1), ("apnews.com", "权威媒体", 1),
    ("bbc.com", "权威媒体", 1), ("nature.com", "权威媒体", 1), ("science.org", "权威媒体", 1),
    ("zhihu.com", "知乎", 2), ("bilibili.com", "B站", 2),
    ("thepaper.cn", "主流媒体", 2), ("caixin.com", "主流媒体", 2),
    ("jiemian.com", "主流媒体", 2), ("yicai.com", "主流媒体", 2),
    ("21jingji.com", "主流媒体", 2), ("cls.cn", "主流媒体", 2),
    ("163.com", "门户站点", 2), ("sina.com.cn", "门户站点", 2), ("qq.com", "门户站点", 2),
    ("sohu.com", "门户站点", 2), ("ifeng.com", "门户站点", 2), ("baidu.com", "门户站点", 2),
    ("36kr.com", "门户站点", 2), ("ithome.com", "门户站点", 2), ("wikipedia.org", "百科站点", 2),
    ("weibo.com", "微博", 3), ("weibo.cn", "微博", 3),
    ("douyin.com", "抖音", 3), ("iesdouyin.com", "抖音", 3),
    ("xiaohongshu.com", "小红书", 3), ("x.com", "海外社媒", 3), ("twitter.com", "海外社媒", 3),
)
_XP_TIER_NAMES = {1: "T1 权威", 2: "T2 专业", 3: "T3 民间"}


def _xp_classify_url(url) -> tuple[str, int]:
    """信源 URL → (平台名, tier)。未知站点落「其他站点 · T3」，绝不抛异常。"""
    try:
        host = (urllib.parse.urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return "其他站点", 3
    for suffix, platform, tier in _XP_PLATFORM_RULES:
        if host == suffix or host.endswith("." + suffix):
            return platform, tier
    return "其他站点", 3


def _build_crosscheck_prompt(item: dict, candidates: list) -> str:
    lines = [
        "你是「热榜深潜简报」的跨平台核验编辑。下面是一条热点的全网搜索结果（编号 | 标题 | 来源 | 摘要）。",
        "请只基于这些材料生成跨平台核验摘要，不得新增任何事实、数据、机构名，不得输出编号之外的链接。",
        "",
        f"【热点标题】{item.get('title')}",
        "",
        "【全网搜索结果】",
    ]
    for i, c in enumerate(candidates[:5], 1):
        title = re.sub(r"\s+", " ", str(c.get("Title") or ""))[:60]
        source = re.sub(r"\s+", " ", str(c.get("AuthorName") or ""))[:20]
        summary = re.sub(r"\s+", " ", str(c.get("ContentText") or ""))[:80]
        lines.append(f"{i} | {title} | {source} | {summary}")
    lines += [
        "",
        "【输出 JSON 结构】只输出一个 JSON 对象，不要 markdown 代码块、不要解释文字：",
        '{"weibo":"≤40 字：其他平台（微博/社媒）上的大众情绪走向",'
        '"zhihu":"≤40 字：知乎站内的讨论切面（角度/画风）",'
        '"fact":"≤80 字：基于材料能核实到哪一步、还缺什么",'
        '"verdict":"≤60 字：跨平台综合判断——各平台在吵的是不是同一件事、缺什么才能把讨论拉回同一层面",'
        '"sources":[{"name":"≤20 字信源名","index":1,"role":"≤8 字该信源内容的角色，如 当事人声明/深度归因/民间体感/实测拆解/时间线锚点"}]}',
        "",
        "【硬性要求】",
        "1. sources 只能选上面的搜索结果，index 必须对应编号，最多 2 条。",
        "2. 材料不足以支撑某项时如实写明，禁止编造情绪、数据或信源。",
        "3. verdict 只综合上面给出的材料，不得引入新材料、新机构名。",
    ]
    return "\n".join(lines)


def _is_safe_crosscheck_url(url) -> bool:
    """核验信源只接受 http(s) 真实域名：挡伪协议、IP 字面量、缺 host 的脏链接。

    知乎站内链接是既有契约（「知乎切面」本来就引用站内讨论），允许保留；
    相比旧的 startswith 判断，这里补上 IP / 伪协议 / 无主机名校验。
    """
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not host or "." not in host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    return True


def _sanitize_crosscheck(raw: dict, candidates: list) -> dict | None:
    """校验 crosscheck 输出：三段摘要齐全 + 至少 1 个白名单信源，否则整体丢弃。
    sources 的 URL 只能来自 api_web_search 的真实返回（编号映射），模型无权自造。"""
    if not isinstance(raw, dict):
        return None
    weibo = _clip(raw.get("weibo"), CROSSCHECK_LINE_MAX)
    zhihu = _clip(raw.get("zhihu"), CROSSCHECK_LINE_MAX)
    fact = _clip(raw.get("fact"), CROSSCHECK_FACT_MAX)
    if not (weibo and zhihu and fact):
        return None
    allowed = {}
    for idx, c in enumerate(candidates[:5], 1):
        if not isinstance(c, dict):
            continue
        url = str(c.get("Url") or "").strip()
        if _is_safe_crosscheck_url(url):
            allowed[idx] = url
    sources = []
    seen = set()
    raw_sources = raw.get("sources")
    if isinstance(raw_sources, list):
        for s in raw_sources:
            if len(sources) >= 2:
                break
            if not isinstance(s, dict):
                continue
            url = allowed.get(s.get("index"))
            name = _clip(s.get("name"), CROSSCHECK_SOURCE_NAME_MAX)
            if url and name and url not in seen:
                seen.add(url)
                sources.append({"name": name, "url": url,
                                "role": _clip(s.get("role"), XP_ROLE_MAX)})
    if not sources:
        return None
    out = {"weibo": weibo, "zhihu": zhihu, "fact": fact, "sources": sources}
    verdict = _clip(raw.get("verdict"), CROSSCHECK_VERDICT_MAX)
    if verdict:
        out["verdict"] = verdict
    return out


def _build_cross_platform(cc: dict, candidates: list) -> dict | None:
    """多平台聚合对象（2026-11-27）：引证面板 + 规则温差标签，全部确定性生成。

    evidence 逐字段取自 api_web_search 真实返回（作者/摘要/时间/URL 原值截断），
    平台与 tier 由域名白名单归类；temp 只按「在榜平台数 / 各平台层级 / 是否有
    T1 权威信源」模板拼接，不由大模型现写，避免无源总结。
    """
    allowed: dict[str, dict] = {}
    for c in candidates[:5]:
        if not isinstance(c, dict):
            continue
        url = str(c.get("Url") or "").strip()
        if url.startswith(("http://", "https://")):
            allowed[url] = c
    evidence = []
    for s in cc.get("sources", []):
        cand = allowed.get(s.get("url"))
        if cand is None:
            continue  # 理论上 sanitize 已保证，双保险防脏数据
        platform, tier = _xp_classify_url(s.get("url"))
        evidence.append({
            "platform": platform,
            "role": s.get("role") or "",
            "tier": tier,
            "author": _clip(cand.get("AuthorName"), XP_AUTHOR_MAX),
            "excerpt": _clip(cand.get("ContentText"), XP_EXCERPT_MAX),
            "published_at": _clip(cand.get("EditTime"), 20),
            "url": s.get("url"),
        })
    if not evidence:
        return None
    platforms: list[str] = []
    tier_of: dict[str, int] = {}
    for ev in evidence:
        if ev["platform"] not in tier_of:
            platforms.append(ev["platform"])
            tier_of[ev["platform"]] = ev["tier"]
    multi = len(platforms) >= 2
    if multi:
        temp = [f"全网 {len(platforms)} 平台在榜"]
        temp += [f"{p} · {_XP_TIER_NAMES[tier_of[p]]}" for p in platforms]
        temp.append("含 T1 权威信源" if any(t == 1 for t in tier_of.values())
                    else "暂缺 T1 权威信源")
    else:
        # 诚实降级：单平台命中不硬凑温差，只标事实
        temp = [f"仅 {platforms[0]} 单平台命中 · 无跨平台温差"]
    return {"multi": multi, "temp": temp, "evidence": evidence}


def _crosscheck_item(item: dict) -> bool:
    """单条跨平台核验。返回 False 表示配额/预算硬约束命中，调用方应停止整个 pass。"""
    if item.get("background") == NO_BACKGROUND or item.get("crosscheck"):
        return True
    try:
        candidates = [c for c in api_web_search(str(item.get("title") or ""))
                      if isinstance(c, dict)]
    except UpstreamError as exc:
        if "配额" in str(exc):
            return False  # web_search 配额耗尽：后续条目同样会失败
        _log(f"  第 {item.get('rank')} 条 crosscheck 检索失败（组件保持关闭）：{exc}")
        return True
    except Exception as exc:
        _log(f"  第 {item.get('rank')} 条 crosscheck 检索异常（组件保持关闭）：{exc}")
        return True
    if not candidates:
        return True
    messages = [{"role": "user", "content": _build_crosscheck_prompt(item, candidates)}]
    for attempt in (1, 2):
        try:
            text = _chat_with_retry(
                ZHIHU_BASE + "/v1/chat/completions",
                zhihu_headers(),
                {"model": ZHIDA_MODEL, "messages": messages, "stream": False},
                "核验直答",
            )
            got = _sanitize_crosscheck(extract_json(text), candidates)
        except UpstreamError as exc:
            if "配额" in str(exc) or "预算" in str(exc):
                return False
            if attempt == 1:
                continue
            _log(f"  第 {item.get('rank')} 条 crosscheck 生成失败（组件保持关闭）：{exc}")
            return True
        except Exception as exc:
            _log(f"  第 {item.get('rank')} 条 crosscheck 异常（组件保持关闭）：{exc}")
            return True
        if got:
            item["crosscheck"] = got
            xp = _build_cross_platform(got, candidates)
            if xp:
                item["cross_platform"] = xp
            return True
        if attempt == 2:
            _log(f"  第 {item.get('rank')} 条 crosscheck 输出缺字段或无白名单信源，重试后仍无效，组件保持关闭")
            return True
    return True


def _run_crosscheck_pass(items: list) -> None:
    """主生成与富化完成后统一核验。CROSSCHECK_MAX 限制每份简报最多核验条数；
    web_search 配额（QUOTA_WEB_SEARCH_DAILY，默认 0）不足时静默少做或跳过。"""
    if MOCK_MODE or not items or CROSSCHECK_MAX <= 0:
        return
    done = 0
    zhida_start = _QUOTA.used("zhida")
    for item in items:
        if done >= CROSSCHECK_MAX:
            break
        if (ZHIDA_CROSSCHECK_BUDGET
                and _QUOTA.used("zhida") - zhida_start >= ZHIDA_CROSSCHECK_BUDGET):
            _log(f"crosscheck 分层预算耗尽（{ZHIDA_CROSSCHECK_BUDGET} 次），提前结束（已核验 {done} 条）")
            return
        had = bool(item.get("crosscheck"))
        if not _crosscheck_item(item):
            # 失败原因必须按真实配额状态归因：_crosscheck_item 返回 False 既可能是
            # web_search 检索被拦，也可能是直答 zhida 配额/整批时间预算耗尽。
            # 旧日志一律写「web_search 配额耗尽」，会把明早排查带偏（交接文档 §5.2）。
            if _QUOTA.exhausted("web_search"):
                reason = "全网搜索配额耗尽"
            elif _QUOTA.exhausted("zhida"):
                reason = "直答配额耗尽"
            else:
                reason = "整批时间预算已耗尽"
            _log(f"crosscheck：{reason}，提前结束（已核验 {done} 条）")
            return
        if item.get("crosscheck") and not had:
            done += 1
    _log(f"crosscheck pass 完成：{done} 条带跨平台核验")


def build_digest(items: list) -> dict:
    """
    今日洞察：全部由后端确定性计算，不调用大模型。

    宁可少一点文采，也不要多一次幻觉机会——这是「知识洞察奖」最不该翻车的地方。
    """
    # 防御性清洗：build_digest 也可能直接读到缓存直写/旧版残留的脏数据
    # （正常链路由 sanitize_type / sanitize_sides / sanitize_claim_type 保证，此为第二道防线）
    _cleaned = []
    for _it in items:
        if not isinstance(_it, dict):
            # 缓存直写/旧版残留的脏条目：按「待验证」占位，保持 total 与
            # 三态之和自洽，绝不让 dict(None) 抛异常使整批生成被丢弃。
            _cleaned.append({"type": TYPE_VERIFY, "claim_type": "", "gap": "", "rank": 0})
            continue
        _it = dict(_it)
        if _it.get("type") not in VALID_TYPES:
            _it["type"] = TYPE_VERIFY
        sides = _it.get("sides")
        if _it.get("type") == TYPE_DISPUTE and (not isinstance(sides, list) or len(sides) != 2):
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


def atomic_write_json(path: pathlib.Path, data: dict) -> None:
    """原子写入 JSON 缓存，并对 Windows 瞬时文件锁做重试。

    Windows 杀软/索引器会在新文件落盘后短暂持有句柄，daily_archive 与
    build_timeline 早已加固；主服务的首访生成与后台刷新此前仍是裸写，
    一遇 PermissionError 整批结果就丢失，只能继续服务兜底。此处补齐。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    for attempt in range(5):
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(str(tmp), str(path))
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.4 * (attempt + 1))


def cache_path() -> pathlib.Path:
    return DATA_DIR / f"brief_{today_str()}.json"


def cache_age_seconds() -> float | None:
    """当前当日缓存的年龄（秒）；无缓存返回 None。"""
    path = cache_path()
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def is_simulated_brief(data: dict) -> bool:
    """识别 mock/demo 简报，防止真实模式把模拟缓存当正式内容。"""
    if not isinstance(data, dict):
        return False
    if data.get("source") == "demo":
        return True
    if "模拟数据" in str(data.get("notice") or ""):
        return True
    if "模拟" in str(data.get("generated_at") or ""):
        return True
    items = data.get("items") or []
    if not isinstance(items, list):
        return False
    urls = []
    for item in items:
        if not isinstance(item, dict):
            continue
        urls.extend([item.get("question_url"), item.get("url")])
        reading = item.get("reading") or []
        if isinstance(reading, list):
            urls.extend(r.get("url") for r in reading if isinstance(r, dict))
    return any(
        marker in str(url or "")
        for url in urls
        for marker in ("/question/mock-", "/answer/mock-")
    )


def _offsite_link_count(data) -> int:
    """统计简报中不属于知乎域名的原问题链接与延伸阅读链接条数。

    真实管线产物恒为 0：_base_item 只保留知乎 question_url，assemble_reading
    只放行候选池内的知乎链接。非 0 说明内容经过人工整理或来自旧版数据。
    web_evidence / crosscheck 是明确标注的站外信源，不计入。
    """
    count = 0
    items = data.get("items") if isinstance(data, dict) else None
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        q = str(it.get("question_url") or "").strip()
        if q and not _is_zhihu_url(q):
            count += 1
        reading = it.get("reading")
        for r in reading if isinstance(reading, list) else []:
            if isinstance(r, dict):
                u = str(r.get("url") or "").strip()
                if u and not _is_zhihu_url(u):
                    count += 1
    return count


def annotate_provenance(data):
    """标注内容来源：显式声明 curated 或检出站外链接 → 标为人工整理快照。

    只新增 provenance / offsite_links 两个字段并替换 notice；source 仍严格保持
    demo / live / cache / fallback 四值，前端恢复轮询与归档闸门逻辑不受影响。
    """
    if not isinstance(data, dict):
        return data
    offsite = _offsite_link_count(data)
    if data.get("provenance") == "curated" or offsite:
        data["provenance"] = "curated"
        data["offsite_links"] = offsite
        data["notice"] = NOTICE_CURATED
    return data


def load_today_cache() -> dict | None:
    """FR-07：当天已有缓存则直接读取，秒开；带 (date_str, mtime) 轻量内存缓存。"""
    global _BRIEF_CACHE_KEY, _BRIEF_CACHE_DATA
    path = cache_path()
    try:
        st = path.stat()
    except OSError:
        return None
    day = today_str()
    cache_key = (str(path), day, st.st_mtime_ns)
    with _STATE_LOCK:
        if _BRIEF_CACHE_KEY == cache_key and _BRIEF_CACHE_DATA is not None:
            cached_copy = copy.deepcopy(_BRIEF_CACHE_DATA)
            if not MOCK_MODE and is_simulated_brief(cached_copy):
                return None
            return cached_copy
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log(f"缓存文件损坏，忽略并重新生成：{exc}")
        return None
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    if (not isinstance(items, list) or not items
            or any(not isinstance(it, dict) or not str(it.get("title") or "").strip() for it in items)):
        # 空数组、null 条目或缺标题缓存会让前端出现空白/报错，必须按未命中处理。
        _log(f"缓存结构无效，忽略并重新生成：{path.name}")
        return None
    if not MOCK_MODE and is_simulated_brief(data):
        # mock 开发缓存可以跨进程遗留；真实模式必须重新生成，不能仅改 source 伪装正式内容。
        _log(f"真实模式下模拟缓存无效，忽略并重新生成：{path.name}")
        return None
    data["source"] = "cache"
    generated_at = str(data.get("generated_at") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}", generated_at):
        # 兼容早期缺少有效时间戳的缓存：以缓存文件修改时间为准，不再写死“当日缓存”。
        generated_at = datetime.datetime.fromtimestamp(st.st_mtime, CN_TZ).strftime("%Y-%m-%d %H:%M")
    data["generated_at"] = generated_at
    annotate_provenance(data)
    with _STATE_LOCK:
        _BRIEF_CACHE_KEY = cache_key
        _BRIEF_CACHE_DATA = copy.deepcopy(data)
    _log(f"命中当日缓存：{path.name}")
    return data


def generate_and_cache() -> dict:
    data = build_brief()
    atomic_write_json(cache_path(), data)
    _log(f"实时生成完成，共 {len(data['items'])} 条，已写入 {cache_path().name}")
    return data


def load_fallback() -> dict:
    """FR-08：任何异常下都返回可渲染的兜底数据，保证页面不白屏。

    fallback.json 缺失/损坏/结构不合法时退回内存最小结构；旧写法在这里抛异常，
    会让 get_brief 的兜底分支二次失败，最终 /data.js 返回 500。
    """
    try:
        data = json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise UpstreamError("fallback.json 结构不合法")
    except Exception as exc:
        _log(f"静态兜底不可用，改用内存最小兜底：{exc}")
        # 前端封面/总览直接读 digest.total / headline / 三态 / insights，
        # 最小兜底也必须带完整 digest，否则「有 JS、无字段」仍是白屏。
        data = {"items": [], "digest": build_digest([])}
    if not isinstance(data.get("digest"), dict) or not isinstance(data["digest"].get("total"), int):
        data["digest"] = build_digest(data["items"])
    data["date"] = data.get("date") or today_str()
    data["generated_at"] = data.get("generated_at") or now_str()
    data["source"] = "fallback"
    return annotate_provenance(data)


def _refresh_retry_after() -> int:
    """距离允许下一次整批生成的秒数；0 表示现在可以尝试。"""
    with _STATE_LOCK:
        remaining = _REFRESH_NOT_BEFORE - time.monotonic()
    return max(0, int(remaining + 0.999))


def _set_refresh_cooldown() -> None:
    global _REFRESH_NOT_BEFORE
    with _STATE_LOCK:
        _REFRESH_NOT_BEFORE = time.monotonic() + REFRESH_COOLDOWN_SECONDS


def _start_background_refresh() -> bool:
    """尝试启动唯一后台生成线程；冷却中、已有线程或直答配额触顶时返回 False。"""
    if _QUOTA.exhausted("zhida"):
        _log(
            f"当日直答调用已达 {QUOTA_LIMITS['zhida']} 次上限，跳过后台刷新，继续服务现有缓存"
        )
        return False
    if not _refresh_claim():
        return False
    try:
        threading.Thread(target=_background_refresh, daemon=True).start()
        return True
    except Exception:
        _refresh_release()
        raise


def _maybe_background_refresh() -> None:
    """缓存超过 TTL 则后台静默刷新（stale-while-revalidate）：
    本次请求照常秒回旧缓存，生成完成后后续访客自动拿到新版。"""
    if CACHE_TTL_SECONDS <= 0:
        return
    age = cache_age_seconds()
    if age is None or age <= CACHE_TTL_SECONDS:
        return
    if not _start_background_refresh():
        return  # 已有刷新线程在跑，或失败后仍在冷却期
    _log(f"缓存已 {int(age // 60)} 分钟未更新（TTL {CACHE_TTL_SECONDS // 3600} 小时），触发后台自动刷新")


def _count_ok(data: dict) -> int:
    """简报中有背景解读（未降级）的条目数。"""
    return sum(1 for it in (data or {}).get("items", []) if it.get("background") != NO_BACKGROUND)


_COVERAGE_WEIGHTS = (("punch", 2), ("vote", 1), ("quotes", 1), ("lou", 1),
                     ("crosscheck", 2), ("cross_platform", 1), ("kanshan", 1))


def _field_coverage(data: dict) -> int:
    """字段覆盖分：衡量一份简报的功能完整度（锐评/站队/吃瓜/导读/核验/聚合/看山）。
    防劣化加权专用——成功条数相同的两份简报，功能完整度可能天差地别。"""
    score = 0
    for it in (data or {}).get("items", []):
        if not isinstance(it, dict):
            continue
        for field, weight in _COVERAGE_WEIGHTS:
            if it.get(field):
                score += weight
    return score


def _coverage_max(data: dict) -> int:
    """该简报条数对应的理论满分（用于「接近满配」判定）。"""
    return sum(w for _, w in _COVERAGE_WEIGHTS) * len((data or {}).get("items", []))


def _brief_hot_signature(data: dict) -> set:
    """简报的热榜签名：问题 id 集合（取不到 id 时退化为 URL/标题）。
    排名变化不影响签名——只有话题集合变化才算「榜单变了」。"""
    sig = set()
    for it in (data or {}).get("items", []):
        if not isinstance(it, dict):
            continue
        url = str(it.get("question_url") or "").strip()
        qid = _QID_RE.search(url)
        sig.add(qid.group(1) if qid else (url or _norm_title(it.get("title"))))
    return sig


def _hot_list_unchanged(hot_items: list, cached: dict) -> bool:
    """接口热榜与缓存简报的话题集合是否完全一致。"""
    new_sig = set()
    for hot in hot_items or []:
        if not isinstance(hot, dict):
            continue
        url = str(hot.get("Url") or "").strip()
        qid = _QID_RE.search(url)
        new_sig.add(qid.group(1) if qid else (url or _norm_title(hot.get("Title"))))
    return bool(new_sig) and new_sig == _brief_hot_signature(cached)


_KANSHAN_GIFS = {
    TYPE_DISPUTE: ("assets/ks-ball.webp", "assets/ks-stroll.webp"),
    TYPE_CONSENSUS: ("assets/ks-pc.webp", "assets/ks-hi.webp"),
    TYPE_VERIFY: ("assets/ks-sleepy.webp", "assets/ks-idle.webp"),
}


def _build_kanshan(item_type: str, focus: str, gap: str, rank: int) -> dict:
    """为实时生成的简报条目配齐看山动态表情与导读短句，零额外模型配额消耗。"""
    gifs = _KANSHAN_GIFS.get(item_type, ("assets/ks-idle.webp",))
    gif = gifs[(max(rank, 1) - 1) % len(gifs)]
    if focus and focus != EMPTY_FOCUS:
        line = _clip(focus, 40)
    elif gap and gap != EMPTY_GAP:
        line = _clip(f"关键拼图：{gap}", 40)
    else:
        line = "先看核心争议与已知事实，再决定要不要点进原回答。"
    return {"gif": gif, "line": line}


def _is_enriched(data: dict) -> bool:
    """人工整理快照（provenance=curated，由 annotate_provenance 标注）不可被管线覆盖。

    不能再按字段判断：punch/vote/quotes/lou/kanshan 由富化 pass 产出，crosscheck
    也已由 _run_crosscheck_pass 自动产出（需 QUOTA_WEB_SEARCH_DAILY > 0）。按字段判断时，
    开启配额后当日第一版管线缓存就带 crosscheck，之后每次后台刷新都会被跳过，
    当天内容冻结在第一版。load_today_cache 读取时已调用 annotate_provenance。"""
    return isinstance(data, dict) and data.get("provenance") == "curated"


def _background_refresh() -> None:
    try:
        with _GEN_LOCK:
            age = cache_age_seconds()
            if age is not None and age <= CACHE_TTL_SECONDS and load_today_cache() is not None:
                return  # 等锁期间已被别的请求刷新过；仅有新鲜 mtime 的坏缓存不算
            existing = load_today_cache()
            if _is_enriched(existing):
                _set_refresh_cooldown()
                _log("现有缓存为人工整理快照（provenance=curated），管线结果不覆盖，冷却 "
                     f"{REFRESH_COOLDOWN_SECONDS} 秒")
                return
            # 防劣化保护：只把 load_today_cache 认可的有效旧缓存作为基准。
            # 模拟缓存即使“成功条数”很多，也不能阻止真实新结果覆盖它。
            old_ok = _count_ok(load_today_cache() or {})
            old_cov = _field_coverage(existing or {})
            # 榜单签名短路：热榜没变且旧缓存接近满配时跳过重生成（省主生成全部配额）。
            # 旧缓存本身残缺时不短路——继续生成补字段，残缺画面不能被冻结。
            if existing and old_cov >= _coverage_max(existing) * COVERAGE_SKIP_REGEN_RATIO:
                try:
                    current_hot = api_hot_list()
                except Exception as exc:
                    current_hot = None
                    _log(f"榜单签名查询失败（照常进入全量生成）：{exc}")
                if current_hot is not None and _hot_list_unchanged(current_hot, existing):
                    _set_refresh_cooldown()
                    _log(f"热榜与缓存话题集合一致且缓存接近满配（覆盖分 {old_cov}），"
                         f"跳过重生成，冷却 {REFRESH_COOLDOWN_SECONDS} 秒")
                    return
            _log("后台自动刷新开始")
            data = build_brief()
            new_ok = _count_ok(data)
            if new_ok == 0:
                # 全量降级不是可用简报：无缓存时继续展示兜底，有缓存时保留旧内容。
                _set_refresh_cooldown()
                _log(f"本次生成 0 条成功，丢弃结果并冷却 {REFRESH_COOLDOWN_SECONDS} 秒")
                return
            if old_ok > 0 and new_ok < old_ok:
                _set_refresh_cooldown()
                _log(
                    f"刷新结果劣于现有缓存（{new_ok} < {old_ok} 条成功），"
                    f"丢弃本次结果并冷却 {REFRESH_COOLDOWN_SECONDS} 秒，沿用旧缓存"
                )
                return
            # 防劣化加权（同榜单限定）：字段覆盖显著低于旧缓存时保留旧缓存——
            # 配额紧张时生成的「10 条全成功但富化全空」不许顶掉满配画面。
            # 榜单变了（新话题）跳过此门：新鲜度优先，不为保覆盖分展示昨日新闻。
            new_cov = _field_coverage(data)
            if (old_cov > 0 and new_cov + COVERAGE_TOLERANCE < old_cov
                    and _brief_hot_signature(data) == _brief_hot_signature(existing or {})):
                _set_refresh_cooldown()
                _log(f"同榜单重生成字段覆盖显著劣化（{new_cov} < {old_cov} - {COVERAGE_TOLERANCE}），"
                     f"丢弃本次结果并冷却 {REFRESH_COOLDOWN_SECONDS} 秒，沿用旧缓存")
                return
            atomic_write_json(cache_path(), data)
            _log(f"后台自动刷新完成：{new_ok} 条成功，已写入 {cache_path().name}")
    except Exception as exc:
        _set_refresh_cooldown()
        _log(
            f"后台自动刷新失败（旧缓存继续服务，页面不受影响），"
            f"冷却 {REFRESH_COOLDOWN_SECONDS} 秒：{exc}"
        )
    finally:
        _refresh_release()


def _fallback_with_refresh_state(refreshing: bool = False) -> dict:
    """冷缓存时立即给出可读的静态兜底，并向前端暴露自动恢复状态。"""
    data = load_fallback()
    data["refreshing"] = bool(refreshing or _refresh_active())
    data["retry_after"] = _refresh_retry_after()
    # notice 保持快照原值，与静态演示站一致；refreshing/retry_after 仅驱动前端静默轮询
    return data


def get_brief() -> dict:
    """缓存秒回；真实模式的冷缓存生成转入后台，避免网关等待整批构建。

    mock 生成不联网且耗时极短，保留同步首访，方便本地一次跑通全链路。
    真实模式无缓存时立即返回明确标记的静态兜底，后台只启动一个生成线程；
    前端依据 refreshing / retry_after 轮询，缓存落盘后自动切换到当日简报。
    """
    try:
        cached = load_today_cache()
        if cached:
            # 后台刷新是尽力而为：它的任何异常都不能让「已命中的健康缓存」被丢掉。
            try:
                _maybe_background_refresh()
            except Exception as exc:
                _log(f"后台刷新触发失败（不影响本次缓存响应）：{exc}")
            return cached
        if MOCK_MODE:
            with _GEN_LOCK:
                cached = load_today_cache()
                if cached:
                    return cached
                return generate_and_cache()

        started = _start_background_refresh()
        if started:
            _log("无可用缓存，已转为后台生成；本次请求立即返回静态兜底")
        # 极快生成可能已在启动后落盘，优先直接返回真实缓存。
        cached = load_today_cache()
        if cached:
            return cached
        return _fallback_with_refresh_state(refreshing=started)
    except Exception as exc:
        _log(f"生成失败，切换到静态兜底：{exc}")
        # 带恢复状态让前端按冷却时间轮询；load_fallback 已保证永不抛异常。
        return _fallback_with_refresh_state()


# ---------------------------------------------------------------------------
# Flask 路由
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/")
def index():
    return send_file(INDEX_PATH)


@app.route("/assets/<path:filename>")
def assets(filename):
    """v2.8 吉祥物/动效静态资源：前端以 assets/xx.webp 相对路径引用，
    容器必须显式提供，否则正式站吉祥物 404（静态托管的临时站不受影响）。"""
    resp = send_from_directory(DEMO_DIR / "assets", filename)
    # 容器内 mimetypes 缺少 .webp 映射（回退 application/octet-stream），
    # 与 nosniff 叠加会被 iOS Safari / 微信等严格浏览器拒绝渲染——显式补齐图片 MIME。
    lower = filename.lower()
    for ext, mime in ((".webp", "image/webp"), (".png", "image/png"),
                      (".gif", "image/gif"), (".jpg", "image/jpeg"),
                      (".jpeg", "image/jpeg")):
        if lower.endswith(ext):
            resp.headers["Content-Type"] = mime
            break
    return resp


@app.route("/healthz")
def healthz():
    """只读健康检查：不触发任何生成/刷新，仅汇报密钥状态；详细计数器需 HEALTHZ_VERBOSE=1。

    供云托管探针与值班巡检使用；零副作用是本端点唯一硬约束。
    """
    secret_ok = bool(ZHIHU_ACCESS_SECRET) or MOCK_MODE
    payload = {
        "status": "ok" if secret_ok else "degraded",
        # 只暴露布尔值，不泄露密钥内容：用于部署后确认环境变量是否注入成功，
        # 避免"没有报错、但一直静默服务兜底数据"这类最难排查的故障。
        "secret_configured": secret_ok,
        "gen_budget_seconds": GEN_BUDGET_SECONDS,
        "enrich_budget_seconds": ENRICH_BUDGET_SECONDS,
    }
    if os.environ.get("HEALTHZ_VERBOSE", "0") == "1":
        age = cache_age_seconds()
        cached = load_today_cache() or {}
        cached_items = cached.get("items") if isinstance(cached, dict) else None
        if not isinstance(cached_items, list):
            cached_items = []
        cached_items = [it for it in cached_items if isinstance(it, dict)]
        payload.update({
            "date": today_str(),
            "has_today_cache": age is not None,
            "cache_age_sec": None if age is None else int(age),
            "refreshing": _refresh_active(),
            "cooldown_remaining": _refresh_retry_after(),
            "zhida_calls_today": _QUOTA.used("zhida"),
            "zhida_daily_limit": QUOTA_LIMITS["zhida"],
            # 富化/核验覆盖计数：排查「配额耗尽导致 pass 静默跳过」时不再靠拉 data.js 数字段
            "enriched_count": sum(1 for it in cached_items
                                  if it.get("punch") or it.get("vote") or it.get("lou")),
            "crosscheck_count": sum(1 for it in cached_items if it.get("crosscheck")),
            "cross_platform_count": sum(1 for it in cached_items if it.get("cross_platform")),
            "field_coverage": _field_coverage(cached),
            # 逐字段覆盖计数 + 配额池用量：排查「某个 pass 静默跳过」时不用再拉 data.js 数字段
            "punch_count": sum(1 for it in cached_items if it.get("punch")),
            "lou_count": sum(1 for it in cached_items if it.get("lou")),
            "web_evidence_found_count": sum(
                1 for it in cached_items
                if isinstance(it.get("web_evidence"), dict)
                and it["web_evidence"].get("status") == "found"),
            "web_search_calls_today": _QUOTA.used("web_search"),
            "web_search_daily_limit": QUOTA_LIMITS.get("web_search", 0),
        })
    return Response(
        json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _js_safe_json(payload: str) -> str:
    """把 JSON 文本转义成可安全内联进 <script> 的 JS 字面量。

    只替换 <、>、U+2028、U+2029：它们在 JSON 字符串里等价于 \\u003c 等转义，
    解析结果不变，但可避免提前闭合 <script> 或触发旧引擎的换行解析错误。
    """
    return (
        payload.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


@app.route("/data.js")
def data_js():
    try:
        brief = get_brief()
    except Exception as exc:
        # 最后一道防线：任何未预期异常都输出可解析的兜底 JS，绝不返回 500。
        # 保留 038 的 _js_safe_json 转义与 nosniff 头，不回退为裸 json.dumps。
        _log(f"/data.js 生成异常，输出最小兜底：{exc}")
        brief = load_fallback()
    payload = _js_safe_json(json.dumps(brief, ensure_ascii=False))
    return Response(
        f"window.BRIEF_DATA = {payload};",
        mimetype="application/javascript",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


_TIMELINE_CACHE: dict = {"data": None, "fetched_at": 0.0, "url": ""}


def _fetch_remote_timeline(remote_url: str) -> dict | None:
    """拉取并校验远程时间线 JSON（带 _STATE_LOCK 内存 TTL 缓存），失败返回 None。"""
    parsed = urllib.parse.urlparse(remote_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        _log(f"/timeline.js 远程地址非法，回落本地文件：{remote_url!r}")
        return None

    now_mono = time.monotonic()
    with _STATE_LOCK:
        if (
            _TIMELINE_CACHE["data"] is not None
            and _TIMELINE_CACHE["url"] == remote_url
            and TIMELINE_CACHE_TTL_SECONDS > 0
            and (now_mono - _TIMELINE_CACHE["fetched_at"]) < TIMELINE_CACHE_TTL_SECONDS
        ):
            return _TIMELINE_CACHE["data"]

    try:
        req = urllib.request.Request(remote_url, headers={"User-Agent": "zhihu-hot-digest/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not (isinstance(data, dict) and isinstance(data.get("topics"), list)):
            raise ValueError("远程时间线 JSON 缺少 topics 列表")
        with _STATE_LOCK:
            _TIMELINE_CACHE["data"] = data
            _TIMELINE_CACHE["fetched_at"] = time.monotonic()
            _TIMELINE_CACHE["url"] = remote_url
        return data
    except Exception as exc:
        _log(f"/timeline.js 远程拉取失败，回落本地文件：{exc}")
        return None


@app.route("/timeline.js")
def timeline_js():
    """话题时间线：优先读取 TIMELINE_REMOTE_URL（带 TTL 缓存），失败或未配置时回落本地文件。"""
    empty = {
        "generated_at": "",
        "archive_files": 0,
        "tracked_days": 0,
        "missing_dates": [],
        "topics": [],
    }
    remote_url = TIMELINE_REMOTE_URL.strip()
    data = _fetch_remote_timeline(remote_url) if remote_url else None
    if data is None:
        try:
            local_data = json.loads((DATA_DIR / "topic_timeline.json").read_text(encoding="utf-8"))
            if not (isinstance(local_data, dict) and isinstance(local_data.get("topics"), list)):
                raise ValueError("topic_timeline.json 顶层结构无效")
            data = local_data
        except Exception as exc:
            _log(f"/timeline.js 文件不可用，安全回落空结构：{exc}")
            data = empty
    payload = _js_safe_json(json.dumps(data, ensure_ascii=False))
    return Response(
        f"window.TIMELINE_DATA = {payload};",
        mimetype="application/javascript",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------------------------
# 功能示例页 /showcase/：人工整理快照与程序生成数据彻底分开
# 首页 / 兜底只用管线产物；跨平台核验等人工整理内容只在这里展示，并明确标注。
# 页面复用 demo/index.html，其 data.js / timeline.js / assets 均为相对路径，
# 在 /showcase/ 下自动解析到下面几个路由。
# ---------------------------------------------------------------------------
NOTICE_SHOWCASE = (
    "这是功能示例页：展示一份人工整理快照，包含跨平台核验等自动管线尚未产出的内容；"
    "部分链接来自站外媒体，未经过程序的知乎域名校验。今日简报请回首页查看。"
)


def showcase_path() -> pathlib.Path:
    return DATA_DIR / "showcase.json"


def load_showcase() -> dict | None:
    """读取功能示例快照；缺失或结构无效返回 None（页面显示数据为空，不回落到管线数据）。"""
    try:
        data = json.loads(showcase_path().read_text(encoding="utf-8"))
    except Exception as exc:
        _log(f"功能示例快照不可用：{exc}")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or not data["items"]:
        return None
    annotate_provenance(data)
    # source 用 cache：示例页不触发前端恢复轮询；showcase 标记驱动「功能示例」徽章与横幅
    data["source"] = "cache"
    data["showcase"] = True
    data["notice"] = NOTICE_SHOWCASE
    data.pop("refreshing", None)
    data.pop("retry_after", None)
    return data


@app.route("/showcase/")
def showcase_index():
    return send_file(INDEX_PATH)


@app.route("/showcase/data.js")
def showcase_data_js():
    data = load_showcase()
    payload = "null" if data is None else _js_safe_json(json.dumps(data, ensure_ascii=False))
    return Response(
        f"window.BRIEF_DATA = {payload};",
        mimetype="application/javascript",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.route("/showcase/timeline.js")
def showcase_timeline_js():
    return timeline_js()


@app.route("/showcase/assets/<path:filename>")
def showcase_assets(filename):
    return assets(filename)


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


def log_startup_diagnostics() -> None:
    """WSGI（gunicorn）启动时的非阻断自检。

    `if __name__ == "__main__"` 分支在 gunicorn 下不会执行，所以 check_config()
    的提示也就不会出现。这里补一次只写日志、不退出进程的自检：容器进程应由编排
    系统管理，缺密钥时明确告警并继续用兜底/缓存数据服务，避免反复重启放大故障。
    """
    try:
        DATA_DIR.mkdir(exist_ok=True)
    except Exception as exc:  # 目录不可写不应阻断启动，但必须留痕
        _log(f"启动告警：数据目录不可写（{exc}），缓存与配额计数将无法落盘")
    if MOCK_MODE:
        _log("启动自检：mock 模式，不请求知乎 API")
        return
    if ZHIHU_ACCESS_SECRET:
        _log(f"启动自检：密钥已配置（{len(ZHIHU_ACCESS_SECRET)} 字符），数据目录 {DATA_DIR}")
        return
    _log("启动告警：未检测到 ZHIHU_ACCESS_SECRET——只会返回兜底/缓存数据，不会发起真实生成")


log_startup_diagnostics()


if __name__ == "__main__":
    if not check_config():
        sys.exit(1)
    DATA_DIR.mkdir(exist_ok=True)
    port = _env_int("PORT", 5000, minimum=1)
    _log(f"热榜深潜简报已启动：http://127.0.0.1:{port}/")
    app.run(host="0.0.0.0", port=port)
