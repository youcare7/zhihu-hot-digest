# 测试与验收

## 自动化（CI）

每次 push / PR 由 GitHub Actions（[`.github/workflows/test.yml`](../.github/workflows/test.yml)）自动执行三组检查，全程不读取任何密钥、不联网：

| 任务 | 内容 |
|---|---|
| Python 测试 | `tests/test_*.py` 逐文件独立进程运行，每个文件的结果与耗时写入运行摘要 |
| 前端测试 | `npm run test:frontend`（两项 Node 测试） |
| 镜像冒烟 | `docker build` 后以 mock 模式启动，探活 `/healthz`、`/`、`/data.js`、`/timeline.js` 与吉祥物 WebP 的 MIME |

## 本地运行

一次跑完全部 Python 测试（与 CI 相同，逐文件独立进程）：

```bash
for f in tests/test_*.py; do python "$f" || echo "FAIL $f"; done
```

> 不建议用 `unittest discover`：`test_timeline_pipeline` 等脚本式验收会在 import 时执行并改动全局状态，`test_timeline_integration` 则会被静默跳过。

逐文件清单：

```bash
python tests/test_bugfix_timeline_contract.py  # 归档健康闸门、时间线前后端字段契约、主服务原子写入重试
python tests/test_build_snapshot.py            # 离线快照生成：数据注入锚点、timeline 内联、时区口径
python tests/test_cache_timestamp.py           # 缓存保留真实生成时刻，旧缓存按 mtime 补齐
python tests/test_cache_ttl.py                 # 缓存 TTL 到点后台刷新
python tests/test_cold_start.py                # 冷缓存立即兜底、并发只生成一次、全败不落坏缓存
python tests/test_cold_start_latency.py        # 冷启动耗时：指数退避、补跑零等待、整批软预算（虚拟时钟）
python tests/test_engineering_hardening.py     # /healthz 只读、状态锁防重入、直答日配额、/data.js 转义
python tests/test_enrichment_pass.py           # 互动组件富化（锐评 / 站队 / 吃瓜 / 楼上导读）
python tests/test_incremental_refresh.py       # 增量刷新：只为新上榜条目调用直答、问题 ID 复用、整批丢弃后从条目池补回
python tests/test_invalid_cache.py             # 空 / 非对象 / 损坏 JSON 缓存均能自动恢复
python tests/test_merge_hardening_038.py       # 038 合并缺口回归
python tests/test_provenance.py                # 人工整理快照的来源标注：站外链接如实标出，不冒充实时生成
python tests/test_quota_guard.py               # 按接口按日计数、失败 / 429 计入、达上限熔断跳过
python tests/test_reading_index_guard.py       # 延伸阅读编号守卫：bool / 非整数编号不得选中候选
python tests/test_refresh_cooldown.py          # 异常与劣化刷新后的重试冷却
python tests/test_refresh_guard.py             # 后台刷新结果劣于旧缓存时丢弃
python tests/test_regression_fixes.py          # 2026-11-25 审计修复回归
python tests/test_retry_pass.py                # 整批结束后对降级条目补跑一轮
python tests/test_showcase.py                  # 功能示例页 /showcase/ 与兜底快照刷新：人工内容不进入首页数据路径
python tests/test_stability_hardening.py       # 配置容错、计数落盘失败隔离、兜底链不 500、Code 类型宽容
python tests/test_text_quality.py              # 安全收束、乱码 / 疑似硬截断与降级冒烟拦截
python tests/test_timeline_entity_matching.py  # 合订本 v3 实体匹配与远程时间线回落
python tests/test_timeline_integration.py      # 归档与时间线管线集成检查
python tests/test_timeline_pipeline.py         # 时间线管线独立验收（Docker 白名单、归档、构建）
python tests/test_verification_scripts.py      # 验收脚本有界等待、兜底不误判为内容全绿
python tests/test_web_evidence.py              # 全网证据核验试点：单条、四态、补跑去重、配额 0 跳过
```

前端行为另有两项 Node 测试（仅开发验收需要 Node.js，线上运行时不增加依赖）：

```bash
node tests/test_frontend_recovery.js  # 异常轮询、空缓存、首次恢复、健康缓存不循环刷新
node tests/test_paged_navigation.js   # 分页导航与目录行为
```

其余验收脚本：

```bash
python scripts/smoke_check.py                                   # 单次严格 HTTP 冒烟，默认单请求 15s 超时
python scripts/smoke_check.py --wait 360 --timeout 15           # 部署后有界等待冷缓存恢复，再跑完整内容门
python scripts/verify_live.py                                   # v2 结构验收；fallback 明确判为内容未就绪
python scripts/verify_plan_numbers_v2.py data/baseline_insight_v2.json   # 复现计划书附录 A 固定基线
python scripts/verify_plan_numbers_v2.py data.js                         # 复核当日线上快照
python scripts/audit_gap_quality.py                                      # gap 可执行性预筛（启发式，需人工复核）
```

> `data/baseline_insight_v2.json` 是人工归类预览基线，**不是运行时产物**，不能用它跑 `smoke_check.py`。
> 评审期运维见 [ops.md](ops.md) 与 [archive/值班卡_评审期运维_2026-11-17.md](archive/值班卡_评审期运维_2026-11-17.md)。
