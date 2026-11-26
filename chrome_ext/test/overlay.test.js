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
      id: 'hyperfetch-test',
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

  // 2026-11-26: the full "Download" button covered most of a thumbnail. A box
  // under 360x200 gets a round arrow of 24-30 px, closer to its corner.
  await test('a thumbnail gets the arrow alone, a player the full button', async () => {
    const { win } = makeEnv({ openShadow: true });
    const thumb = mkVideo(win, { src: 'https://cdn.x/t.mp4', w: 200, h: 112, top: 100, left: 100 });
    const player = mkVideo(win, { src: 'https://cdn.x/p.mp4', w: 640, h: 360, top: 300, left: 100 });
    win.document.body.append(thumb, player);
    await wait(300);
    const [bt, bp] = badges(win);
    const box = (b) => b.shadowRoot.querySelector('.badge-container');
    assert.ok(box(bt).classList.contains('small'), 'the thumbnail has the full button');
    assert.strictEqual(box(bt).style.getPropertyValue('--side'), '24px');
    assert.strictEqual(parseInt(bt.style.left, 10), 300 - 24 - 6, 'not in the corner');
    assert.strictEqual(parseInt(bt.style.top, 10), 106);
    assert.ok(!box(bp).classList.contains('small'), 'the player lost its full button');
    // the same element made bigger (a preview opening up) gets the full one
    thumb.getBoundingClientRect = () => ({ top: 100, left: 100, right: 740, bottom: 460, width: 640, height: 360 });
    win.dispatchEvent(new win.Event('scroll'));
    await wait(50);
    assert.ok(!box(bt).classList.contains('small'), 'it kept the small one');
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

  // Vimeo's player (2026-11-13): its hidden picture-in-picture notice has
  // "title" in its class, and the video was saved as "Playing in
  // picture-in-picture". checkVisibility() said false for it; jsdom has none.
  await test('a caption the player hides does not name the video', async () => {
    for (const [seen, want] of [[false, 'The New Player.mp4'], [true, 'Playing in picture-in-picture.mp4']]) {
      const { win, state } = makeEnv({ openShadow: true });
      win.document.title = 'The New Player';
      const box = win.document.createElement('div');
      const note = win.document.createElement('div');
      note.className = 'PipOverlay_module_title__82a6be37';
      note.textContent = 'Playing in picture-in-picture';
      note.checkVisibility = () => seen;
      const v = mkVideo(win, { src: 'https://cdn.x/v.mp4' });
      box.append(note, v);
      win.document.body.appendChild(box);
      await wait(300);
      badges(win)[0].shadowRoot.querySelector('button')
        .dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
      assert.strictEqual(state.sent.filename, want, 'seen: ' + seen);
    }
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

  await test('HLS master: blob-video badge grabs the best (first) variant', async () => {
    const { win, state } = makeEnv({ openShadow: true });
    win.document.title = 'My Show';
    const v = mkVideo(win, { src: 'blob:https://example.com/x', w: 800, h: 450 });
    win.document.body.appendChild(v);
    await wait(200);
    assert.strictEqual(badges(win).length, 0, 'no badge before a stream is known');
    state.msgListener({
      type: 'SNIFFED_HLS_MASTER',
      url: 'https://cdn.x/master.m3u8',
      filename: 'master.m3u8',
      variants: [
        { label: '1080p', height: 1080, bandwidth: 5000000, url: 'https://cdn.x/1080/idx.m3u8', size: 0 },
        { label: '480p', height: 480, bandwidth: 1000000, url: 'https://cdn.x/480/idx.m3u8', size: 0 },
      ],
    });
    await wait(300);
    assert.strictEqual(badges(win).length, 1, 'badge appears once the master is parsed');
    const btn = badges(win)[0].shadowRoot.querySelector('button');
    btn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    assert.strictEqual(state.sent.url, 'https://cdn.x/1080/idx.m3u8', 'sends the top variant');
    // with the master it came from: there the app reads whether the sound is a
    // playlist of its own, which the variant alone does not say (2026-11-03)
    assert.strictEqual(state.sent.hlsMaster, 'https://cdn.x/master.m3u8');
    const items = badges(win)[0].shadowRoot.querySelectorAll('.menu-item');
    items[1].dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    assert.strictEqual(state.sent.url, 'https://cdn.x/480/idx.m3u8');
    assert.strictEqual(state.sent.hlsMaster, 'https://cdn.x/master.m3u8',
      'a quality picked from the menu lost its master');
  });

  await test('clicking a plain file link is NOT auto-captured (manual only)', async () => {
    const { win, state } = makeEnv();
    // suppress jsdom navigation on the anchor; we only care that content.js
    // does not hijack the click.
    win.document.addEventListener('click', (e) => e.preventDefault(), true);
    const a = win.document.createElement('a');
    a.href = 'https://cdn.x/already-downloaded.zip';
    a.textContent = 'download';
    win.document.body.appendChild(a);
    a.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
    await wait(50);
    assert.strictEqual(state.sent, null, 'link click must not send to the app');
  });

  // The quality menu hangs 8px below the button, and leaving the badge closes
  // it. With the gap as the menu's own margin, it belonged to the page, and the
  // menu closed on the way down to its options before any could be picked. The
  // gap is the wrapper's padding now, so it is part of the badge. jsdom has no
  // layout, so this pins the structure; the moves were checked in a browser.
  await test('the quality menu cannot close on the way down to it', async () => {
    const { win } = makeEnv({ openShadow: true });
    win.document.body.appendChild(mkVideo(win, { src: 'https://cdn.x/a.mp4' }));
    await wait(300);
    const root = badges(win)[0].shadowRoot;
    const menu = root.querySelector('.menu');
    const wrap = menu.parentElement;
    assert.ok(wrap.classList.contains('menu-wrap'), 'the menu is not inside its bridge');
    assert.ok(wrap.parentElement.classList.contains('badge-container'),
      'the bridge must be part of the badge, or leaving onto it still closes the menu');
    const css = root.querySelector('style').textContent;
    const rule = (sel) => (css.match(new RegExp('\\n\\s*' + sel.replace('.', '\\.') + '\\s*\\{([^}]*)\\}')) || [])[1] || '';
    assert.ok(/top:\s*100%/.test(rule('.menu-wrap')) && /padding-top:\s*8px/.test(rule('.menu-wrap')),
      'the bridge no longer spans the gap below the button');
    assert.ok(!/margin-top/.test(rule('.menu')),
      'the menu has its own margin again - that gap belongs to the page');
    // Leaving the badge still closes it.
    const arrow = root.querySelector('.btn-arrow');
    arrow.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    assert.ok(menu.classList.contains('open'), 'the arrow did not open the menu');
    root.querySelector('.badge-container').dispatchEvent(new win.MouseEvent('mouseleave'));
    assert.ok(!menu.classList.contains('open'), 'moving off the badge no longer closes the menu');
  });

  // Since 1.9.6 the page script runs in every frame: a frame with no button
  // asked for a layout pass twice a second, ~1 s of CPU per 20 s on a page of
  // 60 frames (measured, Edge and Firefox). It asks only to move a button.
  await test('a page with no button asks for no layout pass; one with a button does', async () => {
    const { win } = makeEnv();
    let frames = 0;
    const raf = win.requestAnimationFrame;
    win.requestAnimationFrame = (cb) => { frames++; return raf(cb); };
    win.document.body.appendChild(win.document.createElement('div'));   // the page changes
    win.dispatchEvent(new win.Event('scroll'));
    win.dispatchEvent(new win.Event('resize'));
    await wait(1100);                                                    // two scans
    assert.strictEqual(frames, 0, 'nothing to place, yet it asked ' + frames + ' times');
    const v = mkVideo(win, { src: 'https://cdn.x/movie.mp4', top: 100, left: 100 });
    win.document.body.appendChild(v);
    await wait(300);
    assert.ok(frames > 0, 'a new button is placed');
    assert.strictEqual(badges(win)[0].style.display, 'block');
    const placed = frames;
    win.dispatchEvent(new win.Event('scroll'));
    await wait(50);
    assert.ok(frames > placed, 'and follows the page as it scrolls');
  });

  console.log(`\n${passed} passed` + (process.exitCode ? ' (with failures)' : ''));
  // content.js installs setInterval timers on the jsdom window which keep the
  // node event loop alive; exit explicitly once assertions are done.
  process.exit(process.exitCode || 0);
})();
