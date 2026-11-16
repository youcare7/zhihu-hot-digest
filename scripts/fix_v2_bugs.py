# -*- coding: utf-8 -*-
"""
修复 v2 改造遗留的 4 个缺陷（幂等，可重复运行）。

用法：
    python scripts/fix_v2_bugs.py <项目根目录>

    # 例：
    python scripts/fix_v2_bugs.py .
    python scripts/fix_v2_bugs.py "D:\\KimiData\\kimi\\tasks\\2026-11-15\\19-39-34-85326fd6\\热榜深潜简报"

修什么：
    FIX-1  app.py 第 422 行多了一个 ")，导致 SyntaxError（致命：服务起不来）
    FIX-2  app.py 缺 REASON_MAX 常量
    FIX-3  assemble_reading 里 reason 截断写死 [:60]，与 PROMPT 要求「≤30 字」不一致
    FIX-4  build_digest 的 insight 文案写死「推测对推测、观感对观感、价值判断」三类，
           当某类计数为 0 时文案仍在声称存在该类分歧（后端确定性文案说谎）

设计原则：
    · 每处替换都断言锚点唯一出现（count == 1），锚点漂移立即中止，绝不静默改错位置
    · 幂等：如果已经是修好的状态，跳过并报告 SKIP，不重复改
    · 保留原文件的换行风格（本项目是 CRLF）
    · 改完立刻 compile() 验证语法

退出码：0 = 全部成功（含幂等跳过）；1 = 有锚点漂移或语法仍错；2 = 路径/读取失败。
"""
import sys
import pathlib


FIXES = []


def _norm(text):
    """统一成 \n，便于写锚点。"""
    return text.replace("\r\n", "\n")


def _restore(text, crlf):
    return text.replace("\n", "\r\n") if crlf else text


def fix_1_broken_quote(text):
    """FIX-1：raise UpstreamError(...)")  →  raise UpstreamError(...)"""
    # 注意：good 是 bad 的字符串前缀，所以 bad 存在时 good 必然也命中。
    # 判定只看 bad：bad 出现 1 次 → 修；bad 出现 0 次 → 再确认 good 恰好 1 次才算已修好。
    bad = 'raise UpstreamError("搜索无候选回答，按 FR-07/FR-04 降级")")'
    good = 'raise UpstreamError("搜索无候选回答，按 FR-07/FR-04 降级")'
    n_bad = text.count(bad)
    if n_bad == 1:
        return text.replace(bad, good, 1), 'OK（删除多余的 " )）'
    if n_bad == 0:
        n_good = text.count(good)
        if n_good == 1:
            return text, "SKIP（已是正确写法）"
        raise SystemExit(
            f"[FIX-1] 既找不到错误写法，也找不到唯一的正确写法（good={n_good}）。"
            f"请人工检查 app.py 里 raise UpstreamError 附近。"
        )
    raise SystemExit(
        f"[FIX-1] 锚点异常：错误写法出现 {n_bad} 次，预期恰好 1 次。请人工检查。"
    )


def fix_2_reason_max_const(text):
    """FIX-2：在 GAP_MAX = 100 之后补 REASON_MAX = 30"""
    anchor = "GAP_MAX = 100\n"
    new_line = "REASON_MAX = 30                # 延伸阅读推荐理由上限，与 PROMPT「reason ≤30 字」一致\n"
    if "REASON_MAX" in text:
        return text, "SKIP（REASON_MAX 已存在）"
    if text.count(anchor) != 1:
        raise SystemExit(
            f"[FIX-2] 锚点 'GAP_MAX = 100' 出现 {text.count(anchor)} 次，预期 1 次。"
        )
    return text.replace(anchor, anchor + new_line, 1), "OK（新增 REASON_MAX = 30）"


def fix_3_reason_clip(text):
    """FIX-3：reason 截断 [:60] → _clip(..., REASON_MAX)"""
    bad = '"reason": str(pick.get("reason", ""))[:60],'
    good = '"reason": _clip(pick.get("reason", ""), REASON_MAX),'
    n_bad = text.count(bad)
    n_good = text.count(good)
    if n_bad == 0 and n_good == 1:
        return text, "SKIP（已用 _clip + REASON_MAX）"
    if n_bad != 1:
        raise SystemExit(
            f"[FIX-3] 锚点异常：旧写法出现 {n_bad} 次，新写法出现 {n_good} 次。预期旧写法恰好 1 次。"
        )
    if "def _clip(" not in text:
        raise SystemExit("[FIX-3] app.py 里找不到 def _clip(，无法改用 _clip，请人工处理。")
    return text.replace(bad, good, 1), "OK（reason 截断 60 → REASON_MAX=30，改用 _clip）"


def fix_4_digest_dynamic(text):
    """FIX-4：insight 文案按实际非零类别动态拼句"""
    bad = (
        '        if decidable and soft:\n'
        '            insights.append(\n'
        '                f"{dispute} 条真分歧里，只有 {decidable} 条是补数据就能裁决的事实之争；"\n'
        '                f"其余 {soft} 条分别属于推测对推测、观感对观感、价值判断——吵得再久也不会有答案。"\n'
        '            )\n'
    )
    good = (
        '        if decidable and soft:\n'
        '            # 按 claim_counts 里实际非零的类别动态拼句：\n'
        '            # 曾出现 impression=0 仍写「观感对观感」的情况——后端确定性文案不允许说谎。\n'
        '            kinds = [\n'
        '                CLAIM_TYPE_NAMES[k] for k in VALID_CLAIM_TYPES\n'
        '                if k != "fact" and claim_counts[k]\n'
        '            ]\n'
        '            kinds_text = "、".join(kinds) if kinds else "非事实之争"\n'
        '            insights.append(\n'
        '                f"{dispute} 条真分歧里，只有 {decidable} 条是补数据就能裁决的事实之争；"\n'
        '                f"其余 {soft} 条属于{kinds_text}——吵得再久也不会有答案。"\n'
        '            )\n'
    )
    n_bad = text.count(bad)
    n_good = text.count(good)
    if n_bad == 0 and n_good == 1:
        return text, "SKIP（已改为动态拼句）"
    if n_bad != 1:
        raise SystemExit(
            f"[FIX-4] 锚点异常：旧写法出现 {n_bad} 次，新写法出现 {n_good} 次。预期旧写法恰好 1 次。"
        )
    return text.replace(bad, good, 1), "OK（insight 文案改为按实际类别动态生成）"


STEPS = [
    ("FIX-1", "app.py 语法错误（多余 \" )）", fix_1_broken_quote),
    ("FIX-2", "补 REASON_MAX 常量", fix_2_reason_max_const),
    ("FIX-3", "reason 截断 60 → 30", fix_3_reason_clip),
    ("FIX-4", "digest insight 文案动态拼句", fix_4_digest_dynamic),
]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = pathlib.Path(sys.argv[1]).resolve()
    app = root / "app.py"
    if not app.is_file():
        print(f"[错误] 找不到 {app}")
        return 2

    raw = app.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = _norm(raw)
    print(f"目标：{app}")
    print(f"换行风格：{'CRLF' if crlf else 'LF'}   原始字符数：{len(raw)}")
    print("-" * 72)

    failed = False
    for code, desc, fn in STEPS:
        try:
            text, msg = fn(text)
            print(f"  [{code}] {desc:34s} {msg}")
            FIXES.append((code, msg))
        except SystemExit as e:
            print(f"  [{code}] {desc:34s} FAIL — {e}")
            failed = True

    if failed:
        print("-" * 72)
        print("存在未修复项，未写入文件。")
        return 1

    out = _restore(text, crlf)
    try:
        compile(out, str(app), "exec")
    except SyntaxError as e:
        print("-" * 72)
        print(f"[失败] 修改后语法仍错：L{e.lineno} {e.msg}  {(e.text or '').strip()}")
        print("未写入文件。")
        return 1

    app.write_bytes(out.encode("utf-8"))
    print("-" * 72)
    print(f"已写入：{len(out)} 字符（原 {len(raw)}）")
    print("语法校验：OK")
    print()
    print("下一步请跑冒烟：")
    print(f'  cd "{root}"')
    print("  MOCK_MODE=1 python app.py     # 然后访问 http://127.0.0.1:5000/data.js")
    print("  python scripts/verify_plan_numbers_v2.py data/brief_$(date +%Y-%m-%d).json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
