# 运维手册：部署、配置与排障

> 从根目录 README 拆出，供部署与值班使用。评审期值班流程（冷缓存恢复、配额触顶、预热、回滚）见 [archive/值班卡_评审期运维_2026-11-17.md](archive/值班卡_评审期运维_2026-11-17.md)。

## 缓存与配额机制

### 缓存与恢复

- **冷缓存不阻塞**：真实模式把 1–3 分钟的整批构建放到后台，首访不再同步等待；`fallback.refreshing / retry_after` 只用于前端自动恢复，`source` 仍严格保持 `demo / live / cache / fallback` 四种取值。
- **失败重试冷却**：刷新异常、整批 0 条成功或劣化结果被丢弃后，默认冷却 15 分钟；旧缓存继续服务，避免访客流量反复触发整批生成。冷却只限制重试风暴，不替代日配额硬限额。
- **坏缓存自动恢复**：空 `items`、非对象条目、缺标题或损坏 JSON 均按缓存未命中处理；后台双重检查同时验证内容与 mtime，避免“新鲜坏缓存”阻止重新生成。

### 质量与配额边界

- **单条降级**：每次大模型只处理 1 条热点，失败只降级该条，不中断整份简报；页面保留主标题与原问题入口，并明确说明素材不足时不编造背景与争议。
- **429 退避与配额边界**：知乎 API 限流时按有界指数退避重试（`RETRY_BACKOFF_BASE` 默认 2s 起步、`RETRY_WAIT` 默认 30s 封顶、`RETRY_TIMES` 默认 6 次含首次），每次尝试都计入按日配额守卫（热榜 100 / 搜索 2000 / 直答 80 / 备选模型 500 / 全网搜索 0，达上限熔断跳过）。零降级、不重试时，首轮生成消耗 1 次热榜 + 10 次搜索 + 10 次直答，另加富化环节每条最多 2 次直答；之后的刷新按问题复用当日已生成条目（`INCREMENTAL_REFRESH`），只为新上榜条目调用，3 小时 TTL 下每次刷新通常只需几次直答。**整批生成另有软预算** `GEN_BUDGET_SECONDS`（默认 180 秒）：预算耗尽后停止新的重试与补跑，保留已生成的部分结果，不杀线程；冷却与劣化结果覆盖规则沿用既有逻辑。免费额度与价格以知乎控制台当期规则为准。
- **冷启动耗时**：2026-11-20 定位到旧实现冷启动 3–5 分钟的主因是固定 30s 退避 × 10 条串行（上游本身瞬时返回），不是上游慢或守卫开销；改为指数退避并去掉补跑前无条件等待后，轻度限流从约 300 秒降到约 20 秒。计时证据见 `tests/test_cold_start_latency.py`（虚拟时钟，不真睡）。

### 兜底快照与功能示例页

- **首页兜底**：`data/fallback.json`（线上冷启动时返回）和 `demo/data.js`（本地双击预览）只放程序生成的完整简报。每日归档任务运行 `python scripts/daily_archive.py --update-fallback`，当日简报满足「至少 8 条、全部有效、无站外链接、非模拟」时自动刷新这两个文件并提交；不满足时保留旧文件，归档照常完成。注意：提交后要重新部署，线上兜底才会更新。
- **功能示例页**：`/showcase/` 复用同一个页面，数据来自 `data/showcase.json`（早期人工整理的快照，含跨平台核验，部分链接未经过程序校验）。页面徽章为「功能示例」，横幅说明内容来源并提供返回首页的链接；文件缺失时显示数据为空，不会回落到管线数据。
- **人工整理内容不要再写进 `fallback.json` 或 `data/brief_<日期>.json`**：它们属于程序数据路径，混入后会被标为「人工整理快照」，并让冒烟检查失败。

## 部署

线上实例部署于腾讯云 CloudBase 容器型云托管（Docker），`Min 1 / Max 1` 常驻单实例 + 原子文件写入，可降低缓存分裂与半截文件风险；但容器内缓存不是持久化历史仓库，部署或实例重建仍可能丢旧快照。任何能运行 Python 的托管均可（Render / Railway / PythonAnywhere 等），通用步骤：

1. 上传代码（不要上传 `.env` 与 `data/brief_*.json`）。
2. 平台环境变量里填入 `ZHIHU_ACCESS_SECRET`（平台会自动提供 `PORT`）。
3. 启动命令：镜像默认用 `gunicorn -w 1 --threads 4 ... app:app`（见 `Dockerfile`）；本地调试可用 `python app.py`。**必须保持单进程（`-w 1`）**，进程内锁与配额计数才有效。
4. **必须验证**：用手机流量打开公网链接，确认评委视角可完整访问。

**CloudBase 部署命令（固定用仓库根目录，不要再建 `deploy/` 副本）**：

```bash
tcb cloudrun deploy \
  --env-id genuine-dove-d8gywlbm2e6dbc1b8 \
  --service-name zhihu-hot-digest \
  --source . --port 5000 --force --wait
```

- 根目录 `.dockerignore` 已将构建上下文限定为 `app.py / demo / data / requirements.txt`，其余文件（文档、视频、宣发素材）不会进镜像；
- 历史上曾用 `deploy/` 子目录副本部署，出现过"改了根目录却推了旧代码"，该目录已于 2026-11-17 删除，**不要重建**；
- CLI 有交互提示「是否灰度发布」，非交互环境需 `printf '\n' | tcb ...` 送默认回车，`--force` 不覆盖此项。

## 环境变量

见 `.env.example`。除 `ZHIHU_ACCESS_SECRET` 外都有默认值。

- `ZHIHU_ACCESS_SECRET`：知乎开放平台密钥，**等同密码**，只通过环境变量读取，绝不写进代码 / 仓库。
- `MOCK_MODE=1`：mock 模式开关。
- `ZHIDA_MODEL`：知乎直答模型，默认 `zhida-fast-1p5`。
- `MODEL_API_KEY` / `MODEL_BASE` / `MODEL_NAME`：备选通用大模型（OpenAI 兼容接口），三项齐全后知乎直答失败时自动切换。
- `PORT`：服务端口，默认 5000。
- `CACHE_TTL_HOURS`：当日缓存有效期（小时），默认 3，过期后由访问流量惰性触发后台静默刷新；设为 `0` 恢复「每日只生成一次」。
- `REFRESH_COOLDOWN_SECONDS`：刷新异常、整批 0 条成功或劣化结果丢弃后的冷却时间，默认 900 秒；冷却期间保留旧缓存或静态兜底，不重复调用上游。
- `RETRY_BACKOFF_BASE` / `RETRY_WAIT` / `RETRY_TIMES`：429 有界指数退避，默认 `2` / `30` / `6`；首次等待 2s 后按 2 倍递增，`RETRY_WAIT` 为单次等待上限，`RETRY_TIMES` 含首次。
- `GEN_BUDGET_SECONDS`：整批生成软预算，默认 180 秒；耗尽后停止新重试与补跑并保留已生成的部分结果，`0` = 不限制。
- `INCREMENTAL_REFRESH`：增量刷新开关，默认 `1`；按知乎问题 ID（其次标题）复用当日已成功生成的条目，只为新上榜或失败的条目调用直答，复用池为 `data/item_pool_<日期>.json`。设为 `0` 恢复每次整批重算。
- `ITEM_REUSE_MAX_HOURS`：单条最长复用时长（小时），默认 `0` = 当日内不限，跨日自动失效。
- `QUOTA_ZHIDA_DAILY` / `QUOTA_HOT_LIST_DAILY` / `QUOTA_SEARCH_DAILY` / `QUOTA_MODEL_ALT_DAILY` / `QUOTA_WEB_SEARCH_DAILY`：统一 `QuotaGuard` 日配额熔断上限（默认 `80` / `100` / `2000` / `500` / `0`，持久化于 `data/quota_counters.json`）。
- `CROSSCHECK_MAX`：每份简报最多生成几条跨平台核验，默认 `3`，按「待验证 → 可裁决事实之争 → 其余」的顺序核验。实际是否生成取决于 `QUOTA_WEB_SEARCH_DAILY`（默认 `0` = 关闭）；开启前请确认全网搜索配额，每条核验消耗 1 次全网搜索 + 最多 2 次直答。核验只要求事实核验与至少 1 条白名单信源，站外讨论、知乎切面两段为可选。
- `GAP_LINK_MAX`：每份简报最多为几条证据缺口做站内搜索，默认 `10`，`0` = 关闭。每次消耗 1 次站内搜索（`search` 配额，默认 2000/天），不调用直答。程序用缺口关键词（新生成条目由模型在主生成里顺带给出 `gap_query`，旧条目按规则从缺口文本抽词）搜知乎站内，按关键词命中打分，挑 0–1 条回答展示为「站内相关讨论」；页面注明「程序按关键词找到，未核实是否补上了这块拼图」。
- `GAP_LINK_RECHECK_SECONDS`：没找到站内相关讨论的条目，隔多久再搜一次，默认 `10800`（3 小时）。新回答往往晚于热点出现，增量刷新时会重试；找到后不再重复搜索。
- `ENRICH_QUOTA_RESERVE`：富化的额外配额底线，默认 `0`（关闭）。直答配额的分阶段分配现在由 `ZHIDA_MAIN_BUDGET` / `ZHIDA_ENRICH_BUDGET` / `ZHIDA_CROSSCHECK_BUDGET`（默认 45/20/15）负责；只有想在此之外再为后续刷新多留一些配额时才设置，设置后富化每条之前检查当日直答剩余量，低于该值即停止富化。
- `SIDES_REQUIRE_EVIDENCE`：两派观点绑定原文，默认 `1`。每派须通过候选编号绑定至少 1 条站内回答，同一条回答不能同时作为两派依据，绑定不上即降级为「待验证」；`0` = 宽松，保留两派，只附上能绑定的回答。日志中的「两派未能各自绑定候选回答，降级为 verify」可用来统计降级次数。
- 拦截统计：程序生成的简报带 `guard_stats` 字段（延伸阅读丢弃数、两派依据丢弃与共用移除数、真分歧降级数、跨平台核验信源丢弃数，以及新生成和复用条数），只统计本次新生成的条目，收尾补跑会累加。兜底快照、功能示例页不带这个字段，页面也不显示。
- 合订本分词：每日归档任务额外安装 `jieba`，用于识别标题中的人名、机构名；未安装时 `scripts/build_timeline.py` 自动退回字符切分。人工确认的同一事件写在 `data/topic_aliases.json` 的 `aliases`（按知乎问题 ID 指定），`topics` 段仅作记录，脚本不读取。
- 配额韧性（PR #14，均有默认值，云端不配也能跑）：`ZHIDA_MAIN_BUDGET` / `ZHIDA_ENRICH_BUDGET` / `ZHIDA_CROSSCHECK_BUDGET` 为单次简报内主生成/富化/核验的 zhida 分层上限（默认 `45` / `20` / `15`，合计 80 = 日限），防止主生成吃光配额导致富化、核验静默归零；`CROSSCHECK_BUDGET_SECONDS` 为核验 pass 独立时间预算（默认 `90`）；`COVERAGE_TOLERANCE`（默认 `4`）与 `COVERAGE_SKIP_REGEN_RATIO`（默认 `0.6`）控制防劣化：同榜单重生成字段覆盖显著下降时保留旧缓存，热榜未变且旧缓存接近满配时跳过重生成。
- 排查提示：`HEALTHZ_VERBOSE=1` 时 `/healthz` 返回 `punch_count` / `lou_count` / `crosscheck_count` / `cross_platform_count` / `web_evidence_found_count` / `field_coverage` 及 `web_search_calls_today` / `web_search_daily_limit`，可直接判断某个 pass 是否静默跳过，无需再拉 `/data.js` 数字段。核验 pass 提前结束的日志会按真实原因区分「全网搜索配额耗尽 / 直答配额耗尽 / 整批时间预算已耗尽」。
- 来源保护：后台刷新只跳过来源标注为人工整理（`provenance: "curated"`）的缓存；程序生成的跨平台核验不会阻止刷新。

## 分步验证命令

```bash
# 1) mock 模式全链路
MOCK_MODE=1 python app.py
curl http://127.0.0.1:5000/data.js   # 成功：首行 window.BRIEF_DATA = {"date":...，含 digest 三态统计
curl http://127.0.0.1:5000/data.js   # 第二次：source 变为 "cache"
ls data/                             # 出现 brief_YYYY-MM-DD.json

# 2) 兜底路径（mock 关掉、密钥乱填、指向不可达地址）
MOCK_MODE=0 ZHIHU_ACCESS_SECRET=fake ZHIHU_BASE=http://127.0.0.1:9 python app.py
curl http://127.0.0.1:5000/data.js   # 成功：source 为 "fallback"，页面仍完整

# 3) 缺密钥（mock 关掉、不设密钥）
MOCK_MODE=0 python app.py            # 成功：打印中文设置指引，退出码 1，无 traceback
```

## 报错对照表

| 现象 | 含义 | 处理 |
|---|---|---|
| 启动提示缺少 `ZHIHU_ACCESS_SECRET` | 未设置密钥且未开 mock | 按提示设置环境变量，或先 `MOCK_MODE=1` |
| 知乎接口错误码 `20001` | 鉴权失败 | 检查密钥是否正确、是否带 Bearer |
| 知乎接口错误码 `30001` | 频率限制或额度用尽 | 退避重试自动处理；当日缓存天然限流 |
| 页面徽章「演示兜底」 | 生成链路任一环节失败 | 属预期兜底行为，看终端日志定位原因 |
| 某卡片显示「本条暂无可用素材，未生成解读」 | 该条单点生成降级 | 保留主标题与原问题入口，不影响其余卡片；冒烟脚本会将该快照判为 FAIL，提交前须复查 |
| 首次刷新后内容没变化 | 缓存未过期 | 正常；TTL 默认 3 小时，后台静默刷新 |

## 研发工具链

- **Kimi Code**：正式用于代码阅读、功能开发、调试、测试与差异审查；提交、推送、部署仍须负责人确认。
- **Kimi Claw**：云端「柳东凯」的两个首页只读巡检条目已启用，分别在每天 09:17、21:23（Asia/Shanghai）仅 GET 生产根路径；本机旧条目已于 2026-11-19 21:28 删除。21:23 自动触发、日志与异常告警已验证，结果为 HTTP 503；这表示巡检机制正常、被巡检首页不可用，不代表简报内容验收通过。Claw 不进入核心生成链路，不持有密钥，不自动修改仓库或生产环境。
- **Kimi Work**：继续承担本地文件处理与综合交付。

具体权限、实际任务编号、运行记录和退出条件见 [docs/archive/研发工具链_Kimi_Code_Claw.md](archive/研发工具链_Kimi_Code_Claw.md) 与 [docs/archive/HANDOFF.md](archive/HANDOFF.md)。这些工具属于研发与运营辅助，不改变 Demo 架构，也不作为产品核心创新宣称；首页巡检不能替代 10 条内容完整性与质量验收。

## 版本记录

- **v2.8（2026-11-21 深夜轮）**：封面 v18 居中单栏改版；吉祥物素材 GIF→动画 WebP（修复发布包动画丢失）；手机端整页滑动翻页动画；`data.js?v=2` 防缓存；新增公网发布包（现存 `docs/archive/release-2026-11-21/`）、`extension/` 插件、云端旧站存档（现存 `docs/archive/cloudbase-2026-11-21/`）。详见 [HANDOFF_2026-11-21_深夜轮](handoff/HANDOFF_2026-11-21_深夜轮.md)。

## 跨天空窗期的兜底

站点过了零点、当天简报还没生成时，`/data.js` 先找 `FALLBACK_PREV_MAX_DAYS`（默认 `2`）天内最近一天的程序生成简报（`data/brief_*.json` 且 `source=live`），找不到才用 `data/fallback.json`。页面提示「今天的简报还在生成，先展示 X 日由程序生成的简报」。容器重启后本地缓存会丢失，此时仍退回 `fallback.json`，所以每日归档的 `--update-fallback` 仍要保留。
