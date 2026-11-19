# -*- coding: utf-8 -*-
"""v3.1「缺口全网实证核验」回归测试。

六条边界各自对应一个真实会翻车的场景：
  1. 站外白名单：过滤知乎域名与伪协议、去重、最多 2 条
  2. 只有知乎链接或空池：必须老实标 not_found，不能假装命中
  3. 上游 429 / 结构异常：静默降级 skipped，绝不中断整份简报
  4. 开关关闭与哨兵文案：不触发检索，不烧 API 配额
  5. mock 模式：返回示例文案且完全不触网
  6. 检索式构造：长度 0 < len ≤ 60，且剥掉「目前缺少 / 还需要」这类引导语

运行：python tests/test_gap_verify.py（在仓库根目录下）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

FIXED_NOW = "11-19 23:50"


def _verify(title, gap, **kwargs):
    """固定时间戳、默认开启核验，保证结果与机器时钟和本地 env 无关。"""
    kwargs.setdefault("now_str", FIXED_NOW)
    kwargs.setdefault("enabled", True)
    return app.verify_gap_with_global_search(title, gap, **kwargs)


def test_found_filters_zhihu_and_dedups():
    pool = [
        {"Title": "知乎站内同题", "Url": "https://www.zhihu.com/question/1"},
        {"Title": "知乎专栏转载", "Url": "https://zhuanlan.zhihu.com/p/2"},
        {"Title": "伪协议注入", "Url": "javascript:alert(1)"},
        {"Title": "人社部三季度工时统计", "Url": "https://www.mohrss.gov.cn/report.html"},
        {"Title": "同一条链接重复出现", "Url": "https://www.mohrss.gov.cn/report.html"},
        {"Title": "国家统计局分行业口径", "Url": "https://www.stats.gov.cn/data.html"},
        {"Title": "第三站（应被 2 条上限截断）", "Url": "https://example.org/third.html"},
    ]
    result = _verify(
        "四天工作制试点扩大，是否该全国推广？",
        "缺人社部三季度工时统计原始口径",
        search_fn=lambda query: pool,
    )
    assert result["status"] == "found", result
    assert len(result["sources"]) == 2, "最多只保留 2 条站外信源"
    urls = [s["url"] for s in result["sources"]]
    assert len(set(urls)) == 2, "重复链接必须去重"
    for source in result["sources"]:
        assert set(source) == {"title", "url", "host"}, source
        assert source["url"].startswith("http"), "只允许 http(s) 协议"
        assert source["host"] != "zhihu.com", "知乎主域名必须过滤"
        assert not source["host"].endswith(".zhihu.com"), "知乎子域名必须过滤"
        assert len(source["title"]) <= 48, "标题需收束到 48 字以内"
    assert result["query"], "命中时也要回传实际检索式，便于复核"
    assert "命中 2 条站外公开信源" in result["note"]


def test_not_found_when_only_zhihu_or_empty():
    only_zhihu = [
        {"Title": "知乎回答", "Url": "https://www.zhihu.com/question/1/answer/2"},
        {"Title": "知乎专栏", "Url": "https://zhuanlan.zhihu.com/p/3"},
        {"Title": "伪协议", "Url": "javascript:void(0)"},
    ]
    result = _verify("某争议事件", "缺当事方 11 月财报", search_fn=lambda query: only_zhihu)
    assert result["status"] == "not_found", "只有站内链接时不能冒充站外命中"
    assert result["sources"] == []
    assert "暂未检索到公开权威材料" in result["note"]

    empty = _verify("某争议事件", "缺当事方 11 月财报", search_fn=lambda query: [])
    assert empty["status"] == "not_found"
    assert empty["sources"] == []

    broken = _verify("某争议事件", "缺当事方 11 月财报", search_fn=lambda query: None)
    assert broken["status"] == "not_found", "返回非列表时按未命中处理，不能崩"


def test_api_error_graceful_degrade():
    def rate_limited(query):
        raise app.UpstreamError("知乎接口返回错误码 429（Too Many Requests）")

    def unexpected(query):
        raise RuntimeError("上游返回结构异常")

    for search_fn in (rate_limited, unexpected):
        result = _verify("某争议事件", "缺当事方 11 月财报", search_fn=search_fn)
        assert result["status"] == "skipped", "任何上游异常都必须静默降级"
        assert result["sources"] == []
        assert result["note"] == "", "降级不写文案，前端整块不渲染"


def test_skip_on_disabled_or_sentinel_gap():
    called = {"n": 0}

    def spy(query):
        called["n"] += 1
        return [{"Title": "示例", "Url": "https://example.org/a"}]

    disabled = _verify("某争议事件", "缺当事方 11 月财报", enabled=False, search_fn=spy)
    assert disabled["status"] == "skipped"
    assert called["n"] == 0, "开关关闭时不得触发检索"

    for sentinel in ("目前无公开可核验材料", "无需补充材料", "暂无可补的拼图。", "暂无解读。"):
        result = _verify("某争议事件", sentinel, search_fn=spy)
        assert result["status"] == "skipped", sentinel
        assert result["sources"] == []
    assert called["n"] == 0, "哨兵文案不得触发检索"

    for blank in ("", "   "):
        assert _verify("某争议事件", blank, search_fn=spy)["status"] == "skipped"
    assert called["n"] == 0, "空缺口不得触发检索"


def test_mock_mode_no_network():
    called = {"n": 0}

    def spy(query):
        called["n"] += 1
        raise AssertionError("mock 模式不应触网")

    result = _verify("某争议事件", "缺当事方 11 月财报", mock_mode=True, search_fn=spy)
    assert result["status"] == "not_found"
    assert result["sources"] == []
    assert "全网核验（示例）" in result["note"]
    assert FIXED_NOW in result["note"], "示例文案必须带上核验时间"
    assert called["n"] == 0, "mock 模式绝不能调用搜索"


def test_query_builder_bounds():
    title = "如何看待「四天工作制」试点扩大，你支持在全国推广吗？"
    gap = "目前缺少：人社部 2026 年三季度工时统计原始口径，以及分行业加班时长明细"
    query = app.build_gap_query(title, gap)
    assert 0 < len(query) <= 60, len(query)
    assert "目前缺少" not in query, "引导语会稀释关键词，必须剥掉"
    assert "：" not in query, "标点应清理"

    query2 = app.build_gap_query("短标题", "还需要当事方披露 11 月财报与监管问询函")
    assert 0 < len(query2) <= 60, len(query2)
    assert "还需要" not in query2
    assert "短标题" in query2, "标题必须保留在检索式里"

    long_title = title * 3
    long_gap = gap * 3
    assert len(app.build_gap_query(long_title, long_gap)) <= 60, "超长输入必须截断到 60 字"


TESTS = (
    test_found_filters_zhihu_and_dedups,
    test_not_found_when_only_zhihu_or_empty,
    test_api_error_graceful_degrade,
    test_skip_on_disabled_or_sentinel_gap,
    test_mock_mode_no_network,
    test_query_builder_bounds,
)


def main():
    failures = 0
    for case in TESTS:
        try:
            case()
        except Exception as exc:  # noqa: BLE001 —— 测试脚本要把失败聚合成一份报告
            failures += 1
            print(f"❌ {case.__name__}：{type(exc).__name__}: {exc}")
        else:
            print(f"✅ {case.__name__}")
    if failures:
        print(f"\n失败 {failures}/{len(TESTS)} 个用例")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    main()
