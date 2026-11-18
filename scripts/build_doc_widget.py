# -*- coding: utf-8 -*-
"""把计划书 v2.7 与答辩手册转成 Widget 交互页面。"""
import re, json, html as htmlmod
from pathlib import Path
from markdown_it import MarkdownIt

BASE = Path(r"D:\KimiData\kimi\tasks\2026-11-15\19-39-34-85326fd6\热榜深潜简报")
OUT = Path(r"D:\KimiData\daimon-share\daimon\agents\main\blueprint\widgets\widget_7a6efb42-f573-44eb-9fea-a84524ad55db\workspace\index.html")

md = MarkdownIt("gfm-like", {"linkify": False})

# ---------- 计划书：按 ## 分章 ----------
plan_src = (BASE / "产品说明计划书_v2.7修订版.md").read_text(encoding="utf-8")

# 去掉文档大标题（首行 # ...）与开头的信息框引用（渲染进头部 meta 区）
lines = plan_src.split("\n")
assert lines[0].startswith("# ")
doc_title = lines[0].lstrip("# ").strip()
body = "\n".join(lines[1:])

meta_lines, rest = [], []
in_quote = True
for ln in body.split("\n"):
    if in_quote and (ln.startswith(">") or ln.strip() == ""):
        if ln.startswith(">"):
            meta_lines.append(ln.lstrip("> ").strip())
        continue
    in_quote = False
    rest.append(ln)
body = "\n".join(rest).strip()

meta = [m for m in meta_lines if m and not m.startswith("注意")]
# 提取关键 meta
link = next((m for m in meta if "体验链接" in m), "")
repo = next((m for m in meta if "GitHub" in m), "")
team = next((m for m in meta if "团队成员" in m), "")
version = next((m for m in meta if "文档版本" in m), "")

# 分章：## 开头
parts = re.split(r"\n(?=## )", body)
chapters = []
for p in parts:
    p = p.strip()
    if not p:
        continue
    m = re.match(r"## (.+)", p)
    if not m:
        # 分章前的残留分隔线 / 空段，跳过
        continue
    title = m.group(1).strip()
    # 章号缩短显示
    short = re.sub(r"^(\d+\.|附录 [AB]\.).*", lambda mm: mm.group(0).split(".")[0] + (". " if mm.group(0)[0].isdigit() else ""), title)
    html = md.render(p)
    chapters.append({"title": title, "html": html})

# ---------- 答辩手册：按 ### Q 分卡 ----------
qa_src = (BASE / "答辩问答手册.md").read_text(encoding="utf-8")
q_parts = re.split(r"\n(?=### Q\d)", qa_src)
qa_cards, usage = [], None
for p in q_parts:
    p = p.strip()
    if not p:
        continue
    if p.startswith("### Q"):
        m = re.match(r"### (Q\d+)【(.+?)】(.+)", p, re.S)
        qid, tag, qtext = m.group(1), m.group(2), m.group(3).strip()
        pts_html = md.render(qtext)
        qa_cards.append({"qid": qid, "tag": tag, "html": pts_html})
    elif p.startswith("## 使用须知"):
        usage = md.render(p.split("\n", 1)[1].strip())

data_js = "const CHAPTERS = %s;\nconst QA = %s;\nconst USAGE = %s;" % (
    json.dumps(chapters, ensure_ascii=False),
    json.dumps(qa_cards, ensure_ascii=False),
    json.dumps(usage, ensure_ascii=False),
)

META = {"doc_title": doc_title, "link": link, "repo": repo, "team": team, "version": version}

page = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
  .root { font-family: var(--kimi-font-sans); padding-block-start: var(--kimi-space-4); padding-inline: var(--kimi-space-4); }
  button { font: inherit; }
  .head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .head h1 { font-size: 17px; font-weight: 500; margin: 0; }
  .meta { color: var(--kimi-color-text-tertiary); font-size: 12px; margin-top: 4px; }
  .meta a { color: var(--kimi-color-text-secondary); }
  .tabs { display: flex; gap: 4px; background: var(--kimi-color-surface-muted); border-radius: var(--kimi-radius-full); padding: 3px; }
  .tabs button { border: 0; background: transparent; color: var(--kimi-color-text-secondary); font-size: 14px; padding: 5px 14px; border-radius: var(--kimi-radius-full); cursor: pointer; transition: color var(--t-fast) var(--ease-out); }
  .tabs button[aria-selected="true"] { background: var(--kimi-color-surface-raised); color: var(--kimi-color-text-primary); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .chips button { border: 1px solid var(--kimi-color-border); background: transparent; color: var(--kimi-color-text-secondary); font-size: 12px; padding: 4px 10px; border-radius: var(--kimi-radius-chip); cursor: pointer; transition: all var(--t-fast) var(--ease-out); }
  .chips button[aria-pressed="true"] { background: var(--kimi-color-text-primary); color: var(--kimi-color-surface); border-color: var(--kimi-color-text-primary); }
  .pane { margin-top: 12px; border: 1px solid var(--kimi-color-border); border-radius: var(--kimi-radius-panel); padding: 16px 20px; max-height: 560px; overflow-y: auto; }
  .doc h2 { font-size: 17px; font-weight: 500; margin: 0 0 12px; }
  .doc h3 { font-size: 15px; font-weight: 500; margin: 20px 0 8px; }
  .doc h4 { font-size: 14px; font-weight: 500; margin: 16px 0 6px; }
  .doc p { font-size: 15px; line-height: 1.75; margin: 8px 0; }
  .doc ul, .doc ol { font-size: 15px; line-height: 1.75; padding-left: 22px; margin: 8px 0; }
  .doc blockquote { margin: 10px 0; padding: 10px 14px; border-left: 3px solid var(--kimi-color-text-quaternary); color: var(--kimi-color-text-secondary); font-size: 14px; background: var(--kimi-color-surface-muted); border-radius: 0 var(--kimi-radius-chip) var(--kimi-radius-chip) 0; }
  .doc blockquote p { font-size: 14px; }
  .doc table { border-collapse: collapse; width: 100%; font-size: 14px; margin: 10px 0; }
  .doc th, .doc td { border: 1px solid var(--kimi-color-border); padding: 7px 10px; text-align: left; vertical-align: top; line-height: 1.6; }
  .doc th { background: var(--kimi-color-surface-muted); font-weight: 500; }
  .doc code { font-family: var(--kimi-font-mono); font-size: 13px; background: var(--kimi-color-surface-muted); padding: 1px 5px; border-radius: 4px; }
  .doc pre { background: var(--kimi-color-surface-muted); border-radius: var(--kimi-radius-chip); padding: 12px 14px; overflow-x: auto; font-size: 13px; line-height: 1.6; }
  .doc pre code { background: none; padding: 0; }
  .doc hr { border: 0; border-top: 1px solid var(--kimi-color-border); margin: 16px 0; }
  .doc a { color: var(--kimi-color-text-primary); }
  .doc strong { font-weight: 500; }
  .qa-item { border: 1px solid var(--kimi-color-border); border-radius: var(--kimi-radius-panel); margin-bottom: 8px; overflow: hidden; }
  .qa-q { width: 100%; display: flex; align-items: center; gap: 10px; padding: 12px 14px; background: transparent; border: 0; cursor: pointer; text-align: left; color: var(--kimi-color-text-primary); font-size: 14px; line-height: 1.55; }
  .qa-q:hover { background: var(--kimi-color-surface-muted); }
  .qa-q .caret { margin-left: auto; color: var(--kimi-color-text-quaternary); transition: transform var(--t-fast) var(--ease-out); flex: none; }
  .qa-item[data-open="true"] .caret { transform: rotate(90deg); }
  .qa-tag { flex: none; font-size: 12px; color: var(--kimi-color-text-secondary); border: 1px solid var(--kimi-color-border); border-radius: var(--kimi-radius-chip); padding: 2px 8px; }
  .qa-qid { flex: none; font-size: 12px; color: var(--kimi-color-text-quaternary); font-variant-numeric: tabular-nums; }
  .qa-a { display: none; border-top: 1px solid var(--kimi-color-border); padding: 4px 16px 12px; }
  .qa-item[data-open="true"] .qa-a { display: block; }
  .qa-a ol { padding-left: 20px; margin: 10px 0; font-size: 14px; line-height: 1.75; }
  .qa-a li { margin: 6px 0; }
  .hidden { display: none; }
  .usage { margin-top: 12px; font-size: 13px; color: var(--kimi-color-text-secondary); }
  .usage ul { padding-left: 18px; line-height: 1.7; }
</style>
</head>
<body>
<div class="root">
  <div class="head">
    <div>
      <h1>__TITLE__</h1>
      <div class="meta">__VERSION__ · <a href="__LINKURL__" target="_blank" rel="noopener">公网体验链接</a> · <a href="__REPOURL__" target="_blank" rel="noopener">GitHub 仓库</a></div>
    </div>
    <div class="tabs" role="tablist" aria-label="文档切换">
      <button role="tab" id="tab-plan" aria-selected="true" aria-controls="view-plan">计划书 v2.7</button>
      <button role="tab" id="tab-qa" aria-selected="false" aria-controls="view-qa">答辩手册 · 10 问</button>
    </div>
  </div>

  <div id="view-plan" role="tabpanel" aria-labelledby="tab-plan">
    <div class="chips" id="chips"></div>
    <div class="pane doc" id="plan-pane"></div>
  </div>

  <div id="view-qa" role="tabpanel" aria-labelledby="tab-qa" class="hidden">
    <div id="qa-list"></div>
    <div class="usage" id="qa-usage"></div>
  </div>
</div>

<script>
__DATA__

const chipsEl = document.getElementById('chips');
const paneEl = document.getElementById('plan-pane');
CHAPTERS.forEach((c, i) => {
  const label = c.title.replace(/^#{2,3}\\s*/, '');
  const b = document.createElement('button');
  b.textContent = label.length > 14 ? label.slice(0, 14) + '…' : label;
  b.title = label;
  b.setAttribute('aria-pressed', i === 0 ? 'true' : 'false');
  b.addEventListener('click', () => {
    chipsEl.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', 'false'));
    b.setAttribute('aria-pressed', 'true');
    paneEl.innerHTML = c.html;
    paneEl.scrollTop = 0;
  });
  chipsEl.appendChild(b);
});
paneEl.innerHTML = CHAPTERS[0].html;

const qaList = document.getElementById('qa-list');
QA.forEach((q, i) => {
  const item = document.createElement('div');
  item.className = 'qa-item';
  item.dataset.open = i === 0 ? 'true' : 'false';
  const qtext = q.html.replace(/<ol>[\\s\\S]*?<\\/ol>/, '').replace(/<[^>]+>/g, '').trim();
  item.innerHTML =
    '<button class="qa-q" aria-expanded="' + (i === 0) + '">' +
    '<span class="qa-qid">' + q.qid + '</span>' +
    '<span class="qa-tag">' + q.tag + '</span>' +
    '<span>' + qtext + '</span>' +
    '<span class="caret">›</span></button>' +
    '<div class="qa-a">' + q.html + '</div>';
  item.querySelector('.qa-q').addEventListener('click', () => {
    const open = item.dataset.open === 'true';
    item.dataset.open = String(!open);
    item.querySelector('.qa-q').setAttribute('aria-expanded', String(!open));
  });
  qaList.appendChild(item);
});
if (USAGE) document.getElementById('qa-usage').innerHTML = USAGE;

const tabPlan = document.getElementById('tab-plan');
const tabQa = document.getElementById('tab-qa');
const viewPlan = document.getElementById('view-plan');
const viewQa = document.getElementById('view-qa');
function switchTab(toQa) {
  tabPlan.setAttribute('aria-selected', String(!toQa));
  tabQa.setAttribute('aria-selected', String(toQa));
  viewPlan.classList.toggle('hidden', toQa);
  viewQa.classList.toggle('hidden', !toQa);
}
tabPlan.addEventListener('click', () => switchTab(false));
tabQa.addEventListener('click', () => switchTab(true));
</script>
</body>
</html>
"""

def extract_url(s):
    m = re.search(r"https?://[^\s）)]+", s)
    return m.group(0) if m else "#"

page = page.replace("__TITLE__", "热榜深潜简报 · 参赛文档交互台")
page = page.replace("__VERSION__", "产品说明计划书 v2.7 · 2026-11-16")
page = page.replace("__LINKURL__", extract_url(META["link"]))
page = page.replace("__REPOURL__", extract_url(META["repo"]))
page = page.replace("__DATA__", data_js)

OUT.write_text(page, encoding="utf-8")
print("chapters:", len(chapters), "| qa:", len(qa_cards), "| size:", OUT.stat().st_size)
for c in chapters:
    print(" -", c["title"][:40])
