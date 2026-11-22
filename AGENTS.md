# AI 研发协作工程守则 (AGENTS.md)
# 项目：知乎黑客松 S3 · 热榜深潜简报 (Zhihu Hot Digest)

所有在本代码库中作业的智能体（Antigravity、Cursor、Copilot 等）必须严格遵守以下工程规范与红线。
详细指导请参见：[.github/copilot-instructions.md](.github/copilot-instructions.md)。

---

## 1. 核心研发四原则

1. **先问清楚 (Grill-Me)**：开工前暴露未决假设，给出明确选项和 `(Recommended)` 建议；查库先于提问，不把看代码能解决的问题抛给人类。
2. **控制改动 (Karpathy Guidelines)**：外科手术式精准编码（Minimal Diff），严禁大面积随意重构或格式化文件；不编造不存在的 API 字段或指标；保护现有防御（`QuotaGuard`、原子写入、安全转义）。
3. **按证据排错 (Systematic Debugging)**：先复现、看堆栈日志；单变量提出假设施测，拒绝瞎猜参数或用 `except: pass` 遮掩错误；修复必须伴随回归测试。
4. **规范交接 (Handoff)**：清楚说明当前分支、已改动文件、真实测试结果、环境限制与下一步行动。

---

## 2. 不可违背的仓库红线 (Invariants)

- **配额守卫**：Search 上限 2000，直答上限 90（硬限 80），web_search 默认 0；杜绝超额消耗与虚构的“5000:100”指标。
- **纯静态 Jamstack**：零外部数据库（无 MySQL/Redis/SQLite），数据归档于 `data/archive/YYYY-MM-DD.json`，原子安全替换落盘。
- **极简报刊风**：780px 居中单栏、Noto Serif 字体、纸质色板（`--bg: #faf9f5`）；严禁赛博玻璃态/浮动卡片。
- **根路由硬锁**：根路径 `/` 100% 默认展示 `#today` 今日首张卡片；时间轴仅在带有 `#timeline` 或点击「连续剧」Tab 时展示，禁止缺省劫持。
- **XSS 安全防线**：`/data.js` 与 `/timeline.js` 必须经 `_js_safe_json()` 转义且带 `nosniff`；前端 DOM 渲染必须使用 `textContent`，严禁 `innerHTML` 拼接不可信输入。
- **全绿门禁**：改动后必须保持 `tests/*.py`（全部 16 个测试脚本）与 `scripts/verify_live.py`（13/13 项指标）全部 ExitCode 0。
