// 求真标签插件：线上数据解析、判读转印章、离线兜底、内容脚本接线。
// 运行：node tests/test_extension.js
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ext = require(path.join(__dirname, '..', 'extension', 'background.js'));

// ---- parseDataJs ----
const brief = { date: '2026-11-26', source: 'cache', generated_at: '2026-11-26 21:00', items: [
  { rank: 1, title: '楼板该不该加厚', type: 'dispute', question_url: 'https://www.zhihu.com/question/2066353729507422598',
    punch: '锐评', quotes: ['一', '二'], sides: [{ label: '加厚派' }, { label: '浮筑派' }] },
  { rank: 2, title: '待验证一条', type: 'verify', question_url: 'https://www.zhihu.com/question/2' },
  { rank: 3, title: '共识一条', type: 'consensus', question_url: 'https://www.zhihu.com/question/3' },
  { rank: 4, title: '已实锤', type: 'verify', question_url: 'https://www.zhihu.com/question/4', web_evidence: { status: 'found' } },
  { rank: 5, title: '   ', type: 'verify' },
  null,
] };
const js = 'window.BRIEF_DATA = ' + JSON.stringify(brief).replace(/</g, '\\u003c') + ';';
assert.deepStrictEqual(ext.parseDataJs(js).items.length, 6);
assert.throws(() => ext.parseDataJs('not js'));
assert.throws(() => ext.parseDataJs('window.BRIEF_DATA = {"x":1};'), /items/);

// ---- toExtData ----
const out = ext.toExtData(brief, 'https://example.test/');
assert.strictEqual(out.items.length, 4, '空标题与非对象条目丢弃');
assert.deepStrictEqual(out.items.map(i => i.stampText), ['吵翻天', '缺关键证据', '有定论', '已实锤']);
assert.strictEqual(out.items[0].qid, '2066353729507422598');
assert.deepStrictEqual(out.items[0].labels, ['加厚派', '浮筑派']);
assert.deepStrictEqual(out.items.map(i => i.page), [1, 2, 3, 4], 'page 与站点 #card-N 对应');
assert.strictEqual(out.site_url, 'https://example.test/');
assert.strictEqual(out.curated, false);
assert.strictEqual(ext.toExtData(Object.assign({}, brief, { source: 'demo' })), null, '示例数据不盖章');
assert.strictEqual(ext.toExtData(Object.assign({}, brief, { provenance: 'curated' })).curated, true);
assert(/^https:\/\/.+\.tcloudbase\.com\/$/.test(ext.SITE_URL), '默认指向线上站点，而不是 localhost');

// ---- fetchBrief（注入假 fetch） ----
(async () => {
  const ok = await ext.fetchBrief(async (url) => {
    assert(url.startsWith(ext.SITE_URL + 'data.js?ext='));
    return { ok: true, text: async () => js };
  });
  assert.strictEqual(ok.items.length, 4);
  await assert.rejects(ext.fetchBrief(async () => ({ ok: false, status: 503, text: async () => '' })), /503/);
  await assert.rejects(ext.fetchBrief(async () => ({ ok: true,
    text: async () => 'window.BRIEF_DATA = ' + JSON.stringify(Object.assign({}, brief, { source: 'demo' })) + ';' })), /示例/);

  // ---- 清单与接线 ----
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'extension', 'manifest.json'), 'utf8'));
  assert.strictEqual(manifest.background.service_worker, 'background.js');
  assert.deepStrictEqual(manifest.permissions, ['storage'], '只申请 storage 权限');
  assert.strictEqual(manifest.host_permissions.length, 1, '只允许访问本站一个域名');
  assert(manifest.host_permissions[0].startsWith(ext.SITE_URL.replace(/\/$/, '')));

  const content = fs.readFileSync(path.join(__dirname, '..', 'extension', 'content.js'), 'utf8');
  assert(content.includes("type: 'zhd:get-brief'"), '内容脚本向后台要线上数据');
  assert(content.includes('finish(bundled)'), '拿不到线上数据时回落到自带 data.js');
  assert(!content.includes('localhost'), '内容脚本不再写死 localhost');
  assert(content.includes("mini.querySelector('a').href"), '深读链接用属性赋值，不拼进 innerHTML');

  const bundled = fs.readFileSync(path.join(__dirname, '..', 'extension', 'data.js'), 'utf8');
  assert(!bundled.includes('localhost'), '离线数据的 site_url 已改为线上地址');
  console.log('extension tests passed');
})().catch((e) => { console.error(e); process.exit(1); });
