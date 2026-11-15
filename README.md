# 热榜深潜简报

**10 分钟，看懂今天大家在吵什么。**

基于知乎开放平台 API（热榜 / 站内搜索 / 知乎直答）把当日热榜自动生成一份 10 分钟可读完的深度简报：每条热点配「背景解读 + 支持/反对/未定论三派观点对照 + 延伸阅读」，延伸阅读链接 100% 来自知乎搜索 API 的真实返回，点击跳回知乎站内阅读原回答。

---

## 1. 目录结构

```
.
├── app.py               # Flask 后端：调 API、组装简报、缓存与兜底
├── demo/
│   ├── index.html       # 页面（本地双击可预览静态示例）
│   └── data.js          # 静态示例数据（本地双击时读取；部署后由后端接管同名路由）
├── data/
│   └── fallback.json    # 静态兜底数据（任何异常下保证页面可完整展示）
├── data/brief_*.json    # 当日缓存（运行时自动生成，勿提交）
├── .env.example         # 环境变量模板（不含真实密钥）
├── .gitignore
└── README.md
```

## 2. 安装与运行

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

打开 `http://127.0.0.1:5000/`。**成功标志**：页面顶部徽章为「示例数据」，提示横幅含「【模拟数据】」，10 张卡片完整展示，所有链接为灰色不可点并带「示例」标签；终端打印「mock 模式：使用本地模拟数据」。

### 方式 B：真实数据（需要知乎开放平台 Access Secret）

```bash
# Windows PowerShell
$env:ZHIHU_ACCESS_SECRET="你的密钥"; python app.py
# Windows CMD
set ZHIHU_ACCESS_SECRET=你的密钥 && python app.py
# macOS / Linux
export ZHIHU_ACCESS_SECRET=你的密钥 && python app.py
```

**成功标志**：首次访问约需 1–3 分钟（页面有加载提示，不白屏），徽章为「实时生成」；刷新第二次秒开，徽章变为「当日缓存」。

### 本地双击预览（无需启动后端）

直接双击 `demo/index.html`：读取同目录下的静态 `data.js`（source=demo），徽章「示例数据」，0 个可点链接。

## 3. 环境变量说明

见 `.env.example`。除 `ZHIHU_ACCESS_SECRET` 外都有默认值。

- `ZHIHU_ACCESS_SECRET`：知乎开放平台密钥，**等同密码**，只通过环境变量读取，绝不写进代码 / 仓库 / 聊天记录。
- `MOCK_MODE=1`：mock 模式开关。
- `ZHIDA_MODEL`：知乎直答模型，默认 `zhida-fast-1p5`。
- `MODEL_API_KEY` / `MODEL_BASE` / `MODEL_NAME`：备选通用大模型（OpenAI 兼容接口），三项都配置后，知乎直答失败时自动切换。
- `PORT`：服务端口，默认 5000。
- `REQUEST_TIMEOUT`：上游接口超时秒数，默认 30。
- `RETRY_WAIT` / `RETRY_TIMES`：429 限流退避的等待秒数与总尝试次数，默认 30 / 6。
- `CACHE_TTL_HOURS`：当日缓存的有效期（小时），默认 3。过期后由访问流量惰性触发后台静默刷新（旧缓存继续服务，访客零等待）；设为 `0` 则恢复「每日只生成一次」。

## 4. 数据流与兜底逻辑

```
GET /data.js
  ├─ 存在 data/brief_<今天>.json → 直接返回缓存（source=cache，秒开；超过 CACHE_TTL_HOURS 则后台静默刷新，本次仍返回旧缓存）
  ├─ 否则实时生成：热榜 10 条 → 每条站内搜索 → 知乎直答逐条生成 → 写缓存（source=live）
  └─ 任一步异常 → 读取 data/fallback.json（source=fallback，notice 提示演示兜底）
```

- 每次大模型只处理 1 条热点，成功后再循环下一条；单条失败只降级该条（显示「暂无解读」），不中断整份简报；首轮生成后对仍降级的条目自动补跑一次。
- **防幻觉机制**：大模型只输出候选编号，程序按编号映射回搜索 API 的真实返回，组装后再校验 URL 必须在本次搜索返回的白名单内，不在集合内的条目直接丢弃（双重防线）。
- 密钥缺失时启动即给出中文提示并退出，不抛 traceback。

## 5. 部署

任何能运行 Python 的托管均可（Render / Railway / PythonAnywhere / 各类平台一键发布）。通用步骤：

1. 上传代码（不要上传 `.env` 与 `data/brief_*.json`）。
2. 在平台的环境变量设置里填入 `ZHIHU_ACCESS_SECRET`（平台会自动提供 `PORT`）。
3. 启动命令：`python app.py`，开放端口由平台分配。
4. **必须验证**：用手机流量（不连同一 WiFi）打开公网链接，确认评委视角可完整访问。

Render 参考：New Web Service → 关联仓库 → Build Command 留空或 `pip install flask` → Start Command `python app.py` → 添加环境变量 → Deploy。

## 6. 分步验证命令与成功标志

```bash
# 1) mock 模式全链路
MOCK_MODE=1 python app.py
curl http://127.0.0.1:5000/data.js          # 成功：首行 window.BRIEF_DATA = {"date":...
curl http://127.0.0.1:5000/data.js          # 第二次：source 变为 "cache"，generated_at 为「当日缓存」
ls data/                                    # 出现 brief_YYYY-MM-DD.json

# 2) 兜底路径（mock 关掉、密钥乱填、指向不可达地址，模拟接口故障）
MOCK_MODE=0 ZHIHU_ACCESS_SECRET=fake ZHIHU_BASE=http://127.0.0.1:9 python app.py
curl http://127.0.0.1:5000/data.js          # 成功：source 为 "fallback"，notice 为「当前为演示兜底数据，接口暂不可用」，页面仍完整

# 3) 缺密钥（mock 关掉、不设密钥）
MOCK_MODE=0 python app.py                   # 成功：打印中文设置指引，退出码为 1，无 traceback
```

## 7. 报错对照表

| 现象 | 含义 | 处理 |
|---|---|---|
| 启动提示缺少 `ZHIHU_ACCESS_SECRET` | 未设置密钥且未开 mock | 按提示设置环境变量，或先 `MOCK_MODE=1` |
| 知乎接口错误码 `10001` | 参数错误（字段名/大小写写错） | 检查 `Limit` / `Query` / `Count` 拼写 |
| 知乎接口错误码 `20001` | 鉴权失败 | 检查 `ZHIHU_ACCESS_SECRET` 是否正确、是否带 Bearer |
| 知乎接口错误码 `30001` | 频率限制或额度用尽 | 当天缓存天然限流；等自然日重置，页面自动走兜底 |
| 知乎接口错误码 `90001` | 平台内部错误 | 稍后重试，页面自动走兜底 |
| 页面徽章「演示兜底」 | 生成链路任一环节失败 | 属预期兜底行为，看终端日志定位原因 |
| 某卡片显示「暂无解读」 | 该条单点生成失败 | 属单条降级，不影响其余卡片；整批结束后会自动补跑一次 |
| 大模型输出非 JSON | 解析失败 | 自动降级，终端日志留有原文前 100 字 |

## 8. 参考与引用披露

- **AI 辅助开发**：本作品由 3 人大学生团队完成，开发过程全程使用 AI 辅助编程。
- **数据来源**：知乎开放平台 API（热榜、站内搜索）与知乎直答；页面所有延伸阅读链接均来自知乎搜索 API 的真实返回并跳回知乎站内。
- **开源组件**：Flask（BSD-3-Clause 许可证），通过 `pip install flask` 引入，未修改其源码。
- **设计打磨**：页面视觉经开源 skill [unslop](https://github.com/heydamir/unslop)（作者 Damir Sokolovsky，[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 协议）辅助审查与微调，特此署名。
- **公开方向参考**：热榜聚合与日报式内容产品为公开创意方向，本作品的核心功能（观点对照三派、编号映射防幻觉、打开时实时生成 + 当日缓存 + 静态兜底）由团队独立设计实现。

## 9. 演进路线（V2 规划，供计划书引用）

**从「当日快照」到「叙事演化」**：当前版本回答「这条热点是什么、大家在吵什么」；
V2 方向是「时间轴深潜」——对同一事件追踪 72 小时内知乎叙事的演化：观点何时反转、
哪些早期少数派声音被后续事实验证、评论区是如何吵起来的。别人做「是什么」，我们做「怎么变的」。

这一方向与现有架构天然衔接：搜索 API 免费额度约 1000 次/天，当前版本用「缓存 TTL 自动刷新 +
单条降级 + 静态兜底」把日消耗压在 150 次以内、约占额度 15%；
V2 的增量采集会在同一套缓存架构上增加历史快照层，把「额度约束」升级为「演化数据源」。

## 10. 提交前检查清单

- [ ] 真实密钥下 `Code=0` 验证通过（热榜 10 条 + 每条搜索有候选 + 直答返回合法 JSON）
- [ ] 延伸阅读逐条点开验证可打开、且均为知乎站内真实回答
- [ ] 手机流量打开公网链接验证
- [ ] 页脚三项声明完整（基于知乎开放平台 API 构建 / AI 生成内容仅供参考 / 本披露章节）
- [ ] 仓库中无 `.env`、无 `data/brief_*.json`、无真实密钥
- [ ] `data/fallback.json` 中的链接为站内真实页面（当前为知乎热榜/搜索页，可替换为一次成功运行中已验证的回答链接）
