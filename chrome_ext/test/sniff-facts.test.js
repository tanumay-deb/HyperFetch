/**
 * background.js: what the media sniffer tells the page about each stream.
 *
 * The page's badge has to tell the player's own stream from an ad's live
 * stream or another video's (see pick-stream.test.js). The page cannot read a
 * playlist itself, so the worker says what it learnt: the frame that asked for
 * the stream, how long it is, and whether it is a live stream's sliding
 * window - from the app's /probe when the app answers, else from its own fetch.
 *
 *   cd chrome_ext/test && npm test
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const BG = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');

// files: { url: playlist text } the worker's own fetch can read.
// probeImpl: the app's /probe; the default is an app that is not running.
function load({ files = {}, probeImpl } = {}) {
  const noop = () => {};
  const state = { sent: [], onResponse: null, fetched: [] };
  const probe = probeImpl || (() => Promise.resolve({ ok: false }));
  const sandbox = {
    chrome: {
      storage: {
        local: { get: (d, cb) => cb && cb(typeof d === 'object' ? d : {}) },
        onChanged: { addListener: noop },
      },
      runtime: { onInstalled: { addListener: noop }, onMessage: { addListener: noop }, lastError: null },
      contextMenus: { create: noop, removeAll: (cb) => cb && cb(), onClicked: { addListener: noop } },
      webRequest: { onResponseStarted: { addListener: (cb) => { state.onResponse = cb; } } },
      tabs: { sendMessage: (tabId, msg) => { state.sent.push({ tabId, msg }); } },
      cookies: { getAll: (q, cb) => cb([]) },
    },
    URL,
    fetch: (u, opts) => {
      if (/^http:\/\/127\.0\.0\.1:(21456|5000)\/ping$/.test(u)) {
        return Promise.reject(new Error('app offline'));
      }
      if (/\/probe$/.test(u)) return probe(u, opts);
      state.fetched.push(u);
      return u in files
        ? Promise.resolve({ text: () => Promise.resolve(files[u]) })
        : Promise.reject(new Error('unreadable'));
    },
    navigator: { userAgent: 'test' },
    console,
  };
  vm.runInContext(BG, vm.createContext(sandbox), { filename: 'background.js' });
  return { sb: sandbox, state };
}

const settle = async () => { for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r)); };

// A response as webRequest reports it.
const response = (url, { tabId = 4, frameId = 0, type = 'application/vnd.apple.mpegurl', length } = {}) => ({
  url, tabId, frameId,
  responseHeaders: [{ name: 'Content-Type', value: type }]
    .concat(length ? [{ name: 'Content-Length', value: String(length) }] : []),
});

// An ad network's live rendition: a window of three segments, 9131 dropped so far.
const LIVE = ['#EXTM3U', '#EXT-X-VERSION:3', '#EXT-X-TARGETDURATION:2', '#EXT-X-MEDIA-SEQUENCE:9131',
  '#EXTINF:2.000,', 'a9131.ts', '#EXTINF:2.000,', 'a9132.ts', '#EXTINF:2.000,', 'a9133.ts', ''].join('\n');
// A film's rendition: three segments and an end.
const VOD = ['#EXTM3U', '#EXT-X-TARGETDURATION:10', '#EXT-X-MEDIA-SEQUENCE:0',
  '#EXTINF:10.0,', 's0.ts', '#EXTINF:10.0,', 's1.ts', '#EXTINF:4.5,', 's2.ts', '#EXT-X-ENDLIST', ''].join('\n');
const MASTER = ['#EXTM3U',
  '#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720', '720/index.m3u8',
  '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480', '480/index.m3u8', ''].join('\n');

let passed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ok  ' + name); }
  catch (e) { console.error('FAIL  ' + name + '\n      ' + (e.message || e)); process.exitCode = 1; }
}

(async () => {
  await test('a media file is reported with the frame that asked for it', async () => {
    const { state } = load();
    state.onResponse(response('https://cdn.x/clip.mp4', { frameId: 3, type: 'video/mp4', length: 5000000 }));
    await settle();
    assert.strictEqual(state.sent.length, 1);
    assert.strictEqual(state.sent[0].msg.type, 'SNIFFED_MEDIA');
    assert.strictEqual(state.sent[0].msg.frameId, 3);
  });

  await test("a live stream's playlist is reported as live", async () => {
    const url = 'https://media-hls.adnetwork.example/b-hls-03/42508052/42508052_240p.m3u8';
    const { state } = load({ files: { [url]: LIVE } });
    state.onResponse(response(url, { frameId: 0 }));
    await settle();
    const m = state.sent[0].msg;
    assert.strictEqual(m.type, 'SNIFFED_MEDIA');
    assert.strictEqual(m.live, true, 'no end tag, no type, 9131 segments already dropped');
    assert.strictEqual(m.duration, 6);
    assert.strictEqual(m.frameId, 0);
  });

  await test('a finished playlist is reported with its length, and not as live', async () => {
    const url = 'https://cdn.x/film/index.m3u8';
    const { state } = load({ files: { [url]: VOD } });
    state.onResponse(response(url));
    await settle();
    const m = state.sent[0].msg;
    assert.strictEqual(m.live, false);
    assert.strictEqual(m.duration, 24.5);
  });

  for (const [what, body] of [
    ['numbered from 0 with no end tag (a sloppy server)', VOD.replace('#EXT-X-ENDLIST\n', '')],
    ['marked EVENT, which keeps everything so far',
      LIVE.replace('#EXT-X-MEDIA-SEQUENCE:9131', '#EXT-X-PLAYLIST-TYPE:EVENT\n#EXT-X-MEDIA-SEQUENCE:9131')],
  ]) {
    await test('not live: a playlist ' + what, async () => {
      const url = 'https://cdn.x/v/index.m3u8';
      const { state } = load({ files: { [url]: body } });
      state.onResponse(response(url));
      await settle();
      assert.strictEqual(state.sent[0].msg.live, false);
    });
  }

  await test('a master is reported with the length of its best rendition', async () => {
    const url = 'https://cdn.x/film/master.m3u8';
    const { state } = load({ files: { [url]: MASTER, 'https://cdn.x/film/720/index.m3u8': VOD } });
    state.onResponse(response(url, { frameId: 0 }));
    await settle();
    const m = state.sent[0].msg;
    assert.strictEqual(m.type, 'SNIFFED_HLS_MASTER');
    assert.strictEqual(m.duration, 24.5);
    assert.strictEqual(m.live, false);
    assert.strictEqual(m.frameId, 0);
    assert.strictEqual(m.variants.length, 2);
  });

  await test("the app's answer is passed on: length and live", async () => {
    const url = 'https://gated.x/live/chunklist.m3u8';
    const probeImpl = () => Promise.resolve({
      ok: true, json: () => Promise.resolve({ variants: [], duration: 6.0, live: true }),
    });
    const { state } = load({ probeImpl });
    state.onResponse(response(url, { frameId: 2 }));
    await settle();
    const m = state.sent[0].msg;
    assert.strictEqual(m.live, true);
    assert.strictEqual(m.duration, 6);
    assert.strictEqual(m.frameId, 2);
    assert.deepStrictEqual(state.fetched, [], 'the app answered; the worker must not fetch as well');
  });

  await test('an app from before it said so leaves both unknown', async () => {
    const url = 'https://gated.x/film/master.m3u8';
    const probeImpl = () => Promise.resolve({
      ok: true, json: () => Promise.resolve({ variants: [
        { label: '720p', height: 720, bandwidth: 2400000, url: 'https://gated.x/film/720.m3u8', size: 1 },
      ] }),
    });
    const { state } = load({ probeImpl });
    state.onResponse(response(url));
    await settle();
    const m = state.sent[0].msg;
    assert.strictEqual(m.type, 'SNIFFED_HLS_MASTER');
    assert.strictEqual(m.duration, 0);
    assert.strictEqual(m.live, false);
  });

  await test('a playlist already parsed goes to the next page with that request\'s frame', async () => {
    const url = 'https://cdn.x/film/master.m3u8';
    const { state } = load({ files: { [url]: MASTER, 'https://cdn.x/film/720/index.m3u8': VOD } });
    state.onResponse(response(url, { tabId: 4, frameId: 0 }));
    await settle();
    state.onResponse(response(url, { tabId: 9, frameId: 5 }));
    await settle();
    assert.deepStrictEqual(state.sent.map((s) => [s.tabId, s.msg.frameId]), [[4, 0], [9, 5]]);
    assert.strictEqual(state.fetched.length, 2, 'parsed twice');
  });

  console.log(`\n${passed} passed` + (process.exitCode ? ' (with failures)' : ''));
  process.exit(process.exitCode || 0);
})();
