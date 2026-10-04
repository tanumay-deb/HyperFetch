/**
 * content.js: which sniffed stream the badge on a blob/MSE <video> sends.
 *
 * It used to be the newest stream seen in the tab, HLS first. Seen in the app's
 * log, 2026-10-03, on one video page: the first click sent the film's playlist;
 * the next two sent an ad network's LIVE stream, which had started after the
 * film (three segments, saved as "Completed" at 0.4 MB); a fourth sent the
 * "-preview.webm" a thumbnail plays on hover. Each was named after the page, so
 * each looked right until it was opened.
 *
 * The badge now sends the stream that belongs to the player: one whose length
 * is the player's, never a live stream for a video that has a length, never a
 * clip another element plays by its own address, never a stream another frame
 * asked for. Where nothing tells streams apart it is the old rule again -
 * the page's own site first, HLS before a file, then the newest.
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

function makeEnv({ url = 'https://www.tube.example/video-8xGJx1/a-film/' } = {}) {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
    { url, pretendToBeVisual: true, runScripts: 'outside-only' });
  const win = dom.window;
  win.innerWidth = 1280;
  win.innerHeight = 800;
  const real = win.Element.prototype.attachShadow;
  win.Element.prototype.attachShadow = function () { return real.call(this, { mode: 'open' }); };
  const state = { msgListener: null, sent: [] };
  win.chrome = {
    storage: {
      local: { get: (d, cb) => cb({ enabled: true, token: '' }), set: (o, cb) => cb && cb() },
      onChanged: { addListener: () => {} },
    },
    runtime: {
      id: 'hyperfetch-test',
      onMessage: { addListener: (cb) => { state.msgListener = cb; } },
      sendMessage: (m, cb) => { if (m && m.type === 'DOWNLOAD_URL') state.sent.push(m); cb && cb({ ok: true }); },
      lastError: null,
    },
  };
  win.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
  win.cancelAnimationFrame = (id) => clearTimeout(id);
  win.document.title = 'A Film - Tube';
  vm.runInContext(CONTENT, vm.createContext(win), { filename: 'content.js' });
  return { win, state };
}

// A <video>. `length` is what the player reports as its duration: NaN until it
// has loaded metadata, Infinity for a live broadcast.
function mkVideo(win, { src = 'blob:https://www.tube.example/1f0c', length = NaN,
                        w = 800, h = 450, top = 100, left = 100 } = {}) {
  const v = win.document.createElement('video');
  v.length_ = length;
  v.src_ = src;                             // a player's source can be swapped under it
  Object.defineProperty(v, 'currentSrc', { get: () => v.src_, configurable: true });
  Object.defineProperty(v, 'src', { get: () => v.src_, configurable: true });
  Object.defineProperty(v, 'duration', { get: () => v.length_, configurable: true });
  Object.defineProperty(v, 'isConnected', { get: () => !!v.parentNode, configurable: true });
  v.getBoundingClientRect = () => ({ top, left, right: left + w, bottom: top + h, width: w, height: h });
  win.document.body.appendChild(v);
  return v;
}

const FILM = 'https://dash-s22.tube.example/hls/v6/8xGJx1/master.m3u8';
const FILM_720 = 'https://dash-s22.tube.example/hls/v6/8xGJx1/index-720.m3u8';
const AD_LIVE = 'https://media-hls.adnetwork.example/b-hls-03/42508052/42508052_240p.m3u8';

// What background.js sends for the film: a parsed master, 12 min 34 s long.
const film = (extra = {}) => Object.assign({
  type: 'SNIFFED_HLS_MASTER', url: FILM, filename: 'master.m3u8', frameId: 0,
  duration: 754.2, live: false,
  variants: [{ label: '720p', height: 720, bandwidth: 2400000, url: FILM_720, size: 226000000 }],
}, extra);

// ...and for the ad: one rendition of a live stream, a few seconds on offer.
const adLive = (extra = {}) => Object.assign({
  type: 'SNIFFED_MEDIA', url: AD_LIVE, kind: 'hls', mime: 'application/x-mpegurl',
  size: 0, filename: '42508052_240p.m3u8', frameId: 0, duration: 6, live: true,
}, extra);

const badgeOf = (win) => win.document.querySelectorAll('.hyperfetch-video-badge');
function click(win, i = 0) {
  const host = badgeOf(win)[i];
  assert.ok(host, 'no badge to click');
  host.shadowRoot.querySelector('button').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
}

let passed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ok  ' + name); }
  catch (e) { console.error('FAIL  ' + name + '\n      ' + (e.message || e)); process.exitCode = 1; }
}

(async () => {
  // ---- the reported case ----------------------------------------------------
  await test("an ad's live stream that starts after the film is not sent in its place", async () => {
    const { win, state } = makeEnv();
    mkVideo(win, { length: 754.3 });
    state.msgListener(film());
    await wait(100);
    state.msgListener(adLive());           // arrives later: it used to win for that
    await wait(300);
    click(win);
    assert.strictEqual(state.sent.length, 1);
    assert.strictEqual(state.sent[0].url, FILM_720, 'the ad was sent, named after the page');
  });

  await test('with the ad the only stream seen, the film player offers nothing', async () => {
    const { win, state } = makeEnv();
    mkVideo(win, { length: 754.3 });
    state.msgListener(adLive());
    await wait(300);
    assert.strictEqual(badgeOf(win).length, 0, 'a badge that could only send the ad');
  });

  await test("another video's stream is not this player's, though it came later", async () => {
    const { win, state } = makeEnv();
    mkVideo(win, { length: 754.3 });
    state.msgListener(film());
    await wait(50);
    state.msgListener(film({
      url: 'https://dash-s22.tube.example/hls/v6/OTHER/master.m3u8', duration: 301.0,
      variants: [{ label: '720p', height: 720, bandwidth: 2400000,
                   url: 'https://dash-s22.tube.example/hls/v6/OTHER/index-720.m3u8', size: 0 }],
    }));
    await wait(300);
    click(win);
    assert.strictEqual(state.sent[0].url, FILM_720);
  });

  await test('a length that differs by a rounding is the same video', async () => {
    const { win, state } = makeEnv();
    mkVideo(win, { length: 755.9 });       // playlists round each segment up
    state.msgListener(film());
    await wait(300);
    click(win);
    assert.strictEqual(state.sent[0].url, FILM_720);
  });

  // ---- the hover preview ----------------------------------------------------
  await test("a clip a thumbnail plays by its own address is not the player's", async () => {
    const { win, state } = makeEnv();
    const PREVIEW = 'https://static-eu.tube.example/thumbs/1/16/16732863-preview.webm';
    const main = mkVideo(win, { length: 754.3 });
    const thumb = mkVideo(win, { src: PREVIEW, length: 9.0, w: 320, h: 180, top: 600 });
    // the sniffer saw the clip go by as well
    state.msgListener({ type: 'SNIFFED_MEDIA', url: PREVIEW, kind: 'file', mime: 'video/webm',
                        size: 0, filename: '16732863-preview.webm', frameId: 0 });
    await wait(50);                         // well before the next scan of the page
    assert.strictEqual(badgeOf(win).length, 1, 'only the clip has a badge: it is a file with an address');
    click(win);
    assert.strictEqual(state.sent[0].url, PREVIEW, 'the one badge is the clip\'s own');

    thumb.remove();                         // the mouse moved off the thumbnail
    await wait(700);
    assert.strictEqual(badgeOf(win).length, 0, 'the clip was offered for the film once its element was gone');

    state.msgListener(film());
    await wait(300);
    state.sent.length = 0;
    click(win);
    assert.strictEqual(state.sent[0].url, FILM_720);
    assert.ok(main.isConnected);
  });

  await test('a playlist the player itself first named as its address is still its own', async () => {
    // <video src="…m3u8"> in the page, then the page's script takes the element
    // over and plays the same playlist through a blob. Whose stream is it? Its
    // own: only ANOTHER element's address rules a stream out.
    const { win, state } = makeEnv();
    const LIST = 'https://vod.tube.example/a/playlist.m3u8';
    const v = mkVideo(win, { src: LIST });
    await wait(700);                        // a scan of the page has seen the address
    v.src_ = 'blob:https://www.tube.example/9a';
    v.length_ = 600.2;
    state.msgListener({ type: 'SNIFFED_MEDIA', url: LIST, kind: 'hls', mime: 'application/x-mpegurl',
                        size: 0, filename: 'playlist.m3u8', frameId: 0, duration: 600, live: false });
    await wait(700);
    click(win);
    assert.strictEqual(state.sent[0].url, LIST);
  });

  await test('a file too small to be a video of that length is not it', async () => {
    const { win, state } = makeEnv();
    mkVideo(win, { length: 600 });
    state.msgListener({ type: 'SNIFFED_MEDIA', url: 'https://cdn.tube.example/teaser.mp4', kind: 'file',
                        mime: 'video/mp4', size: 900000, filename: 'teaser.mp4', frameId: 0 });
    await wait(300);
    assert.strictEqual(badgeOf(win).length, 0, '0.9 MB offered as a ten-minute video');
    state.msgListener({ type: 'SNIFFED_MEDIA', url: 'https://cdn.tube.example/full.mp4', kind: 'file',
                        mime: 'video/mp4', size: 60000000, filename: 'full.mp4', frameId: 0 });
    await wait(300);
    click(win);
    assert.strictEqual(state.sent[0].url, 'https://cdn.tube.example/full.mp4');
  });

  // ---- other frames, idle players -------------------------------------------
  await test("a stream another frame asked for is not this page's player's", async () => {
    const { win, state } = makeEnv();
    mkVideo(win, {});                       // length not known yet
    state.msgListener({ type: 'SNIFFED_MEDIA', url: 'https://ads.example/promo/master.m3u8', kind: 'hls',
                        mime: 'application/x-mpegurl', size: 0, filename: 'master.m3u8', frameId: 7 });
    await wait(300);
    assert.strictEqual(badgeOf(win).length, 0, "an ad frame's stream was offered for the page's player");
    state.msgListener(film());
    await wait(300);
    click(win);
    assert.strictEqual(state.sent[0].url, FILM_720);
  });

  await test('a player with nothing attached to it is offered nothing sniffed', async () => {
    const { win, state } = makeEnv();
    mkVideo(win, { src: '' });              // a poster, not yet played
    state.msgListener(film());
    await wait(300);
    assert.strictEqual(badgeOf(win).length, 0);
  });

  await test('a live player may have a live stream', async () => {
    const { win, state } = makeEnv({ url: 'https://live.example/channel/9' });
    mkVideo(win, { src: 'blob:https://live.example/77', length: Infinity });
    state.msgListener({ type: 'SNIFFED_MEDIA', url: 'https://edge.live.example/9/chunklist.m3u8', kind: 'hls',
                        mime: 'application/x-mpegurl', size: 0, filename: 'chunklist.m3u8',
                        frameId: 0, duration: 12, live: true });
    await wait(300);
    click(win);
    assert.strictEqual(state.sent[0].url, 'https://edge.live.example/9/chunklist.m3u8');
  });

  // ---- nothing known: the old rule, the page's own site first ----------------
  await test("with nothing to tell them apart, the page's own site comes before another's", async () => {
    const { win, state } = makeEnv();
    mkVideo(win, {});
    state.msgListener({ type: 'SNIFFED_MEDIA', url: 'https://vod.tube.example/a/master.m3u8', kind: 'hls',
                        mime: 'application/x-mpegurl', size: 0, filename: 'master.m3u8' });
    await wait(50);
    state.msgListener({ type: 'SNIFFED_MEDIA', url: 'https://cdn.elsewhere.example/b/master.m3u8', kind: 'hls',
                        mime: 'application/x-mpegurl', size: 0, filename: 'master.m3u8' });
    await wait(300);
    click(win);
    assert.strictEqual(state.sent[0].url, 'https://vod.tube.example/a/master.m3u8');
  });

  await test('among equals it is HLS before a file, then the newest, as before', async () => {
    const { win, state } = makeEnv();
    mkVideo(win, {});
    const say = (url, kind) => state.msgListener({
      type: 'SNIFFED_MEDIA', url, kind, size: 0, filename: url.split('/').pop(),
      mime: kind === 'hls' ? 'application/x-mpegurl' : 'video/mp4' });
    say('https://cdn.elsewhere.example/1/master.m3u8', 'hls');
    say('https://cdn.elsewhere.example/2/master.m3u8', 'hls');
    say('https://cdn.elsewhere.example/3/clip.mp4', 'file');
    await wait(300);
    click(win);
    assert.strictEqual(state.sent[0].url, 'https://cdn.elsewhere.example/2/master.m3u8');
  });

  // ---- an advert in the film's own player -------------------------------------
  // Seen in a real browser on that page, 2026-10-04: the player attaches the film
  // (a blob), and three seconds later a pre-roll advert takes the SAME element
  // over with a file of its own, from the ad network's host. Afterwards the film
  // comes back. A badge that reads the element's address sends the advert.
  const AD_FILE = 'https://tsvideo.adnetwork.example/2/1/3/f9c0/850x480.mp4';

  await test("an advert that takes the player over is not sent in the film's place", async () => {
    const { win, state } = makeEnv();
    const v = mkVideo(win, {});             // the film is attached; no length yet
    state.msgListener(film());
    await wait(700);                        // the badge has been drawn for the film
    v.src_ = AD_FILE;                       // the pre-roll starts
    v.length_ = 20;
    await wait(700);
    click(win);
    assert.strictEqual(state.sent.length, 1);
    assert.strictEqual(state.sent[0].url, FILM_720, 'the advert was sent, named after the page');

    v.src_ = 'blob:https://www.tube.example/1f0c';   // and the film comes back
    v.length_ = 754.3;
    await wait(700);
    state.sent.length = 0;
    click(win);
    assert.strictEqual(state.sent[0].url, FILM_720);
  });

  await test("the page's own file taking the player over is the video", async () => {
    const { win, state } = makeEnv();
    const v = mkVideo(win, {});
    state.msgListener(film());
    await wait(700);
    const OWN = 'https://vid-s3.tube.example/v5/8xGJx1/720.mp4';   // the site's own host
    v.src_ = OWN;
    v.length_ = 754.3;
    await wait(700);
    click(win);
    assert.strictEqual(state.sent[0].url, OWN);
  });

  await test('what a player was on is forgotten on the next page', async () => {
    const { win, state } = makeEnv();
    const v = mkVideo(win, {});
    state.msgListener(film());
    await wait(700);
    win.history.pushState({}, '', '/video-OTHER/another-film/');   // the site moves on
    const NEXT_FILE = 'https://media.elsewhere.example/films/other-720.mp4';
    v.src_ = NEXT_FILE;
    v.length_ = 301;
    await wait(700);
    click(win);
    assert.strictEqual(state.sent[0].url, NEXT_FILE);
  });

  // ---- the click sends what the player holds now ------------------------------
  await test('the click sends what the player is playing at the click', async () => {
    const { win, state } = makeEnv();
    const v = mkVideo(win, { length: 754.3 });
    const NEXT = 'https://dash-s22.tube.example/hls/v6/NEXT/index-720.m3u8';
    state.msgListener(film({               // the page fetched the next one early
      url: 'https://dash-s22.tube.example/hls/v6/NEXT/master.m3u8', duration: 301.0,
      variants: [{ label: '720p', height: 720, bandwidth: 2400000, url: NEXT, size: 0 }],
    }));
    state.msgListener(film());
    await wait(300);
    v.length_ = 301.2;                      // the page loaded its next video into the player
    click(win);                             // before any timer has looked again
    assert.strictEqual(state.sent[0].url, NEXT);
  });

  console.log(`\n${passed} passed` + (process.exitCode ? ' (with failures)' : ''));
  // content.js installs setInterval timers on the jsdom window which keep the
  // node event loop alive; exit explicitly once assertions are done.
  process.exit(process.exitCode || 0);
})();
