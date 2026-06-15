/**
 * Behavior tests for the per-element video download badge in content.js.
 * Runs content.js inside a jsdom DOM with a stubbed `chrome` API and asserts
 * the badge is created, positioned top-right, hidden for tiny videos, attached
 * to blob/MSE videos once a stream is sniffed, removed with the video, and that
 * clicking it forwards the right download message.
 *
 *   cd chrome_ext/test && npm install && npm test
 */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const CONTENT = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function makeEnv({ openShadow = false } = {}) {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
    { url: 'https://example.com/watch', pretendToBeVisual: true, runScripts: 'outside-only' });
  const win = dom.window;
  win.innerWidth = 1280;
  win.innerHeight = 800;
  if (openShadow) {
    const real = win.Element.prototype.attachShadow;
    win.Element.prototype.attachShadow = function () { return real.call(this, { mode: 'open' }); };
  }
  const state = { msgListener: null, sent: null };
  win.chrome = {
    storage: {
      local: { get: (d, cb) => cb({ enabled: true, token: '' }), set: (o, cb) => cb && cb() },
      onChanged: { addListener: () => {} },
    },
    runtime: {
      onMessage: { addListener: (cb) => { state.msgListener = cb; } },
      sendMessage: (m, cb) => { state.sent = m; cb && cb({ ok: true }); },
      lastError: null,
    },
  };
  win.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
  win.cancelAnimationFrame = (id) => clearTimeout(id);
  vm.runInContext(CONTENT, vm.createContext(win), { filename: 'content.js' });
  return { win, state };
}

function mkVideo(win, { src = '', current = '', w = 640, h = 360, top = 100, left = 100 } = {}) {
  const v = win.document.createElement('video');
  Object.defineProperty(v, 'currentSrc', { value: current || src, configurable: true });
  Object.defineProperty(v, 'src', { value: src, configurable: true });
  Object.defineProperty(v, 'isConnected', { get: () => !!v.parentNode, configurable: true });
  v.getBoundingClientRect = () => ({ top, left, right: left + w, bottom: top + h, width: w, height: h });
  return v;
}

const badges = (win) => win.document.querySelectorAll('.hyperfetch-video-badge');

let passed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ok  ' + name); }
  catch (e) { console.error('FAIL  ' + name + '\n      ' + (e.message || e)); process.exitCode = 1; }
}

(async () => {
  await test('direct-src video gets one top-right badge', async () => {
    const { win } = makeEnv();
    const v = mkVideo(win, { src: 'https://cdn.x/movie.mp4', w: 640, h: 360, top: 100, left: 100 });
    win.document.body.appendChild(v);
    await wait(300);
    const b = badges(win);
    assert.strictEqual(b.length, 1, 'badge count');
    const host = b[0];
    assert.strictEqual(host.style.position, 'fixed', 'fixed positioned');
    assert.strictEqual(host.style.display, 'block', 'visible');
    assert.ok(!v.contains(host), 'must NOT be a child of <video>');
    const left = parseInt(host.style.left, 10);
    const top = parseInt(host.style.top, 10);
    assert.ok(Math.abs(left - 612) <= 8, 'near right edge, left=' + left);
    assert.ok(Math.abs(top - 110) <= 8, 'near top, top=' + top);
  });

  await test('tiny video has no visible badge', async () => {
    const { win } = makeEnv();
    win.document.body.appendChild(mkVideo(win, { src: 'https://cdn.x/c.mp4', w: 80, h: 45 }));
    await wait(300);
    const hidden = Array.from(badges(win)).every((h) => h.style.display === 'none');
    assert.ok(hidden, 'tiny video badge hidden');
  });

  await test('blob video: badge appears only after a stream is sniffed', async () => {
    const { win, state } = makeEnv();
    win.document.body.appendChild(mkVideo(win, { src: 'blob:https://example.com/x', w: 800, h: 450 }));
    await wait(300);
    assert.strictEqual(badges(win).length, 0, 'no badge without a stream');
    assert.strictEqual(typeof state.msgListener, 'function', 'message listener registered');
    state.msgListener({ type: 'SNIFFED_MEDIA', url: 'https://cdn.x/master.m3u8', kind: 'hls', filename: 'master.m3u8' });
    await wait(300);
    assert.strictEqual(badges(win).length, 1, 'badge after HLS sniff');
  });

  await test('badge removed when the video leaves the DOM', async () => {
    const { win } = makeEnv();
    const v = mkVideo(win, { src: 'https://cdn.x/a.mp4' });
    win.document.body.appendChild(v);
    await wait(300);
    assert.strictEqual(badges(win).length, 1);
    v.remove();
    await wait(300);
    assert.strictEqual(badges(win).length, 0, 'badge cleaned up');
  });

  await test('two downloadable videos -> two badges', async () => {
    const { win } = makeEnv();
    win.document.body.appendChild(mkVideo(win, { src: 'https://cdn.x/a.mp4', top: 50 }));
    win.document.body.appendChild(mkVideo(win, { src: 'https://cdn.x/b.mp4', top: 500 }));
    await wait(300);
    assert.strictEqual(badges(win).length, 2);
  });

  await test('clicking the badge forwards url + title-based filename', async () => {
    const { win, state } = makeEnv({ openShadow: true });
    win.document.title = 'My Holiday Clip';
    const v = mkVideo(win, { src: 'https://cdn.x/stream-x9f3a2.mp4' });
    win.document.body.appendChild(v);
    await wait(300);
    const btn = badges(win)[0].shadowRoot.querySelector('button');
    btn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    assert.ok(state.sent, 'a message was sent');
    assert.strictEqual(state.sent.type, 'DOWNLOAD_URL');
    assert.strictEqual(state.sent.url, 'https://cdn.x/stream-x9f3a2.mp4');
    // saved as the video title, not the opaque URL filename
    assert.strictEqual(state.sent.filename, 'My Holiday Clip.mp4');
  });

  await test('HLS stream named after the title -> <title>.m3u8 (app makes .ts)', async () => {
    const { win, state } = makeEnv({ openShadow: true });
    win.document.title = 'Episode 1: Pilot - SomeSite';
    const v = mkVideo(win, { src: 'blob:https://example.com/abc', w: 800, h: 450 });
    win.document.body.appendChild(v);
    await wait(200);
    state.msgListener({ type: 'SNIFFED_MEDIA', url: 'https://cdn.x/hls/master.m3u8', kind: 'hls', filename: 'master.m3u8' });
    await wait(300);
    const btn = badges(win)[0].shadowRoot.querySelector('button');
    btn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    // ':' sanitized to space; extension .m3u8 so the app rewrites to .ts
    assert.strictEqual(state.sent.filename, 'Episode 1 Pilot - SomeSite.m3u8');
  });

  await test('element title/aria-label beats the page title', async () => {
    const { win, state } = makeEnv({ openShadow: true });
    win.document.title = 'Generic Page Title';
    const v = mkVideo(win, { src: 'https://cdn.x/v.mp4' });
    v.setAttribute('aria-label', 'The Real Video Name');
    win.document.body.appendChild(v);
    await wait(300);
    const btn = badges(win)[0].shadowRoot.querySelector('button');
    btn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    assert.strictEqual(state.sent.filename, 'The Real Video Name.mp4');
  });

  console.log(`\n${passed} passed` + (process.exitCode ? ' (with failures)' : ''));
  // content.js installs setInterval timers on the jsdom window which keep the
  // node event loop alive; exit explicitly once assertions are done.
  process.exit(process.exitCode || 0);
})();
