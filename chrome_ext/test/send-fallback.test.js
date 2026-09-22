/**
 * content.js: what happens to a download sent from the page when the app is
 * offline. Exactly one of two things, never both:
 *   - the worker holds it (hold: true) and the page stays where it is, or
 *   - the page opens the file in the browser (hold: false) and nothing is held.
 * Both at once was how one click became two copies — the browser's straight
 * away, and the app's when the offline queue replayed on the next launch.
 *
 *   cd chrome_ext/test && npm install && npm test
 */
const { JSDOM, VirtualConsole } = require('jsdom');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const CONTENT = fs.readFileSync(path.join(__dirname, '..', 'content.js'), 'utf8');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function makeEnv({ appOnline = false, gone = false, throwOnSend = false } = {}) {
  // jsdom does not implement navigation. It reports the attempt on the virtual
  // console instead, and that report is how these tests see the fallback fire.
  const virtualConsole = new VirtualConsole();
  const state = { sent: [], navigations: 0, appOnline };
  virtualConsole.on('jsdomError', (e) => {
    if (/navigation/i.test(String(e && e.message))) state.navigations++;
  });
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    url: 'https://example.com/watch', pretendToBeVisual: true,
    runScripts: 'outside-only', virtualConsole,
  });
  const win = dom.window;
  win.chrome = {
    storage: {
      local: {
        get: (d, cb) => cb({ enabled: true, badgeCorner: 'top-right' }),
        set: (o, cb) => cb && cb(),
      },
      onChanged: { addListener: () => {} },
    },
    runtime: {
      // A live page script always has an id. After an update the old script's
      // chrome.runtime.id is gone, which is how content.js can tell.
      id: gone ? undefined : 'hyperfetch-test',
      onMessage: { addListener: () => {} },
      sendMessage: (m, cb) => {
        if (throwOnSend) throw new Error('Extension context invalidated.');
        if (m && m.type === 'DOWNLOAD_URL') state.sent.push(m);
        cb && cb({ ok: state.appOnline });
      },
      lastError: null,
    },
  };
  win.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
  win.cancelAnimationFrame = (id) => clearTimeout(id);
  const ctx = vm.createContext(win);
  vm.runInContext(CONTENT, ctx, { filename: 'content.js' });
  const send = (...args) => vm.runInContext('sendToApp', ctx)(...args);
  const toasts = () => Array.from(win.document.body.querySelectorAll('div'))
    .map((d) => d.textContent)
    .filter((t) => /offline|Sent to|Pair the extension|reload this page/.test(t));
  return { win, state, send, toasts };
}

let passed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ok  ' + name); }
  catch (e) { console.error('FAIL  ' + name + '\n      ' + (e.message || e)); process.exitCode = 1; }
}

(async () => {
  await test('an offline file send opens it in the browser and is not held', async () => {
    const { state, send, toasts } = makeEnv({ appOnline: false });
    send('https://cdn.x/clip.mp4', 'clip.mp4');
    await wait(20);
    assert.strictEqual(state.sent.length, 1, 'nothing was sent to the worker');
    assert.strictEqual(state.sent[0].hold, false,
      'asked the worker to hold a download the page is about to open itself');
    assert.strictEqual(state.navigations, 1, 'the browser fallback did not happen');
    assert.ok(toasts().some((t) => /downloading in browser/.test(t)),
      'the page did not say it was downloading in the browser');
  });

  await test('an offline video stream is held, and the page stays put', async () => {
    const { state, send, toasts } = makeEnv({ appOnline: false });
    send('https://cdn.x/hls/master.m3u8?token=abc', 'Episode 1.m3u8');
    await wait(20);
    assert.strictEqual(state.sent[0].hold, true,
      'an .m3u8 has no browser fallback — opening it saves the playlist text');
    assert.strictEqual(state.navigations, 0, 'navigated to a playlist');
    assert.ok(toasts().some((t) => /queued until HyperFetch is running/.test(t)),
      'the page did not say the stream was queued');
  });

  await test('a DASH manifest is held the same way', async () => {
    const { state, send } = makeEnv({ appOnline: false });
    send('https://cdn.x/v/manifest.mpd', 'v.mpd');
    await wait(20);
    assert.strictEqual(state.sent[0].hold, true);
    assert.strictEqual(state.navigations, 0);
  });

  await test('a quiet batch send is held, and neither navigates nor toasts', async () => {
    const { state, send, toasts } = makeEnv({ appOnline: false });
    send('https://cdn.x/a.zip', 'a.zip', { quiet: true });
    await wait(20);
    assert.strictEqual(state.sent[0].hold, true);
    assert.strictEqual(state.navigations, 0);
    assert.deepStrictEqual(toasts(), []);
  });

  await test('noNavigate is held and says so', async () => {
    const { state, send, toasts } = makeEnv({ appOnline: false });
    send('https://cdn.x/a.zip', 'a.zip', { noNavigate: true });
    await wait(20);
    assert.strictEqual(state.sent[0].hold, true);
    assert.strictEqual(state.navigations, 0);
    assert.ok(toasts().some((t) => /queued/.test(t)));
  });

  await test('a magnet falls back to the system handler and is not held', async () => {
    const { state, send } = makeEnv({ appOnline: false });
    send('magnet:?xt=urn:btih:abc');
    await wait(20);
    assert.strictEqual(state.sent[0].hold, false,
      'held a magnet the system torrent client is also being handed');
  });

  await test('online, a send is simply sent', async () => {
    const { state, send, toasts } = makeEnv({ appOnline: true });
    send('https://cdn.x/clip.mp4', 'clip.mp4');
    await wait(20);
    assert.strictEqual(state.navigations, 0, 'navigated although the app took it');
    assert.ok(toasts().some((t) => /Sent to Download Manager/.test(t)));
  });

  await test('after an update, a send from an already-open page says to reload it', async () => {
    const { state, send, toasts } = makeEnv({ appOnline: true, gone: true });
    send('https://cdn.x/clip.mp4', 'clip.mp4');   // must not throw
    await wait(20);
    assert.strictEqual(state.sent.length, 0, 'sent through an extension that is gone');
    assert.strictEqual(state.navigations, 0, 'navigated instead of saying what happened');
    assert.ok(toasts().some((t) => /reload this page/.test(t)),
      'a click on an out-of-date page did nothing at all');
  });

  await test('an extension that goes away mid-call is reported the same way', async () => {
    const { state, send, toasts } = makeEnv({ appOnline: true, throwOnSend: true });
    send('https://cdn.x/clip.mp4', 'clip.mp4');   // sendMessage throws; it must not escape
    await wait(20);
    assert.strictEqual(state.navigations, 0);
    assert.ok(toasts().some((t) => /reload this page/.test(t)));
  });

  await test('after an update, a magnet click is left to the browser', async () => {
    const click = ({ win }) => {
      const a = win.document.createElement('a');
      a.href = 'magnet:?xt=urn:btih:abc';
      win.document.body.appendChild(a);
      // false when a listener called preventDefault()
      return a.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
    };
    const live = makeEnv({ appOnline: true });
    const gone = makeEnv({ appOnline: true, gone: true });
    assert.strictEqual(click(live), false, 'a live page no longer takes magnet clicks');
    assert.strictEqual(click(gone), true,
      'an out-of-date page swallowed a magnet it can no longer deliver');
    await wait(20);
    assert.strictEqual(gone.state.sent.length, 0);
  });

  console.log(`\n${passed} passed` + (process.exitCode ? ' (with failures)' : ''));
  // content.js starts setInterval loops that would keep node running
  // forever, so every jsdom test here ends by exiting explicitly.
  process.exit(process.exitCode || 0);
})().catch((e) => { console.error(e); process.exit(1); });
