/**
 * Behavior tests for the batch link grabber (right-click -> "Show all
 * downloadable links…", "Download all images on this page", "Download all
 * links in selection").
 *
 * Drives content.js inside jsdom: builds a page with a mix of real files and
 * ordinary navigation links, fires the HYPERFETCH_GRAB message the background
 * worker sends, and asserts what ends up in the panel and what is sent.
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

const PAGE = `<!DOCTYPE html><html><body>
  <a href="/files/setup.exe">installer</a>
  <a href="/files/manual.pdf">manual</a>
  <a href="/files/movie.mkv">movie</a>
  <a href="https://cdn.x/song.mp3">song</a>
  <a href="/about">about us</a>
  <a href="/contact.html">contact</a>
  <a href="javascript:void(0)">menu</a>
  <a href="magnet:?xt=urn:btih:abc&amp;dn=Thing">magnet</a>
  <a href="/files/pack.torrent">torrent</a>
  <a href="/files/setup.exe">duplicate installer</a>
  <div id="sel"><a href="/files/inside.zip">inside</a></div>
  <img src="https://cdn.x/photo.jpg">
  <a href="https://cdn.x/full.png"><img src="https://cdn.x/thumb.png"></a>
</body></html>`;

function makeEnv() {
  const dom = new JSDOM(PAGE, {
    url: 'https://example.com/page', pretendToBeVisual: true,
    runScripts: 'outside-only',
  });
  const win = dom.window;
  win.innerWidth = 1280;
  win.innerHeight = 800;
  // production uses a CLOSED shadow root; force open so the test can look in
  const real = win.Element.prototype.attachShadow;
  win.Element.prototype.attachShadow = function () { return real.call(this, { mode: 'open' }); };

  const state = { msgListener: null, sent: [], appOnline: true };
  win.chrome = {
    storage: {
      local: {
        get: (d, cb) => cb({ enabled: true, badgeCorner: 'top-right', token: '' }),
        set: (o, cb) => cb && cb(),
      },
      onChanged: { addListener: () => {} },
    },
    runtime: {
      onMessage: { addListener: (cb) => { state.msgListener = cb; } },
      sendMessage: (m, cb) => {
        if (m && m.type === 'DOWNLOAD_URL') state.sent.push(m);
        cb && cb({ ok: state.appOnline });
      },
      lastError: null,
    },
  };
  win.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
  win.cancelAnimationFrame = (id) => clearTimeout(id);
  vm.runInContext(CONTENT, vm.createContext(win), { filename: 'content.js' });
  return { win, state };
}

const host = (win) => win.document.getElementById('hyperfetch-grab-root');
const panel = (win) => { const h = host(win); return h && h.shadowRoot; };
const rows = (win) => [...panel(win).querySelectorAll('.row')];
const names = (win) => rows(win).map((r) => r.querySelector('.nm').textContent);

async function grab(state, scope) {
  state.msgListener({ type: 'HYPERFETCH_GRAB', scope });
  await wait(20);
}

(async () => {
  // ---- what gets collected -------------------------------------------------
  {
    const { win, state } = makeEnv();
    await grab(state, 'all');
    const got = names(win);
    assert.ok(got.includes('setup.exe'), 'missed an .exe');
    assert.ok(got.includes('manual.pdf'), 'missed a .pdf');
    assert.ok(got.includes('movie.mkv'), 'missed a video');
    assert.ok(got.includes('song.mp3'), 'missed audio on another host');
    assert.ok(got.includes('pack.torrent'), 'missed a .torrent');
    // A page is mostly navigation. Listing every href buries the files.
    assert.ok(!got.some((n) => /about|contact/.test(n)),
      'listed ordinary page links');
    assert.ok(!got.some((n) => /void|javascript/.test(n)),
      'listed a javascript: link');
    console.log('  ok  collects files and skips navigation');
  }

  // ---- duplicates ----------------------------------------------------------
  {
    const { win, state } = makeEnv();
    await grab(state, 'all');
    const exes = names(win).filter((n) => n === 'setup.exe');
    assert.strictEqual(exes.length, 1, 'same URL listed twice');
    console.log('  ok  the same URL appears once');
  }

  // ---- magnets survive URL resolution --------------------------------------
  {
    const { win, state } = makeEnv();
    await grab(state, 'all');
    const urls = rows(win).map((r) => r.querySelector('.nm').title);
    assert.ok(urls.some((u) => u.startsWith('magnet:')),
      'dropped the magnet — new URL() would mangle it');
    console.log('  ok  magnet links are kept');
  }

  // ---- images scope --------------------------------------------------------
  {
    const { win, state } = makeEnv();
    await grab(state, 'images');
    const got = names(win);
    assert.ok(got.includes('photo.jpg'), 'missed a plain <img>');
    assert.ok(got.includes('full.png'),
      'missed the full-size image behind a thumbnail');
    assert.ok(!got.includes('setup.exe'), 'images scope returned non-images');
    console.log('  ok  images scope finds img src and thumbnail targets');
  }

  // ---- selection scope -----------------------------------------------------
  {
    const { win, state } = makeEnv();
    const div = win.document.getElementById('sel');
    const range = win.document.createRange();
    range.selectNodeContents(div);
    const sel = win.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    await grab(state, 'selection');
    const got = names(win);
    assert.deepStrictEqual(got, ['inside.zip'],
      'selection scope should only see links inside the selection');
    console.log('  ok  selection scope is limited to the selection');
  }

  // ---- nothing to show -----------------------------------------------------
  {
    const { win, state } = makeEnv();
    const sel = win.getSelection();
    sel.removeAllRanges();
    await grab(state, 'selection');
    assert.strictEqual(host(win), null, 'opened an empty panel');
    console.log('  ok  an empty result shows no panel');
  }

  // ---- sending -------------------------------------------------------------
  {
    const { win, state } = makeEnv();
    await grab(state, 'all');
    // untick everything, then pick just the two videos/audio
    panel(win).getElementById('none').click();
    const boxes = [...panel(win).querySelectorAll('.row input')];
    boxes[0].checked = true;
    boxes[0].dispatchEvent(new win.Event('change', { bubbles: true }));
    panel(win).getElementById('send').click();
    await wait(20);
    assert.strictEqual(state.sent.length, 1,
      `sent ${state.sent.length} instead of the 1 that was ticked`);
    assert.strictEqual(host(win), null, 'panel stayed open after sending');
    console.log('  ok  sends only the ticked rows, then closes');
  }

  // ---- a batch must never navigate the page away ---------------------------
  {
    const { win, state } = makeEnv();
    state.appOnline = false;                 // worker will hold them instead
    const before = win.location.href;
    await grab(state, 'all');
    panel(win).getElementById('send').click();
    await wait(30);
    assert.strictEqual(win.location.href, before,
      'navigated away mid-batch — the offline fallback must not fire here');
    console.log('  ok  offline batch does not navigate the page');
  }

  // ---- escape closes it ----------------------------------------------------
  {
    const { win, state } = makeEnv();
    await grab(state, 'all');
    assert.ok(host(win), 'panel did not open');
    const ev = new win.Event('keydown', { bubbles: true });
    ev.key = 'Escape';
    win.document.dispatchEvent(ev);
    await wait(10);
    assert.strictEqual(host(win), null, 'Escape did not close the panel');
    console.log('  ok  Escape closes the panel');
  }

  console.log('grab-links: all passed');
  // content.js installs setInterval timers on the jsdom window which keep the
  // node event loop alive; exit explicitly once assertions are done.
  process.exit(process.exitCode || 0);
})().catch((e) => { console.error(e); process.exit(1); });
