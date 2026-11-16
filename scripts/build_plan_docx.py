# -*- coding: utf-8 -*-
"""把《产品说明计划书》Markdown 渲染为排版规范的 .docx。

排版约定（来自封版评审的排版护栏）：
- 代码块 / 树状图：Consolas 9pt + 浅灰底色框，保持换行，防表格跨页变形靠导出 PDF
- 表格：网格边框，表头加粗底纹
- 正文：宋体 10.5pt；标题黑体；引用块左侧缩进灰色
- 页脚页码 + 文首目录域

用法：python scripts/build_plan_docx.py [计划书.md 路径]（缺省用 v2.2）
"""
import re
import sys
import pathlib

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

MD = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(
    r"D:\KimiData\kimi\tasks\2026-11-15\19-39-34-85326fd6\热榜深潜简报\产品说明计划书_v2.2修订版.md")
OUT = MD.with_suffix(".docx")

GRAY = RGBColor(0x59, 0x59, 0x59)
LIGHT_GRAY_FILL = "F2F2F2"
HEADER_FILL = "D9D9D9"


def set_font(run, name_cn, name_en, size, bold=False, color=None):
    run.font.name = name_en
    run.font.size = Pt(size)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name_cn)
    rfonts.set(qn("w:ascii"), name_en)
    rfonts.set(qn("w:hAnsi"), name_en)
    if color is not None:
        run.font.color.rgb = color


def shade_para(p, fill):
    ppr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    ppr.append(shd)


def shade_cell(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def add_runs_with_inline(p, text, size=10.5, cn="宋体", en="Times New Roman",
                         bold=False, color=None):
    """解析 **bold** 与 `code` 行内标记。"""
    tokens = re.split(r"(\*\*.+?\*\*|`[^`]+`)", text)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**"):
            r = p.add_run(tok[2:-2])
            set_font(r, cn, en, size, bold=True, color=color)
        elif tok.startswith("`") and tok.endswith("`"):
            r = p.add_run(tok[1:-1])
            set_font(r, cn, "Consolas", size - 0.5, bold=bold, color=color)
        else:
            r = p.add_run(tok)
            set_font(r, cn, en, size, bold=bold, color=color)


def add_code_block(doc, lines):
    for ln in lines:
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.left_indent = Cm(0.4)
        pf.line_spacing = 1.0
        shade_para(p, LIGHT_GRAY_FILL)
        r = p.add_run(ln if ln else " ")
        set_font(r, "宋体", "Consolas", 9)
    # 代码块后补一个窄间距空段
    tail = doc.add_paragraph()
    tail.paragraph_format.space_before = Pt(0)
    tail.paragraph_format.space_after = Pt(6)
    tr = tail.add_run("")
    set_font(tr, "宋体", "Times New Roman", 4)


def add_table(doc, rows):
    ncol = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=ncol)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for i, row in enumerate(rows):
        for j in range(ncol):
            cell = table.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            txt = row[j] if j < len(row) else ""
            if i == 0:
                shade_cell(cell, HEADER_FILL)
                add_runs_with_inline(p, txt, size=9.5, bold=True)
            else:
                add_runs_with_inline(p, txt, size=9.5)
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
    doc.add_paragraph()


def add_quote(doc, lines):
    for ln in lines:
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.left_indent = Cm(0.75)
        pf.space_before = Pt(2)
        pf.space_after = Pt(2)
        shade_para(p, "F7F7F7")
        add_runs_with_inline(p, ln, size=10, color=GRAY)


def add_heading(doc, text, level):
    p = doc.add_paragraph()
    sizes = {1: 18, 2: 15, 3: 12.5}
    pf = p.paragraph_format
    pf.space_before = Pt(14 if level == 2 else 10)
    pf.space_after = Pt(8 if level == 2 else 6)
    if level == 1:
        pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_runs_with_inline(p, text, size=sizes[level], cn="黑体", en="Arial",
                         bold=True, color=RGBColor(0, 0, 0))
    # 给二、三级标题挂大纲级别，供目录域识别
    if level in (2, 3):
        ppr = p._p.get_or_add_pPr()
        ol = OxmlElement("w:outlineLvl")
        ol.set(qn("w:val"), str(level - 1))
        ppr.append(ol)
    return p


def add_toc(doc):
    p = doc.add_paragraph()
    r = p.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-2" \\h \\z \\u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    t = OxmlElement("w:t")
    t.text = "（目录：在 Word 中右键此处 → 更新域，即可生成/刷新页码）"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    r._r.append(fld_begin)
    r._r.append(instr)
    r._r.append(fld_sep)
    r._r.append(t)
    r._r.append(fld_end)
    set_font(r, "宋体", "Times New Roman", 9, color=GRAY)


def add_footer_pagenum(doc):
    section = doc.sections[0]
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r1 = p.add_run("第 ")
    set_font(r1, "宋体", "Times New Roman", 9, color=GRAY)
    r = p.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    r._r.append(fld_begin)
    r._r.append(instr)
    r._r.append(fld_end)
    set_font(r, "宋体", "Times New Roman", 9, color=GRAY)
    r2 = p.add_run(" 页")
    set_font(r2, "宋体", "Times New Roman", 9, color=GRAY)


def main():
    lines = MD.read_text(encoding="utf-8").splitlines()
    doc = Document()
    # 页边距
    for s in doc.sections:
        s.top_margin = Cm(2.5)
        s.bottom_margin = Cm(2.5)
        s.left_margin = Cm(2.8)
        s.right_margin = Cm(2.8)

    add_footer_pagenum(doc)

    i = 0
    first_h1_done = False
    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()

        # 代码块
        if stripped.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1  # 跳过收尾 ```
            add_code_block(doc, block)
            continue

        # 表格
        if stripped.startswith("|") and stripped.endswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                add_table(doc, rows)
            continue

        # 引用块
        if stripped.startswith(">"):
            qlines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                content = lines[i].strip()[1:].strip()
                if content:
                    qlines.append(content)
                i += 1
            add_quote(doc, qlines)
            continue

        # 标题
        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if level == 1 and not first_h1_done:
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(6)
                add_runs_with_inline(p, title, size=20, cn="黑体", en="Arial", bold=True)
                first_h1_done = True
                add_toc(doc)
            else:
                add_heading(doc, title, level)
            i += 1
            continue

        # 分隔线
        if re.fullmatch(r"-{3,}", stripped):
            i += 1
            continue

        # 列表
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
        if m and stripped:
            indent, marker, content = m.groups()
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.left_indent = Cm(0.75)
            pf.first_line_indent = Cm(-0.4)
            pf.space_before = Pt(2)
            pf.space_after = Pt(2)
            bullet = "• " if marker in ("-", "*") else marker + " "
            content = content.replace("[ ]", "☐").replace("[x]", "☑")
            r = p.add_run(bullet)
            set_font(r, "宋体", "Times New Roman", 10.5)
            add_runs_with_inline(p, content)
            i += 1
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 普通段落
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(2)
        pf.space_after = Pt(4)
        pf.line_spacing = 1.3
        pf.first_line_indent = Cm(0.74)
        add_runs_with_inline(p, stripped)
        i += 1

    doc.save(OUT)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
