# GitHub Copilot & Coding Agent 协作守则
# 项目：知乎黑客松 S3 · 热榜深潜简报 (Zhihu Hot Digest)

欢迎参与本项目研发。为确保在团队（刘东凯、饶子翔、林子涵）与多智能体（Copilot、Cursor、Antigravity）协作中保持代码质量、杜绝意外回归并保护金贵的比赛配额，所有 Agent 必须严格遵守以下四项工程纪律与项目红线。

---

## 一、 核心四大工程纪律 (The Four Engineering Pillars)

### 1. 先问清楚 (Grill-Me) · 需求与设计分支对齐
- **拒绝盲目推演**：严禁在未对齐需求边界时擅自编写可能推翻重来的大块核心逻辑。
- **查库先于提问**：如果某个问题可以通过阅读仓库文件、检查已有测试或执行只读自检得到答案，必须先自主勘测，不得把阅读代码能解决的问题抛给人类。
- **暴露假设与推荐**：在遇到多技术路线（如静态归档 vs 数据库、卡片内嵌 vs 独立路由）时，列出具体选项，并使用 `(Recommended)` 给出明确推荐理由与权衡（Trade-offs）。

### 2. 控制改动 (Karpathy Guidelines) · 外科手术式编码
- **最小化变更（Minimal Diff）**：每次修改只触碰与当前目标直接相关的函数与代码行。
- **严禁炫技式重构**：严禁顺手对未涉及的模块做格式化、重命名、全量重写或重构既有良好运转的逻辑。
- **杜绝幻觉与假数字**：严禁在代码、注释或汇报中编造未经测试集验证的虚假性能指标（如“召回率 83.3%”）；API 参数与字段必须基于 `app.py` 或知乎官方文档真实存在的内容。
- **继承防御性设计**：严禁绕过既有的安全与高可用防护（如 `QuotaGuard` 配额熔断、`tmp.replace` POSIX 原子替换、`_js_safe_json` 转义、`X-Content-Type-Options: nosniff`）。

### 3. 按证据排错 (Systematic Debugging) · 拒绝盲猜
- **必须先复现**：遇到 Bug 或报错，先建立最小复现脚本或针对性测试，查阅完整 Traceback、错误行号与返回结构。
- **单变量验证**：每次调试只改变一个变量或参数，观察报错特征；严禁“随机试探多个参数”或写一个空的 `try...except pass` 掩耳盗铃。
- **用测试锁死修复**：任何 Bug 修复后，必须同步补充回归测试断言，确保同类错误不再复发。

### 4. 严谨交接与环境感知 (Handoff & Environment Awareness)
- **环境权限自知**：若当前运行在 IDE 受限沙箱（无终端命令执行权限），请客观说明“已完成代码审查与修改，但本地门禁测试需在宿主终端执行”，不得将“环境无权执行命令”误判为“测试未通过”，也不得伪造全绿声明。
- **规范交接记录**：会话结束或阶段交付时，明确交代：当前分支、已修改文件、验证状态、已知风险与下一步清晰指令。

---

## 二、 本项目专属不可侵犯的工程红线 (Repository Invariants)

在进行任何修改时，以下约束构成硬性验收门禁：

1. **配额硬上限（绝不打满官方配额）**：
   - 站内搜索配额：`QUOTA_LIMITS["search"] = 2000` 次/天；
   - 直答大模型配额：`QUOTA_LIMITS["zhida"] = 90` 次/天（硬上限 `DAILY_ZHIDA_LIMIT = 80` 次/天）；
   - 全网搜索：`QUOTA_LIMITS["web_search"] = 0`（试点前保持禁用）；
   - 绝不在文档或实现中引用虚构的“5000:100 / 50:1 套利”，严防调用不存在的搜索字段。
2. **零重型数据库（Pure Static Jamstack）**：
   - 本项目完全基于无服务器云托管与静态数据流，**严禁引入 MySQL / Redis / MongoDB / SQLite**；
   - 每日归档存入 `data/archive/YYYY-MM-DD.json`，时序索引存入 `data/topic_timeline.json`，原子安全落盘。
3. **前端极简报刊风（Editorial Newspaper Style）**：
   - 页面采用 780px 居中单栏、Noto Serif 衬线体、纸质底色（`--bg: #faf9f5`）、油墨黑（`--ink: #161614`）；
   - **严禁引入玻璃态（Glassmorphism）、毛玻璃高光等赛博风格 CSS**，保持报纸手风琴折叠质感。
4. **前端路由硬锁（防劫持契约）**：
   - 访问根路径 `/` 必须 100% 默认渲染 `#today` 今日首张卡片；
   - 时间轴合订本仅在带有 `#timeline` 路由或用户显式点击「合订本」Tab 时渲染，绝不可在缺省状态下劫持首页。
5. **DOM XSS 硬防护**：
   - `/data.js` 与 `/timeline.js` 输出必须通过 `_js_safe_json()` 转义 `<, >, \u2028, \u2029`，响应头必带 `nosniff`；
   - 前端 JavaScript 渲染必须使用 `textContent` 与 `document.createTextNode`，**严禁使用 `innerHTML` 拼接不可信输入**。
6. **自动化回归套件必须全绿**：
   - `python tests/test_engineering_hardening.py`（43 项工程加固）
   - `python tests/test_timeline_integration.py`（31 项时间轴集成）
   - `python -m unittest discover tests`（全部 16 个测试脚本 ExitCode 0）
   - `python scripts/verify_live.py`（13/13 官方线上验收契约）

---

## 三、 快速参考命令

```bash
# 运行合订本集成测试
python tests/test_timeline_integration.py

# 运行工程加固测试
python tests/test_engineering_hardening.py

# 运行线上契约验收
python scripts/verify_live.py

# 执行每日归档与时间轴编译
python scripts/daily_archive.py
python scripts/build_timeline.py
```
