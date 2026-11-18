// 大版本分页交互的静态契约与滑动手势边界测试。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, '..', 'demo', 'index.html'), 'utf8');

[
  'id="directory"',
  'id="reader-pages"',
  'id="prev-page"',
  'id="next-page"',
  'id="continuous-toggle"',
  'data-filter="dispute"',
  'data-filter="consensus"',
  'data-filter="verify"',
  'data-page="guard" data-title="来源与链接"',
  'window.addEventListener(\'hashchange\', routeFromHash)',
  'getSwipeDirection(start, end, now)',
  'touchstart',
  'touchend',
  'ArrowLeft',
  'ArrowRight',
  'prefers-reduced-motion'
].forEach(marker => assert(html.includes(marker), `缺少分页交互契约：${marker}`));

assert(!html.includes('独立分页测试版'), '正式版本不得保留测试版提示');
assert(!html.includes('>守卫</'), '导航不得继续使用难懂的“守卫”名称');
assert(html.includes('isDemo = data.source === \'demo\''), '模拟数据禁链判断不得丢失');
assert(html.includes('item.viewpoints || {}'), '旧缓存三派字段兼容不得丢失');

const swipe = html.match(/  function getSwipeDirection\(start, end, now\) \{[\s\S]*?\n  \}/);
assert(swipe, '找不到手势判定函数');
const context = {};
vm.createContext(context);
vm.runInContext(swipe[0] + '\nthis.getSwipeDirection = getSwipeDirection;', context);
const direction = context.getSwipeDirection;
const start = { x: 280, y: 200, time: 1000 };

assert.strictEqual(direction(start, { x: 180, y: 205 }, 1400), 1, '明确左滑应进入下一页');
assert.strictEqual(direction(start, { x: 390, y: 205 }, 1400), -1, '明确右滑应返回上一页');
assert.strictEqual(direction(start, { x: 250, y: 205 }, 1400), 0, '短距离滑动不翻页');
assert.strictEqual(direction(start, { x: 180, y: 310 }, 1400), 0, '垂直滚动不翻页');
assert.strictEqual(direction(start, { x: 180, y: 205 }, 1900), 0, '慢速拖动不翻页');
assert.strictEqual(direction(null, { x: 180, y: 205 }, 1400), 0, '缺失起点不翻页');

console.log('分页静态契约 PASS：目录、深链、键盘、连续阅读与旧数据兼容均存在');
console.log('滑动手势 PASS：左右翻页、短滑动、垂直滚动和超时边界共 6 组');
