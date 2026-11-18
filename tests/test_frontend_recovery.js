// 前端冷恢复行为测试。运行：node tests/test_frontend_recovery.js
// 仅开发验收需要 Node.js；线上运行时仍只需 Python + Flask。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, '..', 'demo', 'index.html'), 'utf8');
const match = html.match(/  function startRecoveryPolling\(initial\) \{[\s\S]*?\n  \}/);
assert(match, '找不到 startRecoveryPolling');

const timers = [];
let reloads = 0;
const context = {
  window: {
    setTimeout: (cb, delay) => timers.push({ cb, delay }),
    location: { reload: () => { reloads += 1; } },
  },
  document: { getElementById: () => ({ textContent: '' }) },
  next: null,
};
context.fetch = () => Promise.resolve({
  ok: true,
  text: () => Promise.resolve('window.BRIEF_DATA = ' + JSON.stringify(context.next) + ';'),
});
vm.createContext(context);
vm.runInContext(match[0] + '\nthis.startRecoveryPolling = startRecoveryPolling;', context);
const settle = () => new Promise((resolve) => setImmediate(() => setImmediate(resolve)));

(async () => {
  context.next = { source: 'fallback', items: [{ title: '兜底' }] };
  context.startRecoveryPolling({ refreshing: true });
  assert.strictEqual(timers.length, 1);
  timers.shift().cb();
  await settle();
  assert.strictEqual(reloads, 0);
  assert.strictEqual(timers.length, 1, '无恢复状态时轮询不能停止');
  assert.strictEqual(timers[0].delay, 30000);
  console.log('场景A PASS：无恢复状态时 30 秒后继续低频重试');

  timers.length = 0;
  context.next = { source: 'cache', items: [] };
  context.startRecoveryPolling({ refreshing: true });
  timers.shift().cb();
  await settle();
  assert.strictEqual(reloads, 0, '空缓存不得触发刷新页面');
  assert.strictEqual(timers.length, 1);
  assert.strictEqual(timers[0].delay, 30000);
  console.log('场景B PASS：空缓存不刷新，继续等待有效内容');

  timers.length = 0;
  context.startRecoveryPolling({ source: 'fallback', items: [{ title: '兜底' }] });
  assert.strictEqual(timers.length, 1, '首次响应无恢复状态也要启动轮询');
  assert.strictEqual(timers[0].delay, 30000);
  console.log('场景C PASS：首次异常响应无恢复状态时启动 30 秒低频轮询');

  timers.length = 0;
  context.startRecoveryPolling({ source: 'cache', items: [{ title: '健康缓存' }] });
  assert.strictEqual(timers.length, 0, '健康缓存没有恢复字段也不能启动轮询');
  console.log('场景D PASS：健康缓存无恢复字段时保持静态，不循环刷新');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
