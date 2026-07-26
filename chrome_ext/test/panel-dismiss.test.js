/**
 * Behavior tests for the floating media panel's drag-to-dismiss target.
 * Drives content.js inside jsdom: sniffs a media URL to build the corner panel,
 * then simulates pointer drags to assert the drop zone only appears during a
 * drag, arms over the target, hides the panel for the page session when
 * released on it, and still snaps to a corner on a normal drag.
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

function makeEnv() {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
    { url: 'https://example.com/watch', pretendToBeVisual: true, runScripts: 'outside-only' });
  const win = dom.window;
  win.innerWidth = 1280;
  win.innerHeight = 800;
  // the panel uses a CLOSED shadow root in production; force open so the test
  // can inspect it (behavior under test is identical either way)
  const real = win.Element.prototype.attachShadow;
  win.Element.prototype.attachShadow = function () { return real.call(this, { mode: 'open' }); };

  const state = { msgListener: null, stored: {} };
  win.chrome = {
    storage: {
      local: {
        get: (d, cb) => cb({ enabled: true, badgeCorner: 'top-right', token: '' }),
        set: (o, cb) => { Object.assign(state.stored, o); cb && cb(); },
      },
      onChanged: { addListener: () => {} },
    },
    runtime: {
      onMessage: { addListener: (cb) => { state.msgListener = cb; } },
      sendMessage: (m, cb) => cb && cb({ ok: true }),
      lastError: null,
    },
  };
  win.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
  win.cancelAnimationFrame = (id) => clearTimeout(id);
  vm.runInContext(CONTENT, vm.createContext(win), { filename: 'content.js' });
  return { win, state };
}

const panelHost = (win) => win.document.getElementById('hyperfetch-media-sniffer-root');
const shadow = (win) => { const h = panelHost(win); return h && h.shadowRoot; };

// jsdom has no layout: give the drop zone a real rect at the bottom centre
// (left:50% + translateX(-50%), bottom:26px in CSS) so hit-testing can run.
function stubDropZoneRect(win) {
  const dz = shadow(win).getElementById('dropzone');
  dz.getBoundingClientRect = () => ({
    left: 570, right: 710, top: 700, bottom: 764, width: 140, height: 64,
  });
  return dz;
}

function pointer(win, type, x, y) {
  const ev = new win.Event(type, { bubbles: true });
  ev.clientX = x; ev.clientY = y; ev.button = 0;
  return ev;
}

async function buildPanel(win, state) {
  state.msgListener({
    type: 'SNIFFED_MEDIA', url: 'https://cdn.x/clip.mp4', filename: 'clip.mp4', size: 1024,
  });
  await wait(50);
}

// drag the toggle from its start point through `path`, releasing at the last point
function drag(win, toggle, path) {
  toggle.dispatchEvent(pointer(win, 'pointerdown', path[0][0], path[0][1]));
  for (let i = 1; i < path.length; i++) {
    win.dispatchEvent(pointer(win, 'pointermove', path[i][0], path[i][1]));
  }
  const last = path[path.length - 1];
  win.dispatchEvent(pointer(win, 'pointerup', last[0], last[1]));
}

let passed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ok  ' + name); }
  catch (e) { console.error('FAIL  ' + name + '\n      ' + (e.message || e)); process.exitCode = 1; }
}

(async () => {
  await test('drop zone is hidden until a drag actually starts', async () => {
    const { win, state } = makeEnv();
    await buildPanel(win, state);
    const dz = shadow(win).getElementById('dropzone');
    assert.ok(dz, 'drop zone exists in the shadow root');
    assert.ok(!dz.classList.contains('show'), 'hidden at rest');

    // a click (no movement past the 6px threshold) must not reveal it
    const toggle = shadow(win).getElementById('toggle');
    toggle.dispatchEvent(pointer(win, 'pointerdown', 1200, 30));
    win.dispatchEvent(pointer(win, 'pointermove', 1202, 31));
    win.dispatchEvent(pointer(win, 'pointerup', 1202, 31));
    assert.ok(!dz.classList.contains('show'), 'still hidden after a click');
  });

  await test('dragging reveals the drop zone and arms it over the target', async () => {
    const { win, state } = makeEnv();
    await buildPanel(win, state);
    const dz = stubDropZoneRect(win);
    const toggle = shadow(win).getElementById('toggle');

    toggle.dispatchEvent(pointer(win, 'pointerdown', 1200, 30));
    win.dispatchEvent(pointer(win, 'pointermove', 1100, 200));
    assert.ok(dz.classList.contains('show'), 'revealed once dragging');
    assert.ok(!dz.classList.contains('armed'), 'not armed away from the target');

    win.dispatchEvent(pointer(win, 'pointermove', 640, 730));   // over the zone
    assert.ok(dz.classList.contains('armed'), 'armed over the target');

    win.dispatchEvent(pointer(win, 'pointermove', 200, 200));   // back off it
    assert.ok(!dz.classList.contains('armed'), 'disarms when leaving the target');
    win.dispatchEvent(pointer(win, 'pointerup', 200, 200));
    assert.ok(!dz.classList.contains('show'), 'hidden again after release');
  });

  await test('release on the target hides the panel for this page view', async () => {
    const { win, state } = makeEnv();
    await buildPanel(win, state);
    stubDropZoneRect(win);
    const toggle = shadow(win).getElementById('toggle');

    drag(win, toggle, [[1200, 30], [900, 400], [640, 730]]);
    assert.strictEqual(panelHost(win), null, 'panel removed from the page');

    // dismissal is page-session only: nothing persisted, and in particular the
    // corner is NOT rewritten to a bottom one by the dismissing drag
    assert.ok(!('badgeHidden' in state.stored), 'hide state not persisted');
    assert.ok(!('badgeCorner' in state.stored), 'corner not persisted on dismiss');

    // further sniffed media must not resurrect it
    state.msgListener({
      type: 'SNIFFED_MEDIA', url: 'https://cdn.x/another.mp4', filename: 'another.mp4', size: 2048,
    });
    await wait(50);
    assert.strictEqual(panelHost(win), null, 'stays hidden for new media');
  });

  await test('a normal drag still snaps to a corner and keeps the panel', async () => {
    const { win, state } = makeEnv();
    await buildPanel(win, state);
    stubDropZoneRect(win);
    const toggle = shadow(win).getElementById('toggle');

    drag(win, toggle, [[1200, 30], [600, 200], [80, 120]]);   // top-left, clear of the zone
    assert.ok(panelHost(win), 'panel still present');
    assert.strictEqual(state.stored.badgeCorner, 'top-left', 'snapped + persisted');
  });

  await test('per-video badges are untouched by a panel dismissal', async () => {
    const { win, state } = makeEnv();
    const v = win.document.createElement('video');
    Object.defineProperty(v, 'currentSrc', { value: 'https://cdn.x/movie.mp4', configurable: true });
    Object.defineProperty(v, 'src', { value: 'https://cdn.x/movie.mp4', configurable: true });
    Object.defineProperty(v, 'isConnected', { get: () => !!v.parentNode, configurable: true });
    v.getBoundingClientRect = () => ({ top: 100, left: 100, right: 740, bottom: 460, width: 640, height: 360 });
    win.document.body.appendChild(v);
    await buildPanel(win, state);
    await wait(300);
    stubDropZoneRect(win);

    drag(win, shadow(win).getElementById('toggle'), [[1200, 30], [900, 400], [640, 730]]);
    assert.strictEqual(panelHost(win), null, 'panel dismissed');
    assert.strictEqual(win.document.querySelectorAll('.hyperfetch-video-badge').length, 1,
      'video badge survives');
  });

  console.log(`\n${passed} passed` + (process.exitCode ? ' (with failures)' : ''));
  // content.js installs setInterval timers on the jsdom window which keep the
  // node event loop alive; exit explicitly once assertions are done.
  process.exit(process.exitCode || 0);
})();
