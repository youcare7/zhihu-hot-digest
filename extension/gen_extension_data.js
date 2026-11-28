// 从 demo/data.js 生成 extension/data.js：插件读不到线上数据时使用的离线兜底
// 用法：node extension/gen_extension_data.js
// 转换规则与后台 background.js 完全一致（直接复用 toExtData），避免两份逻辑走样。
const fs = require('fs');
const path = require('path');
const { toExtData, SITE_URL } = require('./background.js');

global.window = {};
require(path.join(__dirname, '..', 'demo', 'data.js'));
const data = window.BRIEF_DATA;

const ext = toExtData(Object.assign({}, data, { source: data.source === 'demo' ? 'cache' : data.source }), SITE_URL);
const out = 'window.ZHD_EXT_DATA = ' + JSON.stringify(ext, null, 1) + ';\n';

fs.writeFileSync(path.join(__dirname, 'data.js'), out, 'utf8');
console.log('extension/data.js written,', ext.items.length, 'items,', ext.items.filter(i => i.qid).length, 'with qid');
