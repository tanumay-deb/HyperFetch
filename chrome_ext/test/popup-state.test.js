/**
 * What the popup says about pairing when it cannot reach the app.
 *
 * The bug this exists for: `needsToken` started as `false` and the /ping
 * failure path called refreshPairState() without ever setting it, so a popup
 * that had just printed "app not running" also printed "pairing: not required"
 * — two contradictory lines, one of them invented. The popup had no tests at
 * all, which is how it shipped.
 *
 *   cd chrome_ext/test && npm install && npm test
 */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const POPUP = fs.readFileSync(path.join(__dirname, '..', 'popup.js'), 'utf8');
const HTML = fs.readFileSync(path.join(__dirname, '..', 'popup.html'), 'utf8');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/** Run popup.js against the real popup.html, with the app on `port`
 *  (0 = nothing listening anywhere). */
function makeEnv({ port = 21456, token = '', needsToken = true } = {}) {
  const dom = new JSDOM(HTML, { url: 'chrome-extension://abc/popup.html',
                                runScripts: 'outside-only' });
  const win = dom.window;
  const stored = { token, enabled: true };

  win.chrome = {
    runtime: {
      lastError: null,
      getManifest: () => ({ version: '1.7.0' }),
      sendMessage: (_m, cb) => cb && cb({}),
      getURL: (p) => p,
    },
    storage: {
      local: {
        get: (defs, cb) => {
          const out = {};
          for (const k of Object.keys(defs)) {
            out[k] = k in stored ? stored[k] : defs[k];
          }
          cb(out);
        },
        set: (o, cb) => { Object.assign(stored, o); cb && cb(); },
        remove: (k, cb) => { delete stored[k]; cb && cb(); },
      },
    },
    tabs: { create: () => {}, query: (_q, cb) => cb && cb([]) },
  };

  win.fetch = (url) => {
    const p = Number(new URL(url).port);
    if (p !== port) return Promise.reject(new Error('nothing on that port'));
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ status: 'ok', needsToken }),
    });
  };

  vm.createContext(win);
  vm.runInContext(POPUP, win, { filename: 'popup.js' });
  return { win, doc: win.document, stored };
}

const pairText = (doc) => doc.getElementById('pairState').textContent.trim();
const statusText = (doc) => doc.getElementById('status').textContent.trim();

(async () => {
  // ---- the bug: unreachable app must not claim pairing is fine ------------
  {
    const { doc } = makeEnv({ port: 0 });          // nothing listening
    await wait(120);
    assert.ok(/not running/i.test(statusText(doc)),
      'the app is down, so the status line should say so');
    assert.ok(!/not required/i.test(pairText(doc)),
      'the popup claimed pairing was "not required" without ever reaching the '
      + 'app — it cannot know that');
    assert.ok(/unknown/i.test(pairText(doc)), `got: ${pairText(doc)}`);
    console.log('  ok  an unreachable app leaves pairing unknown, not "not required"');
  }

  // ---- and says the token is there, since that is why you opened it -------
  {
    const { doc } = makeEnv({ port: 0, token: 'saved-tok' });
    await wait(120);
    assert.ok(/saved/i.test(pairText(doc)), `got: ${pairText(doc)}`);
    assert.ok(/unreachable/i.test(pairText(doc)), `got: ${pairText(doc)}`);
    console.log('  ok  a saved token is still reported when the app is down');
  }

  // ---- a reachable app that wants a token --------------------------------
  {
    const { doc } = makeEnv({ port: 21456, token: '', needsToken: true });
    await wait(120);
    assert.strictEqual(pairText(doc), 'not paired');
    console.log('  ok  reachable and unpaired reads "not paired"');
  }

  {
    const { doc } = makeEnv({ port: 21456, token: 'tok', needsToken: true });
    await wait(120);
    assert.ok(/paired/.test(pairText(doc)) && !/not paired/.test(pairText(doc)),
      `got: ${pairText(doc)}`);
    console.log('  ok  reachable and paired reads "paired"');
  }

  // ---- only a reachable app may say a token is not needed ----------------
  {
    const { doc } = makeEnv({ port: 21456, needsToken: false });
    await wait(120);
    assert.strictEqual(pairText(doc), 'not required');
    console.log('  ok  only an app that answered can say "not required"');
  }

  // ---- the port fallback, from the popup's side --------------------------
  {
    const { doc } = makeEnv({ port: 5000, needsToken: true, token: 'tok' });
    await wait(200);
    assert.ok(/connected/i.test(statusText(doc)),
      'an app on the old port is still an app; the popup should connect to it');
    const ver = doc.getElementById('ver').textContent;
    assert.ok(/5000/.test(ver), `the footer should name the port in use: ${ver}`);
    assert.ok(/update/i.test(ver),
      'connecting on the legacy port should prompt to update the app');
    console.log('  ok  an app on the old port connects and prompts an update');
  }

  console.log('popup-state: all passed');
})().catch((e) => { console.error(e); process.exit(1); });
