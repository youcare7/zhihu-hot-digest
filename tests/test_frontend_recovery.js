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
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
