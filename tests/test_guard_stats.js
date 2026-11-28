// 防幻觉透明度面板与两派原文摘录的前端行为。
// 运行：node tests/test_guard_stats.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, '..', 'demo', 'index.html'), 'utf8');
function extract(name) {
  const m = html.match(new RegExp('  function ' + name + '\\([^)]*\\) \\{[\\s\\S]*?\\n  \\}'));
  assert(m, '找不到 ' + name);
  return m[0];
}
function Node(tag) {
  this.tagName = tag; this.children = []; this.className = ''; this.textContent = '';
  const self = this; this.appendChild = function (c) { self.children.push(c); return c; };
}
function el(tag, cls, text) { const n = new Node(tag); if (cls) n.className = cls; if (text !== undefined && text !== null) n.textContent = text; return n; }
const ctx = { el, URL, document: { createTextNode: (t) => { const n = new Node('#text'); n.textContent = t; return n; } } };
vm.createContext(ctx);
vm.runInContext(extract('guardStatsLines') + '\n' + extract('isZhihuUrl') + '\n' + extract('renderSideQuote')
  + '\nthis.lines = guardStatsLines; this.quote = renderSideQuote;', ctx);

// vm 上下文里创建的数组原型属于另一个 realm，deepStrictEqual 会连原型一起比较；
// 比较前用 Array.from 拷成当前 realm 的普通数组，只比内容。
const plain = (a) => Array.from(a);

// ---- guardStatsLines ----
const lines = ctx.lines({ reading_index_dropped: 3, reading_link_dropped: 2, reading_dup_dropped: 0,
  sides_evidence_dropped: 1, sides_shared_removed: 1, dispute_downgraded: 2, dispute_no_evidence: 1,
  crosscheck_source_dropped: 0 });
assert.strictEqual(lines.length, 3, '跨平台核验为 0 时不显示该行');
assert(lines[0].includes('越界或非整数编号 3 个') && lines[0].includes('非知乎链接 2 条'));
assert(lines[1].includes('同时引用的回答 1 次'));
assert(lines[2].includes('凑不出两派而降级 2 条') && lines[2].includes('找不到对应原回答而降级 1 条'));
assert.strictEqual(ctx.lines({ crosscheck_source_dropped: 4 }).length, 4);
assert.deepStrictEqual(plain(ctx.lines(null)), [], '没有统计时不显示');
assert.deepStrictEqual(plain(ctx.lines('x')), []);
assert(ctx.lines({ reading_index_dropped: -5, reading_link_dropped: 'abc' })[0].includes('编号 0 个'), '脏值按 0 处理');

// ---- renderSideQuote ----
const q = ctx.quote({ evidence: [{ url: 'https://news.ifeng.com/x', excerpt: '站外' },
  { url: 'https://www.zhihu.com/question/1/answer/2', excerpt: '客流跟着品牌走' }] });
assert.strictEqual(q.className, 'side-quote');
assert.strictEqual(q.children[1].textContent, '「客流跟着品牌走」', '只取知乎链接的摘录');
assert.strictEqual(ctx.quote({ evidence: [{ url: 'https://www.zhihu.com/question/1/answer/2' }] }), null, '无摘录不显示');
assert.strictEqual(ctx.quote({ label: '甲' }), null, '旧数据不显示');

// ---- 接线 ----
assert(html.includes('var quoteLine = renderSideQuote(s);'));
assert(html.includes("var guardStats = (!isCurated && !isShowcase) ? data.guard_stats : null;"), '人工快照与示例页不显示统计');
console.log('guard stats tests passed');
