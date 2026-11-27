// 跨平台核验面板：「站外讨论」标签、可选字段空值不渲染、兼容旧人工快照的 weibo 字段。
// 运行：node tests/test_crosscheck_panel.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const plain = (a) => Array.from(a);

const html = fs.readFileSync(path.join(__dirname, '..', 'demo', 'index.html'), 'utf8');

function extract(name) {
  const re = new RegExp('  function ' + name + '\\([^)]*\\) \\{[\\s\\S]*?\\n  \\}');
  const m = html.match(re);
  assert(m, '找不到 ' + name);
  return m[0];
}

// ---- 1. xpRows：纯函数行为 ----
const ctx = {};
vm.createContext(ctx);
vm.runInContext(extract('xpRows') + '\nthis.xpRows = xpRows;', ctx);
const xpRows = ctx.xpRows;

const legacy = xpRows({ weibo: '一街之隔两重天刷屏', zhihu: '拆商圈形成机制', fact: '实地走访核实' });
assert.deepStrictEqual(plain(legacy.map(r => r.label)), ['站外讨论', '知乎'], '旧快照 weibo 字段应显示为「站外讨论」');
assert.strictEqual(legacy[0].text, '一街之隔两重天刷屏');
assert.strictEqual(legacy[0].key, 'weibo');

assert.deepStrictEqual(plain(xpRows({ fact: '只有事实' })), [], '缺少 weibo / zhihu 时不渲染讨论行');
assert.deepStrictEqual(plain(xpRows({ weibo: '   ', zhihu: '', fact: 'x' })), [], '空白值不渲染');
assert.deepStrictEqual(plain(xpRows({ zhihu: '只有知乎' }).map(r => r.label)), ['知乎']);
assert.deepStrictEqual(plain(xpRows(null)), []);
assert.deepStrictEqual(plain(xpRows(undefined)), []);

// ---- 2. makeStamp：真实渲染路径 ----
function Node(tag) {
  this.tagName = tag; this.children = []; this.className = ''; this.textContent = '';
  this.attrs = {}; const self = this;
  this.classList = { toggle() { return true; }, add() {}, remove() {}, contains() { return false; } };
  this.appendChild = function (c) { self.children.push(c); return c; };
  this.setAttribute = function (k, v) { self.attrs[k] = v; };
  this.addEventListener = function () {};
}
function el(tag, cls, text) {
  const n = new Node(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}
const rctx = {
  el, safeHref: (u) => u, XP_KIND: '程序生成', XP_HEAD: '程序生成 · 测试',
  document: { createTextNode: (t) => { const n = new Node('#text'); n.textContent = t; return n; } },
};
vm.createContext(rctx);
vm.runInContext(extract('xpRows') + '\n' + extract('makeStamp') + '\nthis.makeStamp = makeStamp;', rctx);

function tags(wrap) {
  const panel = wrap.children.find(c => c.className === 'xpanel');
  if (!panel) return null;
  return panel.children.filter(c => c.className.indexOf('xp-row') === 0)
    .map(row => row.children[0].textContent);
}

assert.deepStrictEqual(
  tags(rctx.makeStamp('hot', '吵翻天', { crosscheck: { weibo: '旧', zhihu: '旧', fact: '旧', sources: [] } })),
  ['站外讨论', '知乎', '核验'], '旧快照三行都显示，微博改名为站外讨论');
assert.deepStrictEqual(
  tags(rctx.makeStamp('miss', '缺证据', { crosscheck: { fact: '只核实到媒体报道', sources: [{ name: 'a', url: 'https://x.example' }] } })),
  ['核验'], '程序生成的核验只有 fact 时只显示核验行');
assert.strictEqual(tags(rctx.makeStamp('calm', '有定论', {})), null, '无 crosscheck 不生成面板');

// ---- 3. 页面不再出现「微博」标签与「微博情绪」入口文案 ----
assert(!html.includes("['微博', 'weibo'"), '面板标签不应再写死「微博」');
assert(!html.includes('微博情绪'), '入口按钮不应再写「微博情绪」');

console.log('crosscheck panel tests passed');
