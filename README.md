# 热榜深潜简报

**10 分钟，看懂今天大家在吵什么。**

[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Deploy on Tencent CloudBase](https://img.shields.io/badge/Deploy-Tencent%20CloudBase-1783FF?logo=tencent-cloud&logoColor=white)](https://cloudbase.net/)
[![知乎黑客松 S3 参赛作品](https://img.shields.io/badge/知乎黑客松-S3%20参赛作品-0066FF)](https://www.zhihu.com/)

**[🌐 线上体验（免登录直达）](https://zhihu-hot-digest-348140-5-1533039364.sh.run.tcloudbase.com/)**

基于知乎开放平台 API（热榜 / 站内搜索 / 知乎直答）把当日热榜自动生成一份 10 分钟可读完的深度简报。别人做「是什么」，我们做「怎么变的」——不只告诉你热点是什么，更告诉你**哪些是真分歧、哪些是假吵架、哪些缺一手材料**。

![桌面端总览](docs/images/hero-desktop.png)

## 它和别人做的热榜有什么不同

每天热榜 10 条里，真正吵起来的通常只有一半。本项目的核心不是「总结」，而是**分类裁决**：

| 三态裁决 | 含义 | 页面呈现 |
|---|---|---|
| 🔴 真分歧 | 双方冲突真实存在 | 展开支持派 / 反对派双列对照，并标注**分歧性质**：可裁决 / 推测对推测 / 观感对观感 / 价值判断 |
| 🟢 已共识 | 热度来自关注度而非争议 | 直接给共识结论，不制造假对立 |
| 🟡 待验证 | 缺少一手材料，吵不出结论 | 指出**缺的是哪块拼图**，而不是硬编两派观点 |

每条分歧还会自动生成一个**聚焦问题**（这件事真正值得回答的是哪一点），把吵架翻译成一个可以思考的问题。

![卡片三态对照](docs/images/cards-desktop.png)

延伸阅读链接 100% 来自知乎搜索 API 的真实返回，点击跳回知乎站内阅读原回答。

## 数据流与防幻觉架构

```
GET /data.js
  ├─ 存在 data/brief_<今天>.json → 直接返回缓存（source=cache，秒开）
  ├─ 缓存过期（默认 3h TTL）→ 后台静默刷新，旧缓存继续服务，访客零等待
  │     └─ 刷新结果比旧缓存差（成功条数变少）→ 当场丢弃，守住全绿缓存（防劣化棘轮）
  ├─ 无缓存时实时生成：热榜 10 条 → 每条站内搜索 → 知乎直答逐条生成 → 原子写入缓存
  └─ 任一步异常 → data/fallback.json 静态兜底（source=fallback，页面仍完整）
```

- **后端确定性组装**：知乎直答只输出结构化裁决字段（三态分类 / claim_type / 聚焦问题 / 缺口），最终的 `build_digest` 由后端代码确定性地执行，零模型自由度。
- **双重防幻觉**：大模型只输出候选编号，程序按编号映射回搜索 API 的真实返回；组装后再校验 URL 必须在本次搜索白名单内、主机名必须是 `zhihu.com` 或其子域名，越界与重复条目直接丢弃。上线首晚曾拦截过候选池之外的越界假编号。
- **完整性闸门**：声称 dispute 却凑不出两派真实观点的条目，自动降级为待验证——宁可承认缺材料，不编造对立。
- **单条降级**：每次大模型只处理 1 条热点，失败只降级该条（显示「暂无解读」），不中断整份简报。
- **429 退避与配额边界**：知乎 API 限流时自动等待重试（默认 30s × 6 次）。零降级、不重试时，一轮生成消耗 1 次热榜 + 10 次搜索 + 10 次直答；3 小时 TTL 下理论最多约 8 轮/日，直答成功调用约 80–160 次/日，**不含 429 尝试**，且当前没有日配额计数或熔断。免费额度与价格以知乎控制台当期规则为准；真正限额需要另做配额守卫。

## 快速开始

只需 Python 3 和 Flask 一个依赖：

```bash
pip install flask
```

### 方式 A：mock 模式（无密钥 / 无网络，先跑通全链路）

```bash
# Windows PowerShell
$env:MOCK_MODE="1"; python app.py
# Windows CMD
set MOCK_MODE=1 && python app.py
# macOS / Linux
export MOCK_MODE=1 && python app.py
```

打开 `http://127.0.0.1:5000/`。**成功标志**：页面顶部徽章为「示例数据」，10 张卡片完整展示；终端打印「mock 模式：使用本地模拟数据」。

### 方式 B：真实数据（需要知乎开放平台 Access Secret）

```bash
$env:ZHIHU_ACCESS_SECRET="你的密钥"; python app.py   # PowerShell
```

**成功标志**：首次访问约需 1–3 分钟（页面显示等待提示），徽章为「实时生成」；之后 3 小时缓存期内秒开，徽章为「当日缓存」。网关 504 或进程不可达不由数据层兜底覆盖。

### 本地双击预览

直接双击 `demo/index.html`：读取同目录静态 `data.js`，无需启动后端。

## 部署

线上实例部署于腾讯云 CloudBase 容器型云托管（Docker），`Min 1 / Max 1` 常驻单实例 + 原子文件写入，可降低缓存分裂与半截文件风险；但容器内缓存不是持久化历史仓库，部署或实例重建仍可能丢旧快照。任何能运行 Python 的托管均可（Render / Railway / PythonAnywhere 等），通用步骤：

1. 上传代码（不要上传 `.env` 与 `data/brief_*.json`）。
2. 平台环境变量里填入 `ZHIHU_ACCESS_SECRET`（平台会自动提供 `PORT`）。
3. 启动命令 `python app.py`。
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

## 测试与验收

**本项目没有接入 CI**，所有检查均为本地脚本手动执行。

三项集成测试是**独立脚本，不是 unittest 用例**（没有 `TestCase` 类），因此 `python -m unittest discover` 会显示 0 项——这是预期行为，不是"测试不存在"。正确运行方式：

```bash
python tests/test_cache_ttl.py      # 缓存 TTL 到点后台刷新
python tests/test_refresh_guard.py  # 后台刷新结果劣于旧缓存时丢弃
python tests/test_retry_pass.py     # 整批结束后对降级条目补跑一轮
```

其余验收脚本：

```bash
python scripts/smoke_check.py                                   # HTTP 冒烟：首页/data.js/10 条/digest 自洽/域名合法
python scripts/verify_plan_numbers_v2.py data/baseline_insight_v2.json   # 复现计划书附录 A 固定基线
python scripts/verify_plan_numbers_v2.py data.js                         # 复核当日线上快照
python scripts/audit_gap_quality.py                                      # gap 可执行性预筛（启发式，需人工复核）
```

> `data/baseline_insight_v2.json` 是人工归类预览基线，**不是运行时产物**，不能用它跑 `smoke_check.py`。
> 评审期运维（冷缓存 504、配额触顶、预热、回滚）见 `值班卡_评审期运维_2026-11-17.md`。

## 目录结构

```
.
├── app.py               # Flask 后端：调 API、三态裁决、缓存与兜底
├── demo/
│   ├── index.html       # 页面（本地双击可预览静态示例）
│   └── data.js          # 静态示例数据（部署后由后端接管同名路由）
├── data/
│   ├── fallback.json    # 静态兜底数据（任何异常下保证页面可完整展示）
│   └── brief_*.json     # 当日缓存（运行时自动生成，勿提交）
├── docs/images/         # README 截图
├── Dockerfile           # CloudBase 云托管镜像
├── .env.example         # 环境变量模板（不含真实密钥）
└── README.md
```

## 环境变量

见 `.env.example`。除 `ZHIHU_ACCESS_SECRET` 外都有默认值。

- `ZHIHU_ACCESS_SECRET`：知乎开放平台密钥，**等同密码**，只通过环境变量读取，绝不写进代码 / 仓库。
- `MOCK_MODE=1`：mock 模式开关。
- `ZHIDA_MODEL`：知乎直答模型，默认 `zhida-fast-1p5`。
- `MODEL_API_KEY` / `MODEL_BASE` / `MODEL_NAME`：备选通用大模型（OpenAI 兼容接口），三项齐全后知乎直答失败时自动切换。
- `PORT`：服务端口，默认 5000。
- `CACHE_TTL_HOURS`：当日缓存有效期（小时），默认 3，过期后由访问流量惰性触发后台静默刷新；设为 `0` 恢复「每日只生成一次」。
- `RETRY_WAIT` / `RETRY_TIMES`：429 限流退避的等待秒数与总尝试次数，默认 30 / 6。

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
| 某卡片显示「暂无解读」 | 该条单点生成失败 | 属单条降级，不影响其余卡片 |
| 首次刷新后内容没变化 | 缓存未过期 | 正常；TTL 默认 3 小时，后台静默刷新 |

## 演进路线

**V1（当前）：当日横切面**——每天 10 条热榜的三态裁决快照。

**V2（规划）：时间轴深潜**——对同一事件追踪 72 小时内知乎叙事的演化：观点何时反转、哪些早期少数派被验证、评论区是怎么吵起来的。当前每日缓存具备快照形态，但历史持久化尚未实现；迁移到对象存储并建立事件索引，是 V2 上线的第一项工程。

## 参考与引用披露

- **AI 辅助开发**：本作品由学生团队完成，开发过程全程使用 AI 辅助编程（结对 AI + 人工验收）。
- **数据来源**：知乎开放平台 API（热榜、站内搜索）与知乎直答；所有延伸阅读链接均来自知乎搜索 API 的真实返回并跳回知乎站内。
- **开源组件**：Flask（BSD-3-Clause），通过 `pip install flask` 引入，未修改其源码。
- **设计打磨**：页面视觉经开源 skill [unslop](https://github.com/heydamir/unslop)（作者 Damir Sokolovsky，[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)）辅助审查，特此署名。
