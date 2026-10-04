/**
 * How Chrome and Edge ask the app for the pairing token.
 *
 * The app's /pair knows this extension by the Origin header and answers nobody
 * else. Measured on Edge 154 (Chromium 154), 2026-10-03: a GET from an
 * extension's service worker to a host it has permission for carries NO Origin
 * - plain, in cors mode or with a custom header. So by GET every fresh install
 * was refused and never paired ("i installed extension in a new profile in edge
 * and the extension did not auto pair"). A POST still carries it.
 *
 * So /pair is asked by POST. An app from before 2.7 knows only GET there and
 * answers a POST with 405: it is asked the old way then, which still works in
 * a browser that sends the header on a GET.
 *
 *   cd chrome_ext/test && npm install && npm test
 */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const BG = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');
const POPUP = fs.readFileSync(path.join(__dirname, '..', 'popup.js'), 'utf8');
const HTML = fs.readFileSync(path.join(__dirname, '..', 'popup.html'), 'utf8');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// The app's /pair, as each kind of app answers it.
//   'new'     2.7 on: POST and GET, to this extension
//   'old'     before: GET only; a POST is 405 Method Not Allowed
//   'refuses' any version, an extension it does not know: 403 either way
function pairEndpoint(kind, asked) {
  return (init) => {
    const method = (init && init.method) || 'GET';
    asked.push(method);
    const answer = (status, body) => Promise.resolve({
      ok: status < 300, status, json: () => Promise.resolve(body || {}),
    });
    if (kind === 'refuses') return answer(403);
    if (kind === 'old' && method !== 'GET') return answer(405);
    return answer(200, { token: 'tok-from-app' });
  };
}

function loadWorker(kind) {
  const noop = () => {};
  const stored = { token: '', enabled: true };
  const asked = [];
  const pair = pairEndpoint(kind, asked);
  const ctx = {
    console, setTimeout, clearTimeout, URL,
    navigator: { userAgent: 'test' },
    fetch: (url, init) => {
      if (/\/ping$/.test(url)) {
        return Number(new URL(url).port) === 21456
          ? Promise.resolve({ ok: true, status: 200 })
          : Promise.reject(new Error('nothing on that port'));
      }
      if (/\/pair$/.test(url)) return pair(init);
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'ok' }) });
    },
    chrome: {
      runtime: {
        lastError: null,
        onInstalled: { addListener: noop }, onStartup: { addListener: noop },
        onMessage: { addListener: noop },
        getURL: (p) => p, setUninstallURL: noop,
        onUpdateAvailable: { addListener: noop }, reload: noop,
      },
      storage: {
        local: {
          get: (defs, cb) => {
            const out = {};
            for (const k of Object.keys(defs)) out[k] = k in stored ? stored[k] : defs[k];
            cb(out);
          },
          set: (o, cb) => { Object.assign(stored, o); cb && cb(); },
          remove: (k, cb) => { delete stored[k]; cb && cb(); },
        },
        onChanged: { addListener: noop },
      },
      cookies: { getAll: (q, cb) => cb([]) },
      contextMenus: { removeAll: (cb) => cb && cb(), create: noop, onClicked: { addListener: noop } },
      tabs: { create: noop, sendMessage: noop },
      downloads: {
        onCreated: { addListener: noop }, onDeterminingFilename: { addListener: noop },
        cancel: noop, erase: noop,
      },
      webRequest: { onBeforeRequest: { addListener: noop }, onResponseStarted: { addListener: noop } },
    },
  };
  ctx.self = ctx;
  ctx.crypto = require('crypto').webcrypto;
  vm.createContext(ctx);
  vm.runInContext(BG, ctx, { filename: 'background.js' });
  return { ctx, stored, asked };
}

function loadPopup(kind) {
  const dom = new JSDOM(HTML, { url: 'chrome-extension://abc/popup.html', runScripts: 'outside-only' });
  const win = dom.window;
  const stored = { token: '', enabled: true };
  const asked = [];
  const pair = pairEndpoint(kind, asked);
  win.chrome = {
    runtime: {
      lastError: null,
      getManifest: () => ({ version: '1.9.1' }),
      sendMessage: (m, cb) => cb && cb({}),
      getURL: (p) => p,
    },
    storage: {
      local: {
        get: (defs, cb) => {
          const out = {};
          for (const k of Object.keys(defs)) out[k] = k in stored ? stored[k] : defs[k];
          cb(out);
        },
        set: (o, cb) => { Object.assign(stored, o); cb && cb(); },
        remove: (k, cb) => { delete stored[k]; cb && cb(); },
      },
    },
    tabs: { create: () => {}, query: (_q, cb) => cb && cb([]) },
  };
  win.fetch = (url, init) => {
    if (Number(new URL(url).port) !== 21456) return Promise.reject(new Error('nothing on that port'));
    if (/\/pair$/.test(url)) return pair(init);
    return Promise.resolve({
      ok: true, status: 200, json: () => Promise.resolve({ status: 'ok', needsToken: true }),
    });
  };
  vm.createContext(win);
  vm.runInContext(POPUP, win, { filename: 'popup.js' });
  return { doc: win.document, stored, asked };
}

let passed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ok  ' + name); }
  catch (e) { console.error('FAIL  ' + name + '\n      ' + (e.message || e)); process.exitCode = 1; }
}

(async () => {
  // ---- the worker: a click from an install that has no token yet -------------
  await test('the worker asks /pair by POST', async () => {
    const { ctx, stored, asked } = loadWorker('new');
    const token = await ctx.getToken();
    assert.deepStrictEqual(asked, ['POST'], 'a GET carries no Origin: the app refuses it');
    assert.strictEqual(token, 'tok-from-app');
    assert.strictEqual(stored.token, 'tok-from-app');
  });

  await test('the worker asks an older app again, by GET', async () => {
    const { ctx, stored, asked } = loadWorker('old');
    const token = await ctx.getToken();
    assert.deepStrictEqual(asked, ['POST', 'GET']);
    assert.strictEqual(token, 'tok-from-app');
    assert.strictEqual(stored.token, 'tok-from-app');
  });

  await test('the worker does not ask twice of an app that refuses it', async () => {
    const { ctx, stored, asked } = loadWorker('refuses');
    const token = await ctx.getToken();
    assert.deepStrictEqual(asked, ['POST']);
    assert.strictEqual(token, '');
    assert.strictEqual(stored.token, '');
  });

  // ---- the popup: opened on an install that has no token yet ------------------
  await test('the popup asks /pair by POST, and says paired', async () => {
    const { doc, stored, asked } = loadPopup('new');
    await wait(150);
    assert.deepStrictEqual(asked, ['POST']);
    assert.strictEqual(stored.token, 'tok-from-app');
    assert.ok(/paired/i.test(doc.getElementById('pairState').textContent)
              && !/not paired/i.test(doc.getElementById('pairState').textContent),
              doc.getElementById('pairState').textContent);
  });

  await test('the popup asks an older app again, by GET', async () => {
    const { stored, asked } = loadPopup('old');
    await wait(150);
    assert.deepStrictEqual(asked, ['POST', 'GET']);
    assert.strictEqual(stored.token, 'tok-from-app');
  });

  await test('the popup of an extension the app refuses stays unpaired', async () => {
    const { doc, stored, asked } = loadPopup('refuses');
    await wait(150);
    assert.deepStrictEqual(asked, ['POST']);
    assert.strictEqual(stored.token, '');
    assert.strictEqual(doc.getElementById('pairState').textContent.trim(), 'not paired');
  });

  console.log(`\n${passed} passed` + (process.exitCode ? ' (with failures)' : ''));
  process.exit(process.exitCode || 0);
})();
