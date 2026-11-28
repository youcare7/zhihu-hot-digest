// 缺口的「站内相关讨论」：只渲染知乎链接；如实注明「未核实是否补上了这块拼图」；示例数据不可点。
// 运行：node tests/test_gap_link.js
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
  this.tagName = tag; this.children = []; this.className = ''; this.textContent = ''; this.href = '';
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
  const ctx = { el, isDemo, safeHref: (u) => u, demoTag: () => el('span', 'tag-demo', '示例'), URL };
  vm.createContext(ctx);
  vm.runInContext(extract('isZhihuUrl') + '\n' + extract('renderGapLink') + '\nthis.render = renderGapLink;', ctx);
  return ctx.render;
}
const allText = (n) => (n.textContent || '') + n.children.map(allText).join('');

const render = load(false);
const link = { title: 'GB 55038-2025 楼板撞击声怎么测', url: 'https://zhuanlan.zhihu.com/p/2104568979272495698',
  author: '沐峰', excerpt: '第 6.1.2 条规定……', matched: ['GB 55038-2025', '配套检测方法'] };
const box = render({ gap_link: link });
assert.strictEqual(box.className, 'gap-link');
const a = box.children.find((c) => c.tagName === 'a');
assert(a, '应渲染可点链接');
assert.strictEqual(a.href, link.url);
assert.strictEqual(a.target, '_blank');
const text = allText(box);
assert(text.includes('站内相关讨论'), '标签必须写「站内相关讨论」');
assert(text.includes('未核实是否补上了这块拼图'), '必须如实说明程序未核实');
assert(!text.includes('已补上'), '不得声称拼图已补上');
assert(text.includes('命中关键词：GB 55038-2025、配套检测方法'));

assert.strictEqual(render({}), null, '没有 gap_link 不渲染');
assert.strictEqual(render(null), null);
assert.strictEqual(render({ gap_link: 'x' }), null);
assert.strictEqual(render({ gap_link: Object.assign({}, link, { url: 'https://news.ifeng.com/c/x' }) }), null, '站外链接不渲染');
assert.strictEqual(render({ gap_link: Object.assign({}, link, { url: 'https://zhihu.com.evil.cn/p/1' }) }), null, '仿冒域名不渲染');
assert.strictEqual(render({ gap_link: Object.assign({}, link, { title: '' }) }), null);

const demoBox = load(true)({ gap_link: link });
assert.strictEqual(demoBox.children.filter((c) => c.tagName === 'a').length, 0, '示例数据链接不可点');

assert(html.includes('var gapLink = renderGapLink(item);'), '缺口卡片必须调用 renderGapLink');
assert(!html.includes('gap-ask'), '追问按钮已撤掉');
console.log('test_gap_link.js: OK');
