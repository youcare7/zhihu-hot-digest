
(function () {
  'use strict';

  var data = window.BRIEF_DATA;
  var loadingEl = document.getElementById('loading');

  function showError(msg) {
    loadingEl.textContent = msg;
    loadingEl.classList.remove('loading');
  }

  if (!data || !Array.isArray(data.items)) {
    showError('简报数据为空，请检查 data.js 是否正常加载。');
    return;
  }

  // demo（静态示例 / mock 模拟）模式下所有链接不可点，防止假链接被误点。
  // 即使旧缓存把 source 改成了 cache，也必须保持示例标识，不能显示「当日缓存」。
  var isDemo = data.source === 'demo'
    || String(data.notice || '').indexOf('模拟数据') !== -1
    || String(data.generated_at || '').indexOf('模拟') !== -1;

  var BADGES = { demo: '示例数据', live: '实时生成', cache: '当日缓存', fallback: '演示兜底' };
  var TYPE_LABEL = { dispute: '仍有分歧', consensus: '已有共识', verify: '需补材料' };
  var CLAIM_LABEL = { fact: '可裁决', inference: '推测对推测', impression: '观感对观感', value: '价值判断' };

  // 冷缓存时后端立即返回兜底并后台生成；页面低频检查，成功后自动切换。
  // 轮询只读取 data.js，生成锁与冷却期保证它不会放大上游 API 调用量。
  function startRecoveryPolling(initial) {
    var attempts = 0;
    var maxAttempts = 40;

    function stopWithManualHint() {
      var noticeEl = document.getElementById('notice');
      if (noticeEl) noticeEl.textContent += ' 后台生成暂未结束，请稍后手动刷新。';
    }

    function schedule(delay) {
      if (attempts >= maxAttempts) {
        stopWithManualHint();
        return;
      }
      window.setTimeout(poll, Math.max(1000, delay));
    }

    function parseBrief(raw) {
      var text = raw.trim();
      if (text.charAt(0) === '{') return JSON.parse(text);
      var pos = text.indexOf('=');
      if (pos < 0) throw new Error('invalid data.js');
      return JSON.parse(text.slice(pos + 1).trim().replace(/;$/, ''));
    }

    function poll() {
      attempts += 1;
      fetch('data.js?recovery=' + Date.now(), {
        cache: 'no-store',
        credentials: 'same-origin'
      }).then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.text();
      }).then(function (raw) {
        var next = parseBrief(raw);
        if ((next.source === 'cache' || next.source === 'live')
            && Array.isArray(next.items) && next.items.length > 0) {
          window.location.reload();
          return;
        }
        if (next.refreshing) {
          schedule(15000);
        } else if (Number(next.retry_after) > 0) {
          schedule(Number(next.retry_after) * 1000 + 1000);
        } else {
          // 异常兜底可能暂时没有恢复字段；保持低频重试，不能悄悄停在静态页。
          schedule(30000);
        }
      }).catch(function () {
        schedule(30000);
      });
    }

    if (initial.refreshing) {
      schedule(15000);
    } else if (Number(initial.retry_after) > 0) {
      schedule(Number(initial.retry_after) * 1000 + 1000);
    } else if (!((initial.source === 'cache' || initial.source === 'live')
                 && Array.isArray(initial.items) && initial.items.length > 0)) {
      // 只有异常/兜底或空内容需要恢复；健康缓存没有恢复字段也不应轮询刷新。
      schedule(30000);
    }
  }

  // ---------- 元信息与首页判断 ----------
  var badgeEl = document.getElementById('badge');
  badgeEl.textContent = isDemo ? BADGES.demo : (BADGES[data.source] || data.source);
  if (data.source === 'fallback') badgeEl.classList.add('fallback');
  if (isDemo) badgeEl.classList.add('demo');
  document.getElementById('m-count').textContent = '共 ' + data.items.length + ' 条';
  var generatedAt = String(data.generated_at || '').trim();
  var hasExactTime = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(generatedAt);
  var asOf = hasExactTime ? generatedAt : String(data.date || '').trim();
  document.getElementById('m-date').textContent = asOf ? '数据截至：' + asOf : '';
  document.getElementById('m-gen').textContent = '';
  document.getElementById('cover-date').textContent = asOf || '今日简报';
  document.getElementById('cover-total').textContent = data.items.length + ' 条热点';
  document.getElementById('cover-source').textContent = isDemo ? BADGES.demo : (BADGES[data.source] || '3 类判断');

  var digest = data.digest || {};
  var hasDigest = Number.isFinite(Number(digest.total)) && Number(digest.total) > 0;
  document.getElementById('today-heading').textContent = digest.headline || '这份示例使用旧版三派结构；真实简报会在这里显示三态统计。';
  document.getElementById('cover-dispute').textContent = hasDigest ? String(digest.dispute || 0) : '—';
  document.getElementById('cover-consensus').textContent = hasDigest ? String(digest.consensus || 0) : '—';
  document.getElementById('cover-verify').textContent = hasDigest ? String(digest.verify || 0) : '—';
  var insightList = document.getElementById('cover-insights');

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  (digest.insights || []).forEach(function (text) { insightList.appendChild(el('li', '', text)); });

  if (data.notice) {
    var noticeEl = document.getElementById('notice');
    noticeEl.textContent = data.notice;
    noticeEl.hidden = false;
  }

  function demoTag() { return el('span', 'tag-demo', '示例'); }

  function renderTitle(item) {
    var box = el('div', 'card-title-box');
    var titleNode;
    if (isDemo || !item.question_url) {
      titleNode = el('span', 'card-title', item.title);
      titleNode.appendChild(demoTag());
    } else {
      titleNode = el('a', 'card-title', item.title);
      titleNode.href = item.question_url;
      titleNode.target = '_blank';
      titleNode.rel = 'noopener';
    }
    box.appendChild(titleNode);
    return box;
  }

  function renderReading(list) {
    var ul = el('ul', 'reading');
    (list || []).forEach(function (r) {
      var li = el('li');
      if (isDemo || !r.url) {
        var dead = el('span', 'rt dead', r.title);
        dead.appendChild(demoTag());
        li.appendChild(dead);
      } else {
        var a = el('a', 'rt', r.title);
        a.href = r.url;
        a.target = '_blank';
        a.rel = 'noopener';
        li.appendChild(a);
      }
      if (r.reason) li.appendChild(el('span', 'reason', '—— ' + r.reason));
      ul.appendChild(li);
    });
    return ul;
  }

  // ---------- 全网证据核验区块（单条试点） ----------
  // 只渲染 found / no_result 两种态；status 为 error/skipped 或字段缺失时返回
  // null 不渲染——此时卡片结构与无核验时完全一致（任务书 §5.3）。
  // 文案固定（任务书 §4）：无结果时只说「本次检索未返回」，不做全称否定。
  function renderWebEvidence(ev) {
    if (!ev || typeof ev !== 'object') return null;
    var box = el('div', 'web-evidence');
    if (ev.status === 'found') {
      if (!ev.title || !ev.url) return null;
      box.appendChild(el('b', '', '全网检索到的第一手材料'));
      var link = el('a', 'web-evidence-title', ev.title);
      link.href = ev.url;
      link.target = '_blank';
      link.rel = 'noopener';
      box.appendChild(link);
      box.appendChild(el('span', 'web-evidence-meta',
        '来源：' + (ev.source || '未知来源') + '｜核验截至 ' + (ev.checked_at || '—')));
    } else if (ev.status === 'no_result') {
      box.appendChild(el('b', '', '全网证据核验'));
      box.appendChild(document.createTextNode(
        '本次全网检索未返回可用的一手材料｜核验截至 ' + (ev.checked_at || '—')));
    } else {
      return null; // error / skipped / 未知状态：不渲染，不影响原有结构
    }
    box.appendChild(el('span', 'web-evidence-query', '检索词：' + (ev.query || '')));
    return box;
  }

  // ---------- 速览/深读状态（2A+2B 合并测试版） ----------
  // 展开偏好写 localStorage，reload 后保持；深链跳入自动展开目标卡片。
  var EXPAND_KEY = 'zhd-expanded-cards';
  function loadExpanded() {
    try { var v = JSON.parse(localStorage.getItem(EXPAND_KEY)); return Array.isArray(v) ? v : []; }
    catch (error) { return []; }
  }
  function saveExpanded(list) {
    try { localStorage.setItem(EXPAND_KEY, JSON.stringify(list)); } catch (error) { /* 私密模式忽略 */ }
  }
  function toggleDeep(card, btn, persist) {
    var open = card.classList.toggle('deep-open');
    btn.textContent = open ? '收起 ↑' : '展开本条完整解读 ↓';
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (persist) {
      var list = loadExpanded(), i = list.indexOf(card.id);
      if (open && i < 0) list.push(card.id);
      if (!open && i >= 0) list.splice(i, 1);
      saveExpanded(list);
    }
  }
  function clipOne(text, limit) {
    text = String(text || '');
    return text.length > limit ? text.slice(0, limit) + '…' : text;
  }

  // ---------- 卡片与目录 ----------
  var cardsEl = document.getElementById('cards');
  var directoryList = document.getElementById('directory-list');
  var directoryItems = [];
  data.items.forEach(function (item, itemIndex) {
    var card = el('article', 'card page-panel');
    card.id = 'page-card-' + (itemIndex + 1);
    card.setAttribute('data-page', 'card-' + (itemIndex + 1));
    card.setAttribute('data-title', '热点 #' + item.rank);
    card.setAttribute('aria-label', '第 ' + item.rank + ' 条：' + item.title);
    card.appendChild(el('div', 'card-sequence', 'DEEP DIVE / ' + String(itemIndex + 1).padStart(2, '0') + ' · ' + (TYPE_LABEL[item.type] || '热点解读')));

    var head = el('div', 'card-head');
    head.appendChild(el('span', 'rank', String(item.rank)));
    head.appendChild(renderTitle(item));
    card.appendChild(head);

    var deepBody = null;
    if (item.background === '暂无解读。' && item.question_url && !isDemo) {
      var note = el('p', 'degrade-note');
      note.appendChild(document.createTextNode('本条暂无可用素材，未生成解读。我们不编造背景与争议，你可以'));
      var go = el('a', '', '直接去知乎原问题看看大家怎么说 →');
      go.href = item.question_url;
      go.target = '_blank';
      go.rel = 'noopener';
      note.appendChild(go);
      card.appendChild(note);
    } else if (item.background) {
      deepBody = el('div', 'deep-body');
      deepBody.appendChild(el('p', 'background', item.background));
    }

    if (item.type) {
      var tags = el('div', 'tags');
      tags.appendChild(el('span', 'tag ' + item.type, TYPE_LABEL[item.type] || item.type));
      if (item.claim_type) tags.appendChild(el('span', 'tag claim', '分歧性质：' + (CLAIM_LABEL[item.claim_type] || item.claim_type)));
      card.appendChild(tags);
      // 速览层①：判断句（双方共识/站内共识）——评委扫读时第一眼看到的东西
      var verdictLabel = item.type === 'dispute' ? '双方都不否认'
        : item.type === 'consensus' ? '站内共识' : '目前能确认';
      if (item.consensus) {
        var verdictBox = el('div', 'verdict');
        verdictBox.appendChild(el('b', '', verdictLabel));
        verdictBox.appendChild(document.createTextNode(item.consensus));
        card.appendChild(verdictBox);
      }
      // 速览层②：双方观点一句话（深读层保留完整 claim）
      if (item.sides && item.sides.length === 2) {
        var quick = el('div', 'oneliner');
        quick.setAttribute('aria-label', '双方观点速览');
        item.sides.forEach(function (s) {
          var row = el('div', 'row');
          row.appendChild(el('span', 'k', s.label));
          row.appendChild(el('span', '', clipOne(s.claim, 42)));
          quick.appendChild(row);
        });
        card.appendChild(quick);
      }
      if (deepBody && item.focus) {
        var focusBox = el('div', 'focus');
        focusBox.appendChild(el('span', 'section-label', '值得回答的问题'));
        focusBox.appendChild(document.createTextNode(item.focus));
        deepBody.appendChild(focusBox);
      }
      if (deepBody && item.consensus) {
        var cb = el('div', 'consensus-box');
        cb.appendChild(el('b', '', '已有共识'));
        cb.appendChild(document.createTextNode(item.consensus));
        deepBody.appendChild(cb);
      }
      if (deepBody && item.sides && item.sides.length === 2) {
        var sidesGrid = el('div', 'sides');
        sidesGrid.setAttribute('aria-label', '双方观点');
        item.sides.forEach(function (s, i) {
          var box = el('div', 'side ' + (i === 0 ? 'a' : 'b'));
          box.appendChild(el('b', '', s.label));
          box.appendChild(document.createTextNode(s.claim));
          sidesGrid.appendChild(box);
        });
        deepBody.appendChild(sidesGrid);
      }
      if (item.gap) {
        var gapBox = el('div', 'gap');
        gapBox.appendChild(el('b', '', '缺的那块拼图'));
        gapBox.appendChild(document.createTextNode(item.gap));
        card.appendChild(gapBox);
      }
      // 全网核验区块挂在 gap 下方；renderWebEvidence 对 error/skipped/缺字段返回 null，
      // 不满足条件时卡片结构与不渲染完全一致。
      var webEvidenceBox = renderWebEvidence(item.web_evidence);
      if (webEvidenceBox) card.appendChild(webEvidenceBox);
    } else {
      // 兼容旧缓存：没有 type 字段时仍可显示原有三种观点，不出现空白页。
      var vp = item.viewpoints || {};
      var grid = el('div', 'viewpoints');
      [['support', 'vp support', '支持'], ['oppose', 'vp oppose', '反对'], ['undecided', 'vp undecided', '未定论']].forEach(function (cfg) {
        var block = el('div', cfg[1]);
        block.appendChild(el('b', '', cfg[2]));
        block.appendChild(document.createTextNode(vp[cfg[0]] || ''));
        grid.appendChild(block);
      });
      card.appendChild(grid);
    }

    if (deepBody && item.reading && item.reading.length) {
      var readingBlock = el('section', 'reading-block');
      readingBlock.appendChild(el('h3', 'reading-title', '站内延伸阅读'));
      readingBlock.appendChild(renderReading(item.reading));
      deepBody.appendChild(readingBlock);
    }
    // 有深读内容才挂展开按钮；记住用户上次的展开偏好
    if (deepBody && deepBody.childNodes.length) {
      var expandBtn = el('button', 'expand-btn', '展开本条完整解读 ↓');
      expandBtn.type = 'button';
      expandBtn.setAttribute('aria-expanded', 'false');
      expandBtn.addEventListener('click', function () { toggleDeep(card, expandBtn, true); });
      card.appendChild(expandBtn);
      card.appendChild(deepBody);
      if (loadExpanded().indexOf(card.id) >= 0) toggleDeep(card, expandBtn, false);
    }
    cardsEl.appendChild(card);

    var directoryItem = el('li', 'directory-item');
    directoryItem.setAttribute('data-type', item.type || 'legacy');
    var directoryButton = el('button', 'directory-link');
    directoryButton.type = 'button';
    directoryButton.setAttribute('data-go', 'card-' + (itemIndex + 1));
    directoryButton.appendChild(el('span', 'directory-rank', String(item.rank).padStart(2, '0')));
    directoryButton.appendChild(el('span', 'type-dot ' + (item.type || ''), ''));
    directoryButton.appendChild(el('span', 'directory-title', item.title));
    directoryButton.appendChild(el('span', 'directory-type', TYPE_LABEL[item.type] || '旧版示例'));
    directoryItem.appendChild(directoryButton);
    directoryList.appendChild(directoryItem);
    directoryItems.push(directoryItem);
  });

  // 示例数据不会生成 <a>，因此不会误标成可点击链接。
  document.querySelectorAll('#cards a[href]').forEach(function (link) {
    link.classList.add('zhihu-link');
    link.setAttribute('aria-label', link.textContent.trim() + '（在知乎新窗口打开）');
  });

  // ---------- 分页、连续阅读与深链 ----------
  var coverEl = document.getElementById('cover');
  var readerEl = document.getElementById('reader');
  var pagerEl = document.getElementById('pager');
  var panels = Array.prototype.slice.call(document.querySelectorAll('.page-panel'));
  var tabsEl = document.getElementById('page-tabs');
  var pageLabel = document.getElementById('page-label');
  var pageName = document.getElementById('reader-page-name');
  var progressBar = document.getElementById('reading-progress-bar');
  var prevButton = document.getElementById('prev-page');
  var nextButton = document.getElementById('next-page');
  var continuousToggle = document.getElementById('continuous-toggle');
  var currentPage = 0;
  var mode = 'cover';
  var pageButtons = [];

  function updateHash(value, replace) {
    try {
      var method = replace ? 'replaceState' : 'pushState';
      if (window.location.hash !== '#' + value) window.history[method](null, '', '#' + value);
    } catch (error) {
      window.location.hash = value;
    }
  }

  function setContinuousState(active) {
    readerEl.classList.toggle('continuous', active);
    continuousToggle.textContent = active ? '返回分页' : '速览全部';
    continuousToggle.classList.toggle('active', active);
    continuousToggle.setAttribute('aria-pressed', active ? 'true' : 'false');
  }

  function showCover(shouldUpdateHash) {
    mode = 'cover';
    coverEl.hidden = false;
    readerEl.hidden = true;
    pagerEl.hidden = true;
    setContinuousState(false);
    if (shouldUpdateHash !== false) updateHash('cover');
    window.scrollTo(0, 0);
  }

  function showPage(index, shouldUpdateHash) {
    if (index < 0 || index >= panels.length) return;
    mode = 'page';
    currentPage = index;
    coverEl.hidden = true;
    readerEl.hidden = false;
    pagerEl.hidden = false;
    readerEl.classList.add('mode-deep');   // 分页 = 深读模式：整页全展开
    setContinuousState(false);
    panels.forEach(function (panel, panelIndex) {
      var active = panelIndex === index;
      panel.classList.toggle('active', active);
      panel.setAttribute('aria-hidden', active ? 'false' : 'true');
    });
    pageButtons.forEach(function (button, buttonIndex) {
      var active = buttonIndex === index;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    var panel = panels[index];
    pageLabel.textContent = String(index + 1).padStart(2, '0') + ' / ' + String(panels.length).padStart(2, '0');
    pageName.textContent = panel.getAttribute('data-title') || '今日简报';
    progressBar.style.width = ((index + 1) / panels.length * 100) + '%';
    prevButton.disabled = index === 0;
    nextButton.disabled = index === panels.length - 1;
    nextButton.textContent = index === panels.length - 1 ? '已到末页' : '下一页 →';
    var activeTab = pageButtons[index];
    if (activeTab && activeTab.scrollIntoView) activeTab.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'auto' });
    if (shouldUpdateHash !== false) updateHash(panel.getAttribute('data-page'));
    window.scrollTo(0, 0);
  }

  function showAll(shouldUpdateHash) {
    mode = 'all';
    coverEl.hidden = true;
    readerEl.hidden = false;
    pagerEl.hidden = true;
    readerEl.classList.remove('mode-deep'); // 速览全部 = 扫读模式：渐进披露
    setContinuousState(true);
    panels.forEach(function (panel) {
      panel.classList.add('active');
      panel.setAttribute('aria-hidden', 'false');
    });
    pageButtons.forEach(function (button) { button.classList.remove('active'); button.removeAttribute('aria-current'); });
    pageName.textContent = '速览全部 · 共 ' + data.items.length + ' 条结论';
    if (shouldUpdateHash !== false) updateHash('all');
    window.scrollTo(0, 0);
  }

  function expandPanelByIndex(index) {
    var panel = panels[index];
    if (!panel) return;
    var btn = panel.querySelector('.expand-btn');
    if (btn && !panel.classList.contains('deep-open')) toggleDeep(panel, btn, true);
  }

  function routeFromHash() {
    var value = window.location.hash.replace(/^#/, '');
    if (value === 'all') { showAll(false); return; }
    var index = panels.findIndex(function (panel) { return panel.getAttribute('data-page') === value; });
    if (index >= 0) { showPage(index, false); expandPanelByIndex(index); }
    else showCover(false);
  }

  panels.forEach(function (panel, index) {
    var pageId = panel.getAttribute('data-page');
    var tabText = pageId === 'guard' ? '来源' : String(index + 1).padStart(2, '0');
    var button = el('button', 'page-tab', tabText);
    button.type = 'button';
    button.title = panel.getAttribute('data-title') || tabText;
    button.setAttribute('aria-label', '前往' + (panel.getAttribute('data-title') || tabText));
    button.addEventListener('click', function () { showPage(index, true); });
    tabsEl.appendChild(button);
    pageButtons.push(button);
  });

  var directory = document.getElementById('directory');
  function openDirectory() {
    if (typeof directory.showModal === 'function') {
      if (!directory.open) directory.showModal();
    } else {
      directory.setAttribute('open', '');
    }
  }
  function closeDirectory() {
    if (typeof directory.close === 'function' && directory.open) directory.close();
    else directory.removeAttribute('open');
  }
  document.querySelectorAll('[data-open-directory]').forEach(function (button) { button.addEventListener('click', openDirectory); });
  document.getElementById('close-directory').addEventListener('click', closeDirectory);
  directory.addEventListener('click', function (event) { if (event.target === directory) closeDirectory(); });

  document.querySelectorAll('[data-go]').forEach(function (button) {
    button.addEventListener('click', function () {
      var target = button.getAttribute('data-go');
      closeDirectory();
      if (target === 'cover') { showCover(true); return; }
      if (target === 'all') { showAll(true); return; }
      var index = panels.findIndex(function (panel) { return panel.getAttribute('data-page') === target; });
      if (index >= 0) { showPage(index, true); expandPanelByIndex(index); }
    });
  });

  // 全部展开/收起：评委演示模式由主讲人一键控制
  var expandAllBtn = document.getElementById('expand-all');
  var allExpanded = false;
  expandAllBtn.addEventListener('click', function () {
    allExpanded = !allExpanded;
    document.querySelectorAll('#cards .card').forEach(function (card) {
      var btn = card.querySelector('.expand-btn');
      if (!btn) return;
      var isOpen = card.classList.contains('deep-open');
      if (allExpanded !== isOpen) toggleDeep(card, btn, true);
    });
    expandAllBtn.textContent = allExpanded ? '全部收起' : '全部展开';
    expandAllBtn.setAttribute('aria-pressed', allExpanded ? 'true' : 'false');
    expandAllBtn.classList.toggle('active', allExpanded);
  });

  document.querySelectorAll('[data-filter]').forEach(function (button) {
    button.addEventListener('click', function () {
      var filter = button.getAttribute('data-filter');
      var visible = 0;
      document.querySelectorAll('[data-filter]').forEach(function (other) {
        var active = other === button;
        other.classList.toggle('active', active);
        other.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      directoryItems.forEach(function (item) {
        var show = filter === 'all' || item.getAttribute('data-type') === filter;
        item.hidden = !show;
        if (show) visible += 1;
      });
      document.getElementById('directory-empty').hidden = visible !== 0;
    });
  });

  document.getElementById('back-cover').addEventListener('click', function () { showCover(true); });
  continuousToggle.addEventListener('click', function () { if (mode === 'all') showPage(currentPage, true); else showAll(true); });
  prevButton.addEventListener('click', function () { showPage(currentPage - 1, true); });
  nextButton.addEventListener('click', function () { showPage(currentPage + 1, true); });
  window.addEventListener('hashchange', routeFromHash);

  document.addEventListener('keydown', function (event) {
    if (mode !== 'page' || directory.open || event.altKey || event.ctrlKey || event.metaKey) return;
    var tagName = event.target && event.target.tagName;
    if (tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT') return;
    if (event.key === 'ArrowLeft') { event.preventDefault(); showPage(currentPage - 1, true); }
    else if (event.key === 'ArrowRight') { event.preventDefault(); showPage(currentPage + 1, true); }
    else if (event.key === 'Escape') showCover(true);
  });

  // 只识别明确、快速的横向手势；不 preventDefault，垂直滚动与链接点击保持原生行为。
  function getSwipeDirection(start, end, now) {
    if (!start || !end) return 0;
    var dx = end.x - start.x;
    var dy = end.y - start.y;
    var elapsed = now - start.time;
    if (elapsed > 800 || Math.abs(dx) < 64 || Math.abs(dx) < Math.abs(dy) * 1.35) return 0;
    return dx < 0 ? 1 : -1;
  }
  var gesture = null;
  var readerPages = document.getElementById('reader-pages');
  readerPages.addEventListener('touchstart', function (event) {
    if (mode !== 'page' || event.touches.length !== 1) { gesture = null; return; }
    if (event.target.closest('a, button, summary, input, textarea, select')) { gesture = null; return; }
    var touch = event.touches[0];
    gesture = { x: touch.clientX, y: touch.clientY, time: Date.now() };
  }, { passive: true });
  readerPages.addEventListener('touchcancel', function () { gesture = null; }, { passive: true });
  readerPages.addEventListener('touchend', function (event) {
    if (!gesture || mode !== 'page' || !event.changedTouches.length) { gesture = null; return; }
    var touch = event.changedTouches[0];
    var direction = getSwipeDirection(gesture, { x: touch.clientX, y: touch.clientY }, Date.now());
    gesture = null;
    if (direction > 0) showPage(currentPage + 1, true);
    else if (direction < 0) showPage(currentPage - 1, true);
  }, { passive: true });

  var backTop = document.getElementById('backTop');
  function updateBackTop() { backTop.hidden = window.scrollY < 600 || mode !== 'all'; }
  window.addEventListener('scroll', updateBackTop, { passive: true });
  backTop.addEventListener('click', function () {
    var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
  });

  loadingEl.remove();
  document.getElementById('app').hidden = false;
  routeFromHash();
  updateBackTop();
  startRecoveryPolling(data);
})();
