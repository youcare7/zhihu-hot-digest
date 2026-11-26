// 热榜深潜简报 · 求真标签（content script）
// 在知乎热榜页给今日 10 条深读条目盖「求真印章」，悬停看锐评 + 双方金句，点击跳站内深读。
(function () {
  'use strict';
  var DATA = window.ZHD_EXT_DATA || { items: [] };
  var SITE_URL = DATA.site_url || 'http://localhost:7100/';
  var COLOR = { found: '#24824f', miss: '#7163b8', hot: '#b76018', calm: '#056de8' };

  // ---------- 样式 ----------
  var css = [
    '.zhd-badge{display:inline-block;margin-left:8px;padding:1px 8px;border:1.5px solid currentColor;border-radius:6px;font-size:11px;font-weight:700;vertical-align:2px;transform:rotate(3deg);background:#fffefa;cursor:help;line-height:1.6;letter-spacing:.08em;white-space:nowrap;}',
    '.zhd-mini{position:fixed;z-index:2147483647;width:300px;padding:14px 15px;border-radius:13px;background:#fffefa;border:1px solid rgba(29,37,46,.18);box-shadow:0 16px 50px rgba(20,28,36,.24);font-size:13px;line-height:1.7;color:#2c323a;}',
    '.zhd-mini .t{font-weight:700;font-size:13.5px;margin-bottom:6px;color:#1d252e;}',
    '.zhd-mini .p{color:#5a4d9e;margin-bottom:8px;}',
    '.zhd-mini .q{padding:6px 9px;border-radius:9px;margin-top:6px;font-weight:600;}',
    '.zhd-mini .qa{background:rgba(5,109,232,.08);color:#0a4fa8;}',
    '.zhd-mini .qb{background:rgba(183,96,24,.08);color:#8a4a12;}',
    '.zhd-mini .f{margin-top:9px;font-size:11.5px;color:#9aa2aa;}',
    '.zhd-mini a{color:#056de8;font-weight:600;text-decoration:none;}',
  ].join('\n');
  var styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.documentElement.appendChild(styleEl);

  // ---------- 匹配 ----------
  function norm(s) {
    return String(s || '').toLowerCase().replace(/[《》「」?？!！.。,，、:：;；·"“”‘’'()（）\[\]【】\s]+/g, '');
  }
  var byQid = {};
  var byTitle = {};
  DATA.items.forEach(function (it) {
    if (it.qid) byQid[it.qid] = it;
    byTitle[norm(it.title)] = it;
  });
  function findItem(anchor, titleText) {
    var m = /question\/(\d+)/.exec(anchor.getAttribute('href') || '');
    if (m && byQid[m[1]]) return byQid[m[1]];
    if (titleText) {
      var t = norm(titleText);
      if (byTitle[t]) return byTitle[t];
      // 标题被截断的热榜项：前 8 字命中即可
      if (t.length >= 8) {
        var head = t.slice(0, 8);
        for (var k in byTitle) if (k.indexOf(head) === 0) return byTitle[k];
      }
    }
    return null;
  }

  // ---------- 悬停卡 ----------
  var mini = null;
  function closeMini() { if (mini) { mini.remove(); mini = null; } }
  function openMini(item, x, y, linkRect) {
    closeMini();
    mini = document.createElement('div');
    mini.className = 'zhd-mini';
    var html = '<div class="t"></div>';
    if (item.punch) html += '<div class="p"></div>';
    if (item.quotes && item.quotes.length === 2) {
      html += '<div class="q qa"></div><div class="q qb"></div>';
    }
    html += '<div class="f">求真印章：<b style="color:' + (COLOR[item.stampCls] || COLOR.calm) + '">' + item.stampText + '</b> · 热榜深潜简报<br><a href="' + SITE_URL + '#card-' + item.rank + '" target="_blank" rel="noopener">查看完整深读 →</a></div>';
    mini.innerHTML = html;
    mini.querySelector('.t').textContent = item.title;
    if (item.punch) mini.querySelector('.p').textContent = item.punch;
    if (item.quotes && item.quotes.length === 2) {
      var qa = mini.querySelector('.qa'), qb = mini.querySelector('.qb');
      qa.textContent = (item.labels[0] || '一方') + '：' + item.quotes[0];
      qb.textContent = (item.labels[1] || '另一方') + '：' + item.quotes[1];
    }
    document.body.appendChild(mini);
    var mw = 300, mh = mini.offsetHeight;
    var left = linkRect.left + linkRect.width / 2 - mw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - mw - 8));
    var top = linkRect.bottom + 8;
    if (top + mh > window.innerHeight - 8) top = linkRect.top - mh - 8;
    mini.style.left = left + 'px';
    mini.style.top = Math.max(8, top) + 'px';
  }

  // ---------- 注入印章 ----------
  function scan() {
    var anchors = document.querySelectorAll('a[href*="/question/"]');
    anchors.forEach(function (a) {
      if (a.dataset.zhd) return;
      a.dataset.zhd = '1';
      var titleText = (a.textContent || '').trim();
      var item = findItem(a, titleText);
      if (!item) return;
      var badge = document.createElement('span');
      badge.className = 'zhd-badge';
      badge.textContent = '深潜·' + item.stampText;
      badge.style.color = COLOR[item.stampCls] || COLOR.calm;
      badge.addEventListener('mouseenter', function () { openMini(item, 0, 0, badge.getBoundingClientRect()); });
      badge.addEventListener('mouseleave', function (e) {
        if (mini && !mini.contains(e.relatedTarget)) closeMini();
      });
      a.insertAdjacentElement('afterend', badge);
    });
  }

  document.addEventListener('mouseover', function (e) {
    if (mini && !mini.contains(e.target) && !(e.target.classList && e.target.classList.contains('zhd-badge'))) closeMini();
  });
  window.addEventListener('scroll', closeMini, { passive: true });

  scan();
  // 知乎热榜是 SPA：监听 DOM 变化补盖印章
  var timer = null;
  new MutationObserver(function () {
    if (timer) return;
    timer = setTimeout(function () { timer = null; scan(); }, 400);
  }).observe(document.body, { childList: true, subtree: true });
})();
