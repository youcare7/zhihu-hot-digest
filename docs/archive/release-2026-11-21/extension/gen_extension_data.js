// 从 demo/data.js 生成 extension/data.js（插件 content script 用的今日条目子集）
// 用法：node extension/gen_extension_data.js
const fs = require('fs');
const path = require('path');

global.window = {};
require(path.join(__dirname, '..', 'demo', 'data.js'));
const data = window.BRIEF_DATA;

function stampOf(item) {
  if (item.web_evidence && item.web_evidence.status === 'found') return ['found', '已实锤'];
  if (item.type === 'verify') return ['miss', '缺关键证据'];
  if (item.type === 'dispute') return ['hot', '吵翻天'];
  return ['calm', '有定论'];
}

const items = data.items.map(function (it) {
  const m = /question\/(\d+)/.exec(it.question_url || '');
  const s = stampOf(it);
  return {
    rank: it.rank,
    title: it.title,
    type: it.type,
    qid: m ? m[1] : '',
    punch: it.punch || '',
    quotes: it.quotes || [],
    labels: (it.sides || []).map(function (x) { return x.label; }),
    stampCls: s[0],
    stampText: s[1],
  };
});

const out = 'window.ZHD_EXT_DATA = ' + JSON.stringify({
  date: data.date,
  site_url: 'http://localhost:7100/',
  items: items,
}, null, 1) + ';\n';

fs.writeFileSync(path.join(__dirname, 'data.js'), out, 'utf8');
console.log('extension/data.js written,', items.length, 'items,', items.filter(i => i.qid).length, 'with qid');
