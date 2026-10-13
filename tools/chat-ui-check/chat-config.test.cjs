const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, '../../assets/chat-config.js');
const source = fs.readFileSync(sourcePath, 'utf8');
const localEndpoint = 'http://127.0.0.1:8788/api/chat';
function load(origin, existing) {
  const window = { location: { origin } };
  if (existing) window.XiongDayeChat = existing;
  vm.runInNewContext(source, { window }, { filename: sourcePath });
  return window.XiongDayeChat;
}

for (const origin of ['http://127.0.0.1:8786', 'http://localhost:8786']) {
  const config = load(origin);
  assert.equal(config.endpoint, localEndpoint, 'local preview must bypass the public tunnel');
  assert.ok(Object.isFrozen(config));
}
for (const origin of ['https://yanmingxing.asia', 'https://starbeef6.github.io',
  'http://127.0.0.1:8785', 'http://localhost.evil:8786', 'https://127.0.0.1:8786']) {
  const config = load(origin);
  assert.match(config.endpoint, /^https:\/\/[a-z0-9-]+\.trycloudflare\.com\/api\/chat$/,
    'only the exact trusted HTTP preview origins select loopback');
  assert.notEqual(config.endpoint, localEndpoint);
}
const injected = { endpoint: '/api/chat' };
assert.strictEqual(load('http://127.0.0.1:8787', injected), injected,
  'the backend-injected same-origin config must remain intact');
assert.strictEqual(load('http://127.0.0.1:8786', injected), injected,
  'an existing explicit endpoint must remain intact');
console.log('chat-config exact-origin routing checks passed');
