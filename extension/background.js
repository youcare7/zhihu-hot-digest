// 热榜深潜简报 · 求真标签（后台 service worker）
// 从线上站点读取当天的判读结果（/data.js），转成插件用的精简结构，缓存在 chrome.storage.local。
// 内容脚本在知乎热榜页向这里要数据；线上读取失败时，内容脚本回落到插件自带的 data.js。
'use strict';

var SITE_URL = 'https://zhihu-hot-digest-348140-5-1533039364.sh.run.tcloudbase.com/';
var CACHE_KEY = 'zhd_brief_v1';
var CACHE_TTL_MS = 30 * 60 * 1000;   // 30 分钟内不重复请求站点
var FETCH_TIMEOUT_MS = 8000;

// /data.js 的内容是 `window.BRIEF_DATA = {...};`，其中 {...} 是合法 JSON（后端只把 < > 等转义成 \uXXXX）。
function parseDataJs(text) {
  var s = String(text || '').trim();
  var i = s.indexOf('=');
  if (i < 0) throw new Error('data.js 格式不对');
  var body = s.slice(i + 1).trim().replace(/;\s*$/, '');
  var data = JSON.parse(body);
  if (!data || typeof data !== 'object' || !Array.isArray(data.items)) throw new Error('data.js 缺少 items');
  return data;
}

function stampOf(item) {
  if (item.web_evidence && item.web_evidence.status === 'found') return ['found', '已实锤'];
  if (item.type === 'verify') return ['miss', '缺关键证据'];
  if (item.type === 'dispute') return ['hot', '吵翻天'];
  return ['calm', '有定论'];
}

// 简报 → 插件数据。示例数据（source=demo）不用于盖章，返回 null。
function toExtData(brief, siteUrl) {
  if (!brief || brief.source === 'demo') return null;
  var items = (brief.items || []).filter(function (it) {
    return it && typeof it === 'object' && String(it.title || '').trim();
  }).map(function (it, index) {
    var m = /question\/(\d+)/.exec(String(it.question_url || ''));
    var s = stampOf(it);
    return {
      rank: it.rank,
      page: index + 1,                       // 站点深读页的 #card-N 按条目顺序编号
      title: String(it.title),
      type: it.type,
      qid: m ? m[1] : '',
      punch: typeof it.punch === 'string' ? it.punch : '',
      quotes: Array.isArray(it.quotes) ? it.quotes.slice(0, 2) : [],
      labels: (Array.isArray(it.sides) ? it.sides : []).map(function (x) { return (x && x.label) || ''; }),
      stampCls: s[0],
      stampText: s[1],
    };
  });
  return {
    date: brief.date || '',
    generated_at: brief.generated_at || '',
    source: brief.source || '',
    curated: brief.provenance === 'curated' || brief.showcase === true,
    site_url: siteUrl || SITE_URL,
    items: items,
  };
}

function fetchBrief(fetchImpl) {
  var f = fetchImpl || fetch;
  var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, FETCH_TIMEOUT_MS) : null;
  return f(SITE_URL + 'data.js?ext=' + Date.now(), {
    cache: 'no-store', credentials: 'omit', signal: ctrl ? ctrl.signal : undefined,
  }).then(function (res) {
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.text();
  }).then(function (text) {
    var ext = toExtData(parseDataJs(text), SITE_URL);
    if (!ext) throw new Error('站点当前是示例数据');
    return ext;
  }).finally(function () { if (timer) clearTimeout(timer); });
}

function getBrief(force) {
  return chrome.storage.local.get(CACHE_KEY).then(function (got) {
    var cached = got && got[CACHE_KEY];
    if (!force && cached && cached.data && Date.now() - cached.at < CACHE_TTL_MS) return cached.data;
    return fetchBrief().then(function (data) {
      var entry = {}; entry[CACHE_KEY] = { at: Date.now(), data: data };
      return chrome.storage.local.set(entry).then(function () { return data; });
    }).catch(function (err) {
      if (cached && cached.data) return cached.data;   // 请求失败但有旧缓存：先用旧的
      throw err;
    });
  });
}

if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    if (!msg || msg.type !== 'zhd:get-brief') return false;
    getBrief(!!msg.force)
      .then(function (data) { sendResponse({ ok: true, data: data }); })
      .catch(function (err) { sendResponse({ ok: false, error: String(err && err.message || err) }); });
    return true;   // 异步响应
  });
}

if (typeof module !== 'undefined') {
  module.exports = { parseDataJs: parseDataJs, stampOf: stampOf, toExtData: toExtData, fetchBrief: fetchBrief, SITE_URL: SITE_URL };
}
