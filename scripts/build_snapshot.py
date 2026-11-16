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


def main() -> None:
    html = INDEX.read_text(encoding="utf-8")
    raw = urllib.request.urlopen(BASE_URL + "/data.js", timeout=60).read().decode("utf-8")
    m = re.match(r"window\.BRIEF_DATA = (.*);", raw, re.S)
    if not m:
        raise SystemExit("线上 /data.js 格式异常，未生成快照")
    data = json.loads(m.group(1))
    if not isinstance(data.get("items"), list) or not data["items"]:
        raise SystemExit("线上数据为空，未生成快照")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    annotate = ('<script>window.addEventListener("load",function(){'
                'var b=document.getElementById("badge");'
                'if(b){b.textContent+=" · 离线快照";'
                'b.title="本文件为离线快照（数据截至 ' + stamp + '，北京时间），正式体验以公网链接为准";}});</script>')
    snapshot = html.replace(
        '<script src="data.js"></script>',
        '<script>window.BRIEF_DATA = ' + m.group(1) + ';</script>\n'
        '<!-- 离线快照：数据截至 ' + stamp + '（北京时间），由线上真实缓存导出；正式体验以公网链接为准 -->\n'
        + annotate,
    )

    for old in ROOT.glob("热榜深潜简报_离线快照_*.html"):
        old.unlink()
    out = ROOT / f"热榜深潜简报_离线快照_{datetime.datetime.now():%Y%m%d_%H%M}.html"
    out.write_text(snapshot, encoding="utf-8")

    ok = sum(1 for it in data["items"] if it.get("background") and it["background"] != "暂无解读。")
    links = sum(len(it.get("reading", [])) for it in data["items"])
    print(f"已生成: {out}")
    print(f"数据基准: source={data['source']} 条目={len(data['items'])} 解读成功={ok} 延伸阅读={links}")


if __name__ == "__main__":
    main()
