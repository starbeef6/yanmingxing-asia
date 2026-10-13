const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const sessionApi = require('../../assets/chat-session.js');
const source = fs.readFileSync(path.join(__dirname, '../../assets/mascot.js'), 'utf8');

// A bounded DOM adapter runs the real widget event handlers without a browser,
// network, credentials or model calls. Layout acceptance is checked separately
// in the browser; these assertions cover viewport inputs and request lifecycles.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toLowerCase(); this.children = []; this.listeners = {};
    this.attributes = {}; this.value = ''; this.hidden = false; this.disabled = false;
    this.scrollHeight = 60; this.scrollTop = 0; this.scrollLeft = 0; this.clientHeight = 250;
    this.props = {}; this.style = { setProperty: (key, value) => { this.props[key] = value; } };
    this.classList = {
      contains: value => (this.className || '').split(/\s+/).includes(value),
      add: (...values) => { this.className = [...new Set([...this.classes(), ...values])].join(' '); },
      toggle: (value, force) => {
        const has = force === undefined ? !this.classList.contains(value) : force;
        this.className = this.classes().filter(item => item !== value).concat(has ? value : []).join(' ');
        return has;
      },
    };
  }
  classes() { return (this.className || '').split(/\s+/).filter(Boolean); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this); }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  async fire(type, details = {}) {
    const event = { preventDefault() {}, target: this, ...details };
    await Promise.all((this.listeners[type] || []).map(listener => listener(event)));
    for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
  }
  focus() { this.focused = true; }
  matches(selector) {
    const parts = selector.trim().split(/\s+/);
    const own = parts.pop();
    if (own.startsWith('.')) { if (!this.classList.contains(own.slice(1))) return false; }
    else if (this.tagName !== own.split('[')[0]) return false;
    if (!parts.length) return true;
    let parent = this.parent;
    while (parent) { if (parent.matches(parts.join(' '))) return true; parent = parent.parent; }
    return false;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(','); const result = [];
    const walk = item => { for (const child of item.children) {
      if (selectors.some(part => child.matches(part))) result.push(child); walk(child);
    } };
    walk(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  set innerHTML(html) {
    this.children = []; const stack = [this];
    for (const match of html.matchAll(/<(\/?)([a-z][a-z0-9]*)([^>]*)>/gi)) {
      if (match[1]) { if (stack.length > 1) stack.pop(); continue; }
      const child = new Element(match[2]);
      for (const attribute of match[3].matchAll(/([a-z][a-z0-9-]*)(?:="([^"]*)")?/gi)) {
        child.attributes[attribute[1]] = attribute[2] || '';
        if (attribute[1] === 'class') child.className = attribute[2];
        if (attribute[1] === 'id') child.id = attribute[2];
        if (attribute[1] === 'hidden') child.hidden = true;
      }
      stack.at(-1).append(child);
      if (!['img', 'input', 'br', 'path'].includes(child.tagName)) stack.push(child);
    }
  }
}

function widget(options = {}) {
  const calls = []; const document = {
    getElementById: () => null, currentScript: { src: 'https://yanmingxing.asia/assets/mascot.js' },
    createElement: tag => new Element(tag), body: new Element('body'),
    addEventListener() {}, visibilityState: 'visible',
  };
  const viewport = new Element();
  Object.assign(viewport, { width: 375, height: 760, offsetTop: 0, offsetLeft: 0 }, options.viewport);
  const window = new Element();
  Object.assign(window, {
    innerWidth: options.innerWidth || 375, innerHeight: 820, visualViewport: viewport,
    matchMedia: () => ({ matches: false }), navigator: { onLine: true },
    OperationsChatSession: sessionApi,
    XiongDayeChat: { endpoint: 'https://relay.example/api/chat', statusEndpoint: 'https://relay.example/api/status', timeoutMs: 75000 },
  });
  const fetch = async (url, config = {}) => {
    calls.push({ url, ...config });
    if (config.method === 'POST') {
      const result = options.post ? options.post(calls.filter(call => call.method === 'POST').length) : { reply: '你好呀' };
      if (result instanceof Error) throw result;
      return new Response(JSON.stringify(result), { status: result.status || 200, headers: { 'content-type': 'application/json' } });
    }
    if (options.statusError) throw new TypeError('Failed to fetch');
    return new Response(JSON.stringify({ ready: options.ready !== false }), { headers: { 'content-type': 'application/json' } });
  };
  vm.runInNewContext(source, { window, document, location: { href: 'https://yanmingxing.asia/' },
    navigator: window.navigator, Intl, URL, Blob, AbortController, TypeError, SyntaxError,
    fetch, setTimeout, clearTimeout, requestAnimationFrame: fn => fn(), Date,
  });
  return { root: document.body.children[0], window, viewport, calls, options };
}

(async () => {
  const clipped = widget({ innerWidth: 980, viewport: { width: 375, offsetLeft: 31, height: 410, offsetTop: 92 } });
  assert.equal(clipped.root.props['--xd-viewport-width'], '375px', 'the visual width, not wide layout viewport, must constrain the frame');
  assert.equal(clipped.root.props['--xd-viewport-left'], '31px', 'a panned Safari viewport must keep both buttons in the visible frame');
  assert.equal(clipped.root.props['--xd-viewport-top'], '92px');
  assert.ok(clipped.root.classList.contains('xd-compact'), 'a keyboard-reduced frame must keep the composer usable');
  clipped.viewport.width = 280; clipped.viewport.offsetLeft = 58;
  await clipped.viewport.fire('resize');
  assert.equal(clipped.root.props['--xd-viewport-width'], '280px');
  assert.equal(clipped.root.props['--xd-viewport-left'], '58px');

  const offline = widget({ ready: false });
  await offline.root.querySelector('.xd-trigger').fire('click');
  assert.equal(offline.root.querySelector('.xd-input').focused, undefined, 'opening on a touch device must not auto-focus and trigger keyboard or Safari auto-zoom');
  assert.notEqual(offline.root.querySelector('.xd-mode').textContent, '在线对话', 'a configured URL is not proof the service is online');
  offline.root.querySelector('.xd-input').value = '你好';
  await offline.root.querySelector('.xd-form').fire('submit');
  assert.equal(offline.calls.filter(call => call.method === 'POST').length, 0, 'confirmed offline status must not consume a model attempt');
  assert.equal(offline.root.querySelectorAll('.xd-message-user').length, 1);
  assert.ok(offline.root.querySelector('.xd-retry'), 'failed text remains recoverable through an explicit retry');
  offline.options.ready = true;
  await offline.root.querySelector('.xd-retry').fire('click');
  assert.equal(offline.calls.filter(call => call.method === 'POST').length, 1, 'explicit retry checks that the service has recovered before one model attempt');
  assert.equal(offline.root.querySelectorAll('.xd-message-error').length, 0);
  assert.equal(offline.root.querySelectorAll('.xd-message-user').length, 1);

  const unreachable = widget({ statusError: true });
  await unreachable.root.querySelector('.xd-trigger').fire('click');
  unreachable.root.querySelector('.xd-input').value = '网络断开时的提问';
  await unreachable.root.querySelector('.xd-form').fire('submit');
  assert.equal(unreachable.calls.filter(call => call.method === 'POST').length, 0);
  assert.ok(unreachable.root.querySelector('.xd-retry'));

  const failing = widget({ post: count => count < 3 ? new TypeError('Failed to fetch') : { reply: '恢复了' } });
  await failing.root.querySelector('.xd-trigger').fire('click');
  failing.root.querySelector('.xd-input').value = '这次的原始提问';
  await failing.root.querySelector('.xd-form').fire('submit');
  assert.equal(failing.calls.filter(call => call.method === 'POST').length, 1, 'network failure must never silently retry POST');
  await failing.root.querySelector('.xd-retry').fire('click');
  assert.equal(failing.root.querySelectorAll('.xd-message-error').length, 1, 'retry failure updates one bubble instead of accumulating duplicates');
  assert.equal(failing.root.querySelectorAll('.xd-message-user').length, 1, 'the original question is displayed exactly once');
  await failing.root.querySelector('.xd-retry').fire('click');
  assert.equal(failing.root.querySelectorAll('.xd-message-error').length, 0, 'a successful retry clears the stale error bubble');
  assert.equal(failing.calls.filter(call => call.method === 'POST').length, 3);
  assert.ok(failing.calls.filter(call => call.method === 'POST').every(call => JSON.parse(call.body).message === '这次的原始提问'));
  assert.ok(failing.calls.filter(call => call.method !== 'POST').every(call => call.credentials === 'omit'), 'status probes carry no credentials');
  console.log('mascot viewport and failed-request recovery checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
