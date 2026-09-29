/**
 * Behavior tests for background.js: the offline download queue, the capture of
 * downloads the browser starts, and applying an update.
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

// firefox: Firefox's shape of the same APIs - a moz-extension: address, and
// downloads.onCreated where Chrome has onDeterminingFilename. pairRefused and
// requireToken play the app as Firefox meets it: /pair refuses an address it
// cannot know, and a download without a token is a 401.
function makeEnv({ online = true, port = 21456, reply = 'ok', delayMs = 0,
                   hang = false, patchSource = null, firefox = false,
                   pairRefused = false, requireToken = false,
                   pairAnswer = 'unavailable' } = {}) {
  const state = {
    stored: { token: 'tok', enabled: true },
    posts: [],           // download POSTs that reached the "app"
    online,
    menuHandler: null,
    msgHandler: null,
    filenameHandler: null,
    order: [],           // 'cancel' / 'suggest', in the order they happened
    reply,               // what the fake app answers a download with
    delayMs,             // how long the fake app takes to answer one
    hang,                // the fake app takes a download and never answers
    updateHandler: null, // background.js's onUpdateAvailable listener
    reloads: 0,          // chrome.runtime.reload() calls
    cancelled: [],       // download ids the extension took away from Chrome
    erased: [],
    port,                // which port the fake app listens on
    pinged: [],          // ports the worker tried, in order
    createdHandler: null, // downloads.onCreated listener (Firefox's capture)
    tabMsgs: [],         // messages the worker sent to pages (toasts)
    pairAnswer,          // what the fake app says to /pair/request
    pairAsks: [],        // codes Firefox asked /pair/request with
    cookieQueries: [],   // what each cookies.getAll asked for
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
      if (/\/pair\/request$/.test(url)) {
        // Firefox's pairing: the person answers in the app (see pairing.py).
        state.pairAsks.push(JSON.parse(init.body).code);
        const a = state.pairAnswer;
        const body = a === 'approved' ? { status: a, token: 'tok-ff' } : { status: a };
        const code = { approved: 200, pending: 202, denied: 403, unavailable: 404 }[a] || 400;
        return Promise.resolve({ ok: code < 300, status: code, json: () => Promise.resolve(body) });
      }
      if (/\/pair$/.test(url)) {
        if (pairRefused) {
          return Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({}) });
        }
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
      if (requireToken && !JSON.parse(init.body).token) {
        return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) });
      }
      if (state.hang) return new Promise(() => {});
      const answer = {
        ok: true, status: 200, json: () => Promise.resolve({ status: state.reply }),
      };
      return state.delayMs
        ? new Promise((r) => setTimeout(() => r(answer), state.delayMs))
        : Promise.resolve(answer);
    },
    chrome: {
      runtime: {
        lastError: null,
        onInstalled: { addListener: () => {} },
        onStartup: { addListener: () => {} },
        onMessage: { addListener: (cb) => { state.msgHandler = cb; } },
        getURL: (p) => (firefox ? 'moz-extension://0f1e2d3c/' : '') + p,
        setUninstallURL: () => {},
        onUpdateAvailable: { addListener: (cb) => { state.updateHandler = cb; } },
        reload: () => { state.reloads++; state.order.push('reload'); },
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
      cookies: { getAll: (q, cb) => { state.cookieQueries.push(q); cb([]); } },
      contextMenus: {
        removeAll: (cb) => cb && cb(),
        create: () => {},
        onClicked: { addListener: (cb) => { state.menuHandler = cb; } },
      },
      tabs: { create: () => {}, sendMessage: (id, msg) => { state.tabMsgs.push(msg); } },
      downloads: Object.assign({
        // Chrome has both events and must be captured through only the one;
        // Firefox has onCreated alone.
        onCreated: { addListener: (cb) => { state.createdHandler = cb; } },
        cancel: (id, cb) => { state.cancelled.push(id); state.order.push('cancel'); cb && cb(); },
        erase: (q, cb) => { state.erased.push(q.id); cb && cb(); },
      }, firefox ? {} : {
        onDeterminingFilename: { addListener: (cb) => { state.filenameHandler = cb; } },
      }),
      webRequest: {
        onBeforeRequest: { addListener: () => {} },
        onResponseStarted: { addListener: () => {} },
      },
    },
  };
  ctx.self = ctx;
  ctx.crypto = require('crypto').webcrypto;   // the pairing code
  vm.createContext(ctx);
  vm.runInContext(patchSource ? patchSource(BG) : BG, ctx, { filename: 'background.js' });
  return { ctx, state };
}

const held = (state) => state.stored.pending || [];
const HOUR = 60 * 60 * 1000;

// Fire the browser-download capture the way Chrome does. Chrome waits for
// suggest() before it saves anything, so every call is recorded: it has to
// happen exactly once, whatever the app did.
function capture(state, item) {
  const calls = [];
  const ret = state.filenameHandler(item, (...args) => {
    calls.push(args);
    state.order.push('suggest');
  });
  return { ret, calls };
}

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
  // The bug this guards: with the app closed, the capture fired, the POST
  // failed, and the item went into the queue anyway. Because the POST failed
  // the extension never cancelled Chrome's download, so Chrome kept the file —
  // and the next time the app started, the queue drained and the app fetched a
  // second copy of something already in the Downloads folder.
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    assert.ok(state.filenameHandler, 'the capture listener never registered');

    const { ret, calls } = capture(state, { id: 7, url: 'https://x/movie.mkv',
                                            filename: 'C:\Users\me\Downloads\movie.mkv' });
    assert.strictEqual(ret, true,
      'the listener must return true, or Chrome ignores a suggest() that comes later');
    await wait(40);

    assert.strictEqual(calls.length, 1,
      'a closed app left the download waiting instead of handing it back to Chrome');
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
    console.log('  ok  an offline capture goes back to Chrome and is not replayed');
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

  // ---- online, a queued capture is taken off Chrome, then let go -----------
  {
    const { state } = makeEnv({ online: true, reply: 'queued' });
    await wait(10);
    const { calls } = capture(state, { id: 9, url: 'https://x/taken.bin',
                                       filename: 'taken.bin' });
    await wait(40);
    assert.deepStrictEqual(state.posts.map((p) => p.url), ['https://x/taken.bin'],
      'the capture never reached the app');
    assert.deepStrictEqual(state.cancelled, [9], "Chrome's copy was not cancelled");
    assert.deepStrictEqual(state.erased, [9], 'the cancelled entry was left in the list');
    assert.strictEqual(calls.length, 1, 'suggest() must be called exactly once');
    assert.deepStrictEqual(state.order, ['cancel', 'suggest'],
      'released before cancelling — Chrome would start saving the file first');
    console.log('  ok  a queued capture is cancelled in Chrome, then released');
  }

  // ---- a type the app does not take goes straight back to Chrome -----------
  {
    const { state } = makeEnv({ online: true, reply: 'ignored' });
    await wait(10);
    const { calls } = capture(state, { id: 10, url: 'https://x/page.html',
                                       filename: 'page.html' });
    await wait(40);
    assert.strictEqual(state.posts.length, 1, 'the app was not asked');
    assert.deepStrictEqual(state.cancelled, [], 'cancelled a download the app refused');
    assert.strictEqual(calls.length, 1, 'the refused download was never released');
    console.log('  ok  an ignored capture is left with Chrome');
  }

  // ---- the file is judged by the name Chrome resolved, not by the URL ------
  // The reported bug. A GitHub release redirects to a signed URL with no
  // extension in it, so judged by the URL a listed .exe was "not on the list".
  {
    const { state } = makeEnv({ online: true, reply: 'queued' });
    await wait(10);
    const signed = 'https://release-assets.githubusercontent.com/' +
                   'github-production-release-asset/173333385/86bf6e94-f851?sig=abc';
    capture(state, {
      id: 11,
      url: 'https://github.com/o/r/releases/download/v1.9.27/Salad-1.9.27.exe',
      finalUrl: signed,
      filename: 'Salad-1.9.27.exe',
      referrer: 'https://github.com/o/r/releases',
    });
    await wait(40);
    const sent = state.posts[0];
    assert.ok(sent, 'nothing reached the app');
    assert.strictEqual(sent.filename, 'Salad-1.9.27.exe',
      "the app was given the URL's last segment instead of the real filename");
    assert.strictEqual(sent.url, signed, 'the final URL is the one to fetch');
    assert.strictEqual(sent.auto, true, 'a capture must let the app apply its list');
    assert.strictEqual(sent.referrer, 'https://github.com/o/r/releases',
      'the page the download came from was not passed on');
    console.log('  ok  a capture is judged by the filename Chrome resolved');
  }

  // ---- a slow app never holds a download for long --------------------------
  {
    const cap = vm.runInContext('HANDOFF_WAIT_MS', makeEnv().ctx);
    const { state } = makeEnv({ online: true, reply: 'queued', delayMs: cap + 250 });
    await wait(10);
    const { calls } = capture(state, { id: 12, url: 'https://x/slow.zip',
                                       filename: 'slow.zip' });
    await wait(100);
    assert.strictEqual(calls.length, 0, 'released before the app had a chance to answer');
    await wait(cap);
    assert.strictEqual(calls.length, 1,
      `the download was still waiting after ${cap} ms — a hung app stalls every download`);
    // The app does take it in the end: Chrome's copy still has to go, and
    // suggest() must not be called a second time.
    await wait(400);
    assert.deepStrictEqual(state.cancelled, [12],
      'a late "queued" left the file in Chrome as well');
    assert.strictEqual(calls.length, 1, 'suggest() was called twice');
    console.log('  ok  a slow app is capped, and a late answer still takes the file');
  }

  // ---- with capture switched off, the app is not asked ---------------------
  {
    const { state } = makeEnv({ online: true });
    state.stored.enabled = false;
    await wait(10);
    const { calls } = capture(state, { id: 13, url: 'https://x/a.zip', filename: 'a.zip' });
    await wait(30);
    assert.deepStrictEqual(state.posts, [], 'asked the app with capture turned off');
    assert.strictEqual(calls.length, 1, 'the download was not handed back');
    console.log('  ok  capture off hands every download straight back');
  }

  // ---- a blob: download is not the app's to fetch --------------------------
  {
    const { state } = makeEnv({ online: true });
    await wait(10);
    const { calls } = capture(state, { id: 14, url: 'blob:https://x/5f3c', filename: 'x.bin' });
    assert.strictEqual(calls.length, 1, 'a blob: download was made to wait');
    await wait(20);
    assert.deepStrictEqual(state.posts, []);
    console.log('  ok  a blob: download is released without asking the app');
  }

  // ---- an expired hold is dropped, not replayed ----------------------------
  // Replaying an old signed link fetches the "link expired" page, and the app
  // saves that under the file's name.
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/old.exe', 'old.exe', '', () => {});
    await wait(30);
    state.stored.pending[0].at = Date.now() - 2 * HOUR;
    state.online = true;
    ctx.flushPending();
    await wait(50);
    assert.deepStrictEqual(state.posts, [], 'an expired download was replayed');
    assert.strictEqual(held(state).length, 0, 'the expired download was left in storage');
    console.log('  ok  a download held for over an hour is dropped unsent');
  }

  // ---- the popup's count leaves out expired holds --------------------------
  {
    const { state } = makeEnv({ online: false });
    await wait(10);
    state.stored.pending = [
      { url: 'https://x/stale.zip', filename: 'stale.zip', at: Date.now() - 2 * HOUR },
      { url: 'https://x/live.zip', filename: 'live.zip', at: Date.now() },
    ];
    let reply = null;
    state.msgHandler({ type: 'PENDING_COUNT' }, {}, (r) => { reply = r; });
    await wait(20);
    assert.strictEqual(reply && reply.count, 1, 'counted a download that will never be sent');
    console.log('  ok  PENDING_COUNT leaves out expired holds');
  }

  // ---- a failed replay keeps its age ---------------------------------------
  // Otherwise every blip of the app re-stamps it, and an expired link lives on.
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    const then = Date.now() - 50 * 60 * 1000;          // fifty minutes ago
    state.stored.pending = [{ url: 'https://x/a.zip', filename: 'a.zip',
                              referrer: '', extra: {}, at: then }];
    ctx.flushPending();                                 // still offline
    await wait(50);
    assert.strictEqual(held(state).length, 1, 'the failed replay was dropped');
    assert.strictEqual(held(state)[0].at, then,
      'a failed replay was re-stamped as new, so it would never expire');
    console.log('  ok  a failed replay keeps the age it had');
  }

  // ---- a page that falls back to the browser is not also held --------------
  // The overlay's offline path opens the file in the browser. Holding it as
  // well meant a second copy from the app on the next launch.
  {
    const { state } = makeEnv({ online: false });
    await wait(10);
    state.msgHandler({ type: 'DOWNLOAD_URL', url: 'https://x/clip.mp4',
                       filename: 'clip.mp4', hold: false },
                     { tab: { url: 'https://x/watch' } }, () => {});
    await wait(40);
    assert.strictEqual(held(state).length, 0,
      'held a download the page was already opening in the browser');

    state.msgHandler({ type: 'DOWNLOAD_URL', url: 'https://x/stream.m3u8',
                       filename: 'stream.m3u8', hold: true },
                     { tab: { url: 'https://x/watch' } }, () => {});
    await wait(40);
    assert.deepStrictEqual(Array.from(held(state), (p) => p.url), ['https://x/stream.m3u8'],
      'a download the browser cannot fall back on was dropped');
    console.log('  ok  only a send with no browser fallback is held');
  }

  // ---- the queue's own bookkeeping never reaches the app -------------------
  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    await wait(30);
    state.online = true;
    ctx.flushPending();
    state.msgHandler({ type: 'DOWNLOAD_URL', url: 'https://x/b.mp4',
                       filename: 'b.mp4', hold: false }, { tab: { url: 'https://x/' } },
                     () => {});
    await wait(60);
    assert.strictEqual(state.posts.length, 2);
    for (const p of state.posts) {
      assert.ok(!('heldAt' in p) && !('noHold' in p),
        "sent the offline queue's private fields to the app");
    }
    console.log('  ok  queue bookkeeping is not sent to the app');
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

  // ---- applying an update ---------------------------------------------------
  // Chrome only switches to a downloaded update when this worker stops, and the
  // media sniffer keeps waking it, so the extension reloads onto an update
  // itself — but only between handoffs.
  {
    const { state } = makeEnv({ online: true });
    await wait(10);
    assert.ok(state.updateHandler, 'no onUpdateAvailable listener was registered');
    state.updateHandler({ version: '9.9.9' });
    assert.strictEqual(state.reloads, 1, 'an idle worker did not reload onto the update');
    console.log('  ok  an update is applied at once when nothing is in flight');
  }

  {
    const { state } = makeEnv({ online: true, reply: 'queued', delayMs: 300 });
    await wait(10);
    const { calls } = capture(state, { id: 21, url: 'https://x/a.zip', filename: 'a.zip' });
    state.updateHandler({ version: '9.9.9' });
    await wait(100);
    assert.strictEqual(state.reloads, 0, 'reloaded while a download was being handed over');
    await wait(400);
    assert.strictEqual(calls.length, 1, 'the download was not released exactly once');
    assert.deepStrictEqual(state.cancelled, [21], "Chrome's copy was not cancelled");
    assert.strictEqual(state.reloads, 1, 'the update was never applied after the handoff');
    assert.deepStrictEqual(state.order, ['cancel', 'suggest', 'reload'],
      'reloaded before the handoff had finished');
    console.log('  ok  an update waits for a capture to finish, then applies');
  }

  {
    const { ctx, state } = makeEnv({ online: true, delayMs: 200 });
    await wait(10);
    let reloadsWhenDone = null;
    ctx.sendToApp('https://x/asked.zip', 'asked.zip', '', () => {
      reloadsWhenDone = state.reloads;
    });
    state.updateHandler({ version: '9.9.9' });
    await wait(80);
    assert.strictEqual(state.reloads, 0, 'reloaded in the middle of an explicit send');
    await wait(300);
    assert.strictEqual(reloadsWhenDone, 0, 'the send was cut off by the reload');
    assert.strictEqual(state.reloads, 1, 'the update was never applied after the send');
    console.log('  ok  an update waits for an explicit send to finish');
  }

  {
    const { ctx, state } = makeEnv({ online: false });
    await wait(10);
    ctx.sendToApp('https://x/later.zip', 'later.zip', '', () => {});
    state.updateHandler({ version: '9.9.9' });
    assert.strictEqual(state.reloads, 0);
    await wait(60);
    assert.strictEqual(state.reloads, 1, 'a failed send kept the update from applying');
    assert.strictEqual(held(state).length, 1,
      'the click was not held before the reload, so it would be lost');
    console.log('  ok  a failed send is held first, then the update applies');
  }

  {
    const { ctx, state } = makeEnv({ online: true });
    await wait(10);
    for (let i = 0; i < 3; i++) ctx.sendToApp(`https://x/${i}.zip`, `${i}.zip`, '', () => {});
    capture(state, { id: 30, url: 'https://x/c.zip', filename: 'c.zip' });
    await wait(60);
    assert.strictEqual(state.reloads, 0, 'reloaded with no update waiting');
    console.log('  ok  finishing work never reloads without an update');
  }

  {
    assert.strictEqual(vm.runInContext('UPDATE_WAIT_MS', makeEnv().ctx), 60 * 1000,
      'the backstop is meant to be a minute');
    const { ctx, state } = makeEnv({
      online: true, hang: true,
      patchSource: (src) => {
        const from = 'const UPDATE_WAIT_MS = 60 * 1000;';
        assert.ok(src.includes(from), 'UPDATE_WAIT_MS changed shape - update this test');
        return src.replace(from, 'const UPDATE_WAIT_MS = 150;');
      },
    });
    await wait(10);
    ctx.sendToApp('https://x/stuck.zip', 'stuck.zip', '', () => {});
    state.updateHandler({ version: '9.9.9' });
    await wait(60);
    assert.strictEqual(state.reloads, 0, 'did not wait for the handoff at all');
    await wait(200);
    assert.strictEqual(state.reloads, 1, 'a wedged handoff held the update back for good');
    console.log('  ok  a handoff that never ends cannot hold an update back');
  }

  {
    const { state } = makeEnv({ online: true });
    await wait(10);
    state.updateHandler({ version: '9.9.8' });
    state.updateHandler({ version: '9.9.9' });
    await wait(20);
    assert.strictEqual(state.reloads, 1, 'reloaded more than once');
    console.log('  ok  repeated update events reload once');
  }

  // ---- Firefox: capture through downloads.onCreated --------------------------
  // Firefox has no onDeterminingFilename. Its download is already under way when
  // the app is asked, so the app taking it means Firefox's copy is cancelled and
  // cleared; anything else leaves it alone.
  const ffItem = { id: 7, url: 'https://x/get?id=1', referrer: 'https://x/page',
                   filename: 'C:\\Users\\me\\Downloads\\setup.exe' };
  {
    const { state } = makeEnv({ firefox: true, reply: 'queued' });
    await wait(10);
    state.createdHandler(ffItem);
    await wait(30);
    assert.strictEqual(state.posts.length, 1, 'the app was not asked');
    assert.strictEqual(state.posts[0].filename, 'setup.exe',
      'Firefox has named the file by now - the app must judge that name, not the URL');
    assert.strictEqual(state.posts[0].auto, true, 'sent as an explicit click, not a capture');
    assert.deepStrictEqual(state.cancelled, [7], 'Firefox kept downloading a file the app took');
    assert.deepStrictEqual(state.erased, [7], 'the cancelled download was left in Firefox\'s list');
    console.log('  ok  Firefox: a download the app takes is cancelled and cleared');
  }

  {
    const { state } = makeEnv({ firefox: true, reply: 'ignored' });
    await wait(10);
    state.createdHandler(ffItem);
    await wait(30);
    assert.strictEqual(state.posts.length, 1);
    assert.deepStrictEqual(state.cancelled, [], 'cancelled a download the app did not want');
    console.log('  ok  Firefox: a download the app ignores stays with Firefox');
  }

  {
    const { state } = makeEnv({ firefox: true, reply: 'queued' });
    await wait(10);
    state.stored.enabled = false;
    state.createdHandler(ffItem);
    await wait(30);
    assert.strictEqual(state.posts.length, 0, 'capture was switched off, but the app was asked');
    assert.deepStrictEqual(state.cancelled, []);
    console.log('  ok  Firefox: capture switched off leaves downloads alone');
  }

  {
    const { state } = makeEnv({ firefox: true, online: false });
    await wait(10);
    state.createdHandler(ffItem);
    await wait(40);
    assert.deepStrictEqual(state.cancelled, [], 'the app is closed - Firefox must keep it');
    assert.strictEqual(held(state).length, 0,
      'held a capture: Firefox is saving it anyway, so a replay would fetch a second copy');
    console.log('  ok  Firefox: with the app closed the download stays in Firefox, unqueued');
  }

  {
    const { state } = makeEnv({ firefox: true, reply: 'queued' });
    await wait(10);
    state.createdHandler({ id: 8, url: 'blob:https://x/5f1c', filename: 'clip.mp4' });
    state.createdHandler({ id: 9, url: 'data:text/plain,hi', filename: 'hi.txt' });
    await wait(30);
    assert.strictEqual(state.posts.length, 0, 'the app cannot fetch blob:/data: URLs');
    console.log('  ok  Firefox: blob: and data: downloads are left alone');
  }

  {
    const chrome_ = makeEnv({});
    const firefox_ = makeEnv({ firefox: true });
    await wait(10);
    assert.ok(chrome_.state.filenameHandler, 'Chrome lost its capture');
    assert.strictEqual(chrome_.state.createdHandler, null,
      'Chrome captured through onCreated as well - every download would be sent twice');
    assert.ok(firefox_.state.createdHandler, 'Firefox has no capture at all');
    console.log('  ok  each browser captures through exactly one event');
  }

  {
    const { state } = makeEnv({ firefox: true, reply: 'queued', delayMs: 80 });
    await wait(10);
    state.createdHandler(ffItem);
    await wait(10);
    state.updateHandler({ version: '9.9.9' });
    await wait(20);
    assert.strictEqual(state.reloads, 0,
      'reloaded mid-handoff - the file could end up in Firefox and the app both');
    await wait(120);
    assert.deepStrictEqual(state.cancelled, [7]);
    assert.strictEqual(state.reloads, 1, 'the update was never applied after the handoff');
    console.log('  ok  Firefox: an update waits for a capture in flight');
  }

  // ---- Firefox: pairing is a paste, and the menu says so --------------------
  // /pair answers only the store listings' ids, which a Firefox install's random
  // address can never be, so the right-click toast has to say what to do.
  {
    const { state } = makeEnv({ firefox: true, pairRefused: true, requireToken: true });
    await wait(10);
    state.stored.token = '';
    state.menuHandler({ menuItemId: 'hyperfetch-download', linkUrl: 'https://x/a.zip' },
                      { id: 3, url: 'https://x/' });
    await wait(40);
    const toast = state.tabMsgs.find((m) => m.type === 'HYPERFETCH_TOAST');
    assert.ok(toast, 'no toast - the click failed silently');
    assert.ok(/paste the token/i.test(toast.text),
      `with no way to ask, the toast must say to paste the token: "${toast.text}"`);
    console.log('  ok  Firefox: an unpaired right-click says how to pair');
  }

  // ---- Firefox: pairing by asking the app ------------------------------------
  // /pair can never recognise a Firefox install, so it asks /pair/request with a
  // four-digit code; the app asks the person, showing the same code.
  {
    const { ctx, state } = makeEnv({ firefox: true, requireToken: true, pairAnswer: 'pending' });
    await wait(10);
    state.stored.token = '';
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    await wait(40);
    ctx.sendToApp('https://x/b.zip', 'b.zip', '', () => {});
    await wait(40);
    assert.ok(state.pairAsks.length >= 2, 'Firefox never asked the app to pair');
    assert.ok(state.pairAsks.every((c) => /^\d{4}$/.test(c)), `not four digits: ${state.pairAsks}`);
    assert.strictEqual(new Set(state.pairAsks).size, 1,
      'the code changed between asks - the one the person compares would not match');
    console.log('  ok  Firefox: an unpaired install asks with one four-digit code');
  }

  {
    const { ctx, state } = makeEnv({ firefox: true, requireToken: true, pairAnswer: 'approved' });
    await wait(10);
    state.stored.token = '';
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    await wait(40);
    assert.strictEqual(state.stored.token, 'tok-ff', 'the allowed token was not kept');
    assert.ok(!('pairCode' in state.stored), 'the code outlived the pairing');
    assert.ok(state.posts.some((p) => p.url === 'https://x/a.zip' && p.token === 'tok-ff'),
      'the download did not go through once allowed');
    console.log('  ok  Firefox: once allowed, the token is kept and the download goes through');
  }

  {
    const { state } = makeEnv({ firefox: true, requireToken: true, pairAnswer: 'pending' });
    await wait(10);
    state.stored.token = '';
    state.menuHandler({ menuItemId: 'hyperfetch-download', linkUrl: 'https://x/a.zip' },
                      { id: 3, url: 'https://x/' });
    await wait(60);
    const toast = state.tabMsgs.find((m) => m.type === 'HYPERFETCH_TOAST');
    const code = state.pairAsks[state.pairAsks.length - 1];
    assert.ok(toast && toast.text.includes(code) && /approve/i.test(toast.text),
      `while the app is asking, the toast must say so with the code: "${toast && toast.text}"`);
    console.log('  ok  Firefox: while the app asks, a failed click shows the code to look for');
  }

  {
    const { state } = makeEnv({ firefox: true, pairAnswer: 'approved' });
    await wait(10);
    let reply = null;
    const async_ = state.msgHandler({ type: 'PAIR_REQUEST' }, {}, (r) => { reply = r; });
    assert.strictEqual(async_, true, 'must return true or the page never gets the answer');
    await wait(40);
    assert.strictEqual(reply.status, 'approved');
    assert.ok(/^\d{4}$/.test(reply.code), 'the page gets the code to show');
    assert.ok(!('token' in reply), 'the token went to a page - it belongs to the worker alone');
    console.log('  ok  the popup and welcome page get the code, never the token');
  }

  {
    const { ctx, state } = makeEnv({});           // Chrome
    await wait(10);
    state.stored.token = '';
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {});
    await wait(40);
    assert.strictEqual(state.pairAsks.length, 0, 'Chrome pairs through /pair; it must never ask');
    console.log('  ok  Chrome keeps pairing on its own, never asking');
  }

  // ---- Firefox: containers ---------------------------------------------------
  // A login lives in the cookie store of the tab it happened in - a container's
  // or a private window's. The store is chosen per download; the app never
  // sees its name.
  // copied out of the vm's realm: strict deep-equal also compares prototypes
  const lastQuery = (state) => Object.assign({}, state.cookieQueries[state.cookieQueries.length - 1]);
  {
    const { state } = makeEnv({ firefox: true });
    await wait(10);
    state.menuHandler({ menuItemId: 'hyperfetch-download', linkUrl: 'https://x/a.zip' },
                      { id: 3, url: 'https://x/', cookieStoreId: 'firefox-container-2' });
    await wait(40);
    assert.deepStrictEqual(lastQuery(state), { url: 'https://x/a.zip', storeId: 'firefox-container-2' });
    assert.ok(state.posts.length && !('storeId' in state.posts[0]), 'the store name was sent to the app');
    console.log('  ok  Firefox: a right-click in a container sends that container\'s cookies');
  }

  {
    const { state } = makeEnv({ firefox: true });
    await wait(10);
    let reply = null;
    state.msgHandler({ type: 'DOWNLOAD_URL', url: 'https://x/v.mp4', filename: 'v.mp4' },
                     { tab: { url: 'https://x/', cookieStoreId: 'firefox-container-5' } },
                     (r) => { reply = r; });
    await wait(40);
    assert.deepStrictEqual(lastQuery(state), { url: 'https://x/v.mp4', storeId: 'firefox-container-5' });
    assert.ok(reply && reply.ok);
    console.log('  ok  Firefox: the page\'s own sends use the tab\'s container');
  }

  {
    const { state } = makeEnv({ firefox: true, reply: 'queued' });
    await wait(10);
    state.createdHandler(Object.assign({}, ffItem, { cookieStoreId: 'firefox-container-3' }));
    await wait(40);
    assert.deepStrictEqual(lastQuery(state), { url: ffItem.url, storeId: 'firefox-container-3' });
    console.log('  ok  Firefox: a capture in a container uses its cookies');
  }

  {
    const { ctx, state } = makeEnv({ firefox: true, online: false });
    await wait(10);
    ctx.sendToApp('https://x/a.zip', 'a.zip', '', () => {}, { storeId: 'firefox-container-4' });
    await wait(30);
    assert.strictEqual(held(state)[0].storeId, 'firefox-container-4', 'the held download lost its container');
    state.online = true;
    ctx.flushPending();
    await wait(60);
    assert.deepStrictEqual(lastQuery(state), { url: 'https://x/a.zip', storeId: 'firefox-container-4' },
      'the replay read the default store - the container\'s login was not used');
    console.log('  ok  a download held while the app was closed keeps its container');
  }

  {
    const { state } = makeEnv({});                 // Chrome: no containers
    await wait(10);
    state.menuHandler({ menuItemId: 'hyperfetch-download', linkUrl: 'https://x/a.zip' },
                      { id: 3, url: 'https://x/' });
    await wait(40);
    assert.deepStrictEqual(lastQuery(state), { url: 'https://x/a.zip' });
    console.log('  ok  Chrome reads the default cookie store as before');
  }

  console.log('offline-queue: all passed');
})().catch((e) => { console.error(e); process.exit(1); });
