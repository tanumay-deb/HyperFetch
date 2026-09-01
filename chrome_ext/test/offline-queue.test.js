/**
 * Behavior tests for the offline download queue in background.js.
 *
 * When the app is not running a download is held in chrome.storage.local and
 * replayed once the app answers again, so a click is never lost just because
 * the app happened to be closed.
 *
 *   cd chrome_ext/test && npm install && npm test
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const BG = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function makeEnv({ online = true, port = 21456 } = {}) {
  const state = {
    stored: { token: 'tok', enabled: true },
    posts: [],           // download POSTs that reached the "app"
    online,
    menuHandler: null,
    msgHandler: null,
    createdHandler: null,
    cancelled: [],       // download ids the extension took away from Chrome
    erased: [],
    port,                // which port the fake app listens on
    pinged: [],          // ports the worker tried, in order
  };
  const ctx = {
    console,
    setTimeout,
    clearTimeout,
    navigator: { userAgent: 'test' },
    fetch: (url, init) => {
      if (!state.online) return Promise.reject(new Error('offline'));
      if (/\/ping$/.test(url)) {
        // state.port decides which port this fake app answers on, so the
        // worker's new-port-then-old-port resolution can actually be tested.
        const p = Number(new URL(url).port);
        state.pinged.push(p);
        return p === state.port
          ? Promise.resolve({ ok: true, status: 200 })
          : Promise.reject(new Error('nothing on that port'));
      }
      if (/\/pair$/.test(url)) {
        return Promise.resolve({
          ok: true, status: 200, json: () => Promise.resolve({ token: 'tok' }),
        });
      }
      // Gate every request on the port too, not just /ping: an app that is not
      // listening does not answer /download either, and without this "no app
      // anywhere" would still look like a successful send.
      if (Number(new URL(url).port) !== state.port) {
        return Promise.reject(new Error('nothing on that port'));
      }
      state.posts.push(JSON.parse(init.body));
      return Promise.resolve({
        ok: true, status: 200, json: () => Promise.resolve({ status: 'ok' }),
      });
    },
    chrome: {
      runtime: {
        lastError: null,
        onInstalled: { addListener: () => {} },
        onStartup: { addListener: () => {} },
        onMessage: { addListener: (cb) => { state.msgHandler = cb; } },
        getURL: (p) => p,
        setUninstallURL: () => {},
      },
      storage: {
        local: {
          get: (defs, cb) => {
            const out = {};
            for (const k of Object.keys(defs)) {
              out[k] = k in state.stored ? state.stored[k] : defs[k];
            }
            cb(out);
          },
          set: (o, cb) => { Object.assign(state.stored, o); cb && cb(); },
          remove: (k, cb) => { delete state.stored[k]; cb && cb(); },
        },
        onChanged: { addListener: () => {} },
      },
      cookies: { getAll: (q, cb) => cb([]) },
      contextMenus: {
        removeAll: (cb) => cb && cb(),
        create: () => {},
        onClicked: { addListener: (cb) => { state.menuHandler = cb; } },
      },
      tabs: { create: () => {}, sendMessage: () => {} },
      downloads: {
        // A real enough downloads API that the onCreated capture listener
        // actually registers; without onCreated background.js skips it.
        onCreated: { addListener: (cb) => { state.createdHandler = cb; } },
        cancel: (id, cb) => { state.cancelled.push(id); cb && cb(); },
        erase: (q, cb) => { state.erased.push(q.id); cb && cb(); },
      },
      webRequest: {
        onBeforeRequest: { addListener: () => {} },
        onResponseStarted: { addListener: () => {} },
      },
    },
  };
  ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInContext(BG, ctx, { filename: 'background.js' });
  return { ctx, state };
}

const held = (state) => state.stored.pending || [];

(async () => {
  // ---- offline: the download is kept, not dropped --------------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', 'https://x/', () => {});
    await wait(30);
    assert.strictEqual(held(state).length, 1, 'the download was lost');
    assert.strictEqual(held(state)[0].url, 'https://x/a.zip');
    console.log('  ok  an offline download is held');
  }

  // ---- cookies are never written to disk -----------------------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', 'https://x/', () => {});
    await wait(30);
    const blob = JSON.stringify(held(state));
    assert.ok(!/cookie/i.test(blob),
      'cookies were persisted — storage.local is not encrypted and a held ' +
      'item can sit there for days');
    console.log('  ok  cookies are not persisted with a held download');
  }

  // ---- back online: held downloads are replayed ----------------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    ctx.sendToApp('https://x/b.zip', 'b.zip', '', () => {});
    await wait(30);
    assert.strictEqual(held(state).length, 2);

    state.online = true;
    ctx.flushPending();
    await wait(50);
    const urls = state.posts.map((p) => p.url).sort();
    assert.deepStrictEqual(urls, ['https://x/a.zip', 'https://x/b.zip'],
      'held downloads were not replayed');
    assert.strictEqual(held(state).length, 0, 'the queue was not cleared');
    console.log('  ok  held downloads replay when the app returns');
  }

  // ---- a failed replay is kept, not lost -----------------------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    await wait(30);
    ctx.flushPending();              // still offline
    await wait(50);
    assert.strictEqual(held(state).length, 1,
      'a replay that failed dropped the download');
    console.log('  ok  a failed replay stays queued');
  }

  // ---- the same URL is not queued twice ------------------------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    await wait(20);
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    await wait(20);
    assert.strictEqual(held(state).length, 1, 'queued the same URL twice');
    console.log('  ok  a repeated URL is queued once');
  }

  // ---- the buffer is bounded ----------------------------------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    for (let i = 0; i < 60; i++) {
      ctx.sendToApp(`https://x/f${i}.zip`, `f${i}.zip`, '', () => {});
      await wait(2);
    }
    await wait(40);
    assert.ok(held(state).length <= 50,
      `queue grew to ${held(state).length} — it is a buffer, not a history`);
    // newest kept: an old queued click is the one already forgotten about
    assert.ok(held(state).some((p) => p.url.endsWith('f59.zip')),
      'dropped the newest instead of the oldest');
    console.log('  ok  the queue is bounded and keeps the newest');
  }

  // ---- a successful send drains anything waiting ---------------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/old.zip', 'old.zip', '', () => {});
    await wait(30);
    state.online = true;
    ctx.sendToApp('https://x/new.zip', 'new.zip', '', () => {});
    await wait(60);
    const urls = state.posts.map((p) => p.url);
    assert.ok(urls.includes('https://x/new.zip'), 'the new download never sent');
    assert.ok(urls.includes('https://x/old.zip'),
      'the held download was not drained by a successful send');
    console.log('  ok  a successful send drains the queue');
  }

  // ---- the popup asks how many are waiting ---------------------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    ctx.sendToApp('https://x/b.zip', 'b.zip', '', () => {});
    await wait(30);
    let reply = null;
    const async_ = state.msgHandler({ type: 'PENDING_COUNT' }, {},
                                    (r) => { reply = r; });
    assert.strictEqual(async_, true,
      'must return true or the popup never gets the reply');
    await wait(20);
    assert.strictEqual(reply && reply.count, 2);
    console.log('  ok  PENDING_COUNT reports the held downloads');
  }

  // ---- opening the popup drains the queue ----------------------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    await wait(30);
    state.online = true;                       // app came back
    state.msgHandler({ type: 'HYPERFETCH_FLUSH' }, {}, () => {});
    await wait(60);
    assert.ok(state.posts.some((p) => p.url === 'https://x/a.zip'),
      'opening the popup did not drain the queue');
    assert.strictEqual(held(state).length, 0);
    console.log('  ok  HYPERFETCH_FLUSH drains when the app is back');
  }

  // ---- a capture the browser already handled is NOT replayed --------------
  // The bug this guards: with the app closed, onCreated fired, the POST
  // failed, and the item went into the queue anyway. Because the POST failed
  // the extension never cancelled Chrome's download, so Chrome kept the file —
  // and the next time the app started, the queue drained and the app fetched a
  // second copy of something already in the Downloads folder.
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    assert.ok(state.createdHandler, 'the capture listener never registered');

    state.createdHandler({ id: 7, url: 'https://x/movie.mkv',
                           filename: 'C:\Users\me\Downloads\movie.mkv' });
    await wait(40);

    assert.strictEqual(held(state).length, 0,
      'an auto-capture was queued for replay — the app will download a second ' +
      'copy of the file Chrome already has');
    assert.deepStrictEqual(state.cancelled, [],
      "cancelled the browser's download even though the app never took it");

    // and nothing appears when the app comes back
    state.online = true;
    ctx.flushPending();
    await wait(50);
    assert.deepStrictEqual(state.posts, [],
      'the capture was replayed to the app on the next launch');
    console.log('  ok  an offline capture is not replayed when the app returns');
  }

  // ---- but an explicit request still is ------------------------------------
  // The distinction that makes the guard correct: a right-click has no
  // fallback. If it is dropped the file is simply never downloaded.
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/asked-for.zip', 'asked-for.zip', '', () => {});
    await wait(30);
    assert.strictEqual(held(state).length, 1,
      'an explicitly requested download was dropped');

    state.online = true;
    ctx.flushPending();
    await wait(50);
    assert.deepStrictEqual(state.posts.map((p) => p.url),
      ['https://x/asked-for.zip'],
      'an explicit download was not replayed');
    console.log('  ok  an explicit download is still held and replayed');
  }

  // ---- online, the capture is taken off Chrome as before -------------------
  {
    const { ctx, state } = makeEnv({ online: true });
    await wait(10);
    state.createdHandler({ id: 9, url: 'https://x/taken.bin',
                           filename: 'taken.bin' });
    await wait(40);
    assert.deepStrictEqual(state.posts.map((p) => p.url), ['https://x/taken.bin'],
      'the capture never reached the app');
    console.log('  ok  an online capture still reaches the app');
  }

  // ---- the port move must not strand users on an older app ---------------
  // Chrome updates extensions within hours; the desktop app is updated by hand.
  // If the extension only spoke to the new port, publishing it would break
  // every existing user until they happened to update, with each click landing
  // silently in the offline queue.
  {
    const { ctx, state } = makeEnv({ port: 5000 });   // user on an older app
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    await wait(60);
    assert.deepStrictEqual(state.posts.map((p) => p.url), ['https://x/a.zip'],
      'the download never reached an app still listening on the old port');
    assert.strictEqual(held(state).length, 0, 'it was queued instead of sent');
    console.log('  ok  an app on the old port is still reached');
  }

  {
    const { ctx, state } = makeEnv({ port: 21456 });
    await wait(10);
    ctx.sendToApp('https://x/b.zip', 'b.zip', '', () => {});
    await wait(60);
    assert.deepStrictEqual(state.posts.map((p) => p.url), ['https://x/b.zip']);
    assert.strictEqual(state.pinged[0], 21456,
      'the new port must be tried first, or every new install pays for the old');
    console.log('  ok  the new port is tried first');
  }

  {
    const { ctx, state } = makeEnv({ port: 0 });      // nothing listening
    await wait(10);
    ctx.sendToApp('https://x/c.zip', 'c.zip', '', () => {});
    await wait(80);
    assert.strictEqual(held(state).length, 1,
      'with no app at all the download must still be held, not dropped');
    console.log('  ok  no app on either port still holds the download');
  }

  console.log('offline-queue: all passed');
})().catch((e) => { console.error(e); process.exit(1); });
