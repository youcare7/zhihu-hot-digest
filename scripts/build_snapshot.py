# -*- coding: utf-8 -*-
"""生成「离线快照 HTML」：拉取线上 /data.js 真实数据，内嵌进页面，产出可双击离线打开的单文件。

用法（在仓库根目录）：
    python scripts/build_snapshot.py

产出：项目根目录 热榜深潜简报_离线快照_<年月日_时分>.html（同时清掉旧快照，只保留最新一份）
说明：快照数据是冻结的，不会随线上更新；正式体验以公网链接为准。
"""
import datetime
import json
import pathlib
import re
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "demo" / "index.html"
BASE_URL = "https://zhihu-hot-digest-348140-5-1533039364.sh.run.tcloudbase.com"
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))

# index.html 的脚本标签带缓存击穿查询串（data.js?v=2 / timeline.js?v=1），
# 锚点必须容忍可选查询串，否则 str.replace 静默失败、快照拿不到数据。
DATA_TAG_RE = re.compile(r'<script src="data\.js[^"]*"></script>')
TIMELINE_TAG_RE = re.compile(r'<script src="timeline\.js[^"]*"></script>')


def inline_payload(html, pattern, var_name, raw, label):
    """把线上 JS 的内容内联进 HTML，替换同名外链标签；找不到锚点必须报错而非静默跳过。"""
    m = re.match(r"window\." + re.escape(var_name) + r" = (.*);", raw, re.S)
    if not m:
        raise SystemExit(f"线上 /{label}.js 格式异常，未生成快照")
    json.loads(m.group(1))  # 提前校验，避免把坏 JSON 内联进快照
    replacement = '<script>window.' + var_name + ' = ' + m.group(1) + ';</script>'
    snapshot, count = pattern.subn(lambda _: replacement, html, count=1)
    if count != 1:
        raise SystemExit(f"{label}.js 脚本标签未找到（count={count}），未生成快照")
    return snapshot, m.group(1)


def main() -> None:
    html = INDEX.read_text(encoding="utf-8")
    data_raw = urllib.request.urlopen(BASE_URL + "/data.js", timeout=60).read().decode("utf-8")
    timeline_raw = urllib.request.urlopen(BASE_URL + "/timeline.js", timeout=60).read().decode("utf-8")

    snapshot, _ = inline_payload(html, DATA_TAG_RE, "BRIEF_DATA", data_raw, "data")
    # 合订本是核心功能：快照必须同时内联 timeline.js，否则离线时 topic 恒空。
    snapshot, _ = inline_payload(snapshot, TIMELINE_TAG_RE, "TIMELINE_DATA", timeline_raw, "timeline")

    data = json.loads(re.match(r"window\.BRIEF_DATA = (.*);", data_raw, re.S).group(1))
    if not isinstance(data.get("items"), list) or not data["items"]:
        raise SystemExit("线上数据为空，未生成快照")

    now = datetime.datetime.now(CN_TZ)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    annotate = ('<script>window.addEventListener("load",function(){'
                'var b=document.getElementById("badge");'
                'if(b){b.textContent+=" · 离线快照";'
                'b.title="本文件为离线快照（数据截至 ' + stamp + '，北京时间），正式体验以公网链接为准";}});</script>')
    # 离线说明注释追加在 body 末尾，避免打断脚本标签顺序。
    snapshot = snapshot.replace('</body>',
        '<!-- 离线快照：数据截至 ' + stamp + '（北京时间，UTC+8），由线上真实缓存导出；正式体验以公网链接为准 -->\n'
        + annotate + '\n</body>', 1)
    if '离线快照' not in snapshot or annotate not in snapshot:
        raise SystemExit("离线快照标注注入失败，未生成快照")

    for old in ROOT.glob("热榜深潜简报_离线快照_*.html"):
        old.unlink()
    out = ROOT / f"热榜深潜简报_离线快照_{now:%Y%m%d_%H%M}.html"
    out.write_text(snapshot, encoding="utf-8")

    ok = sum(1 for it in data["items"] if it.get("background") and it["background"] != "暂无解读。")
    links = sum(len(it.get("reading", [])) for it in data["items"])
    print(f"已生成: {out}")
    print(f"数据基准: source={data['source']} 条目={len(data['items'])} 解读成功={ok} 延伸阅读={links}")


if __name__ == "__main__":
    main()
