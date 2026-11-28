// 两派观点「原回答」链接：只渲染知乎链接，旧数据（无 evidence）不渲染，示例数据不可点。
// 运行：node tests/test_sides_evidence.js
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
  this.tagName = tag; this.children = []; this.className = ''; this.textContent = ''; this.href = ''; this.title = '';
  const self = this;
  this.appendChild = function (c) { self.children.push(c); return c; };
}
function el(tag, cls, text) {
  const n = new Node(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function load(isDemo) {
  const ctx = {
    el, isDemo, safeHref: (u) => u, demoTag: () => el('span', 'tag-demo', '示例'),
    document: { createTextNode: (t) => { const n = new Node('#text'); n.textContent = t; return n; } },
    URL,
  };
  vm.createContext(ctx);
  vm.runInContext(extract('isZhihuUrl') + '\n' + extract('renderSideEvidence')
    + '\nthis.render = renderSideEvidence;', ctx);
  return ctx.render;
}

const render = load(false);
const box = render({ label: '甲', claim: '一', evidence: [
  { title: '问题标题', url: 'https://www.zhihu.com/question/1/answer/11' },
  { title: '问题标题', url: 'https://news.ifeng.com/c/x' },
  { title: '问题标题', url: 'https://www.zhihu.com/question/1/answer/12' },
] });
const links = box.children.filter((c) => c.tagName === 'a');
assert.strictEqual(box.className, 'side-src');
assert.deepStrictEqual(links.map((a) => a.href),
  ['https://www.zhihu.com/question/1/answer/11', 'https://www.zhihu.com/question/1/answer/12'], '站外链接不渲染');
assert.deepStrictEqual(links.map((a) => a.textContent), ['原回答 1', '原回答 2']);
assert.strictEqual(links[0].title, '问题标题');

assert.strictEqual(render({ label: '甲', claim: '一' }), null, '旧数据无 evidence 不渲染');
assert.strictEqual(render({ label: '甲', claim: '一', evidence: [] }), null);
assert.strictEqual(render({ label: '甲', claim: '一', evidence: [{ url: 'https://news.ifeng.com/c/x' }] }), null);
assert.strictEqual(render(null), null);

const demoBox = load(true)({ label: '甲', claim: '一', evidence: [{ title: 't', url: 'https://www.zhihu.com/question/1/answer/11' }] });
assert.strictEqual(demoBox.children.filter((c) => c.tagName === 'a').length, 0, '示例数据链接不可点');

assert(html.includes('var srcLine = renderSideEvidence(s);'), '两派卡片必须调用 renderSideEvidence');
console.log('sides evidence tests passed');
