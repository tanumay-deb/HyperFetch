/**
 * Unit tests for background.js parseHlsVariants — the HLS master parser that
 * feeds the quality-variant panel. Loads background.js inside a vm context with
 * a stubbed `chrome` API and reads the (globally-declared) parser back out.
 *
 *   cd chrome_ext/test && npm test
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const BG = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');

function loadBackground() {
  const noop = () => {};
  const sandbox = {
    chrome: {
      storage: {
        local: { get: (d, cb) => cb && cb(typeof d === 'object' ? d : {}) },
        onChanged: { addListener: noop },
      },
      runtime: { onInstalled: { addListener: noop }, onMessage: { addListener: noop }, lastError: null },
      contextMenus: { create: noop, onClicked: { addListener: noop } },
      webRequest: { onResponseStarted: { addListener: noop } },
      tabs: { sendMessage: noop },
      cookies: { getAll: noop },
    },
    URL,
    fetch: () => Promise.reject(new Error('no network in test')),
    navigator: { userAgent: 'test' },
    console,
  };
  vm.runInContext(BG, vm.createContext(sandbox), { filename: 'background.js' });
  return sandbox;
}

const bg = loadBackground();

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log('  ok  ' + name); }
  catch (e) { console.error('FAIL  ' + name + '\n      ' + (e.message || e)); process.exitCode = 1; }
}

test('reads peak BANDWIDTH even when AVERAGE-BANDWIDTH is listed first', () => {
  const master = [
    '#EXTM3U',
    '#EXT-X-STREAM-INF:AVERAGE-BANDWIDTH=2000000,BANDWIDTH=2500000,RESOLUTION=1920x1080',
    '1080/index.m3u8',
    '#EXT-X-STREAM-INF:AVERAGE-BANDWIDTH=800000,BANDWIDTH=1000000,RESOLUTION=854x480',
    '480/index.m3u8',
  ].join('\n');
  const v = bg.parseHlsVariants(master, 'https://cdn.x/hls/master.m3u8');
  assert.strictEqual(v.length, 2, 'two variants');
  assert.strictEqual(v[0].height, 1080);
  assert.strictEqual(v[0].bandwidth, 2500000, 'peak BANDWIDTH, not AVERAGE');
  assert.strictEqual(v[0].url, 'https://cdn.x/hls/1080/index.m3u8', 'relative URI resolved against master');
  assert.strictEqual(v[1].bandwidth, 1000000);
});

test('sorts highest-quality first regardless of source order', () => {
  const lowFirst = [
    '#EXTM3U',
    '#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360',
    'low.m3u8',
    '#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080',
    'high.m3u8',
  ].join('\n');
  const v = bg.parseHlsVariants(lowFirst, 'https://cdn.x/m.m3u8');
  assert.strictEqual(v[0].height, 1080, 'best (1080p) first');
  assert.strictEqual(v[1].height, 360);
});

test('returns [] for a media playlist (no #EXT-X-STREAM-INF)', () => {
  const media = '#EXTM3U\n#EXTINF:9.0,\nseg0.ts\n#EXTINF:9.0,\nseg1.ts\n#EXT-X-ENDLIST';
  assert.strictEqual(bg.parseHlsVariants(media, 'https://cdn.x/v.m3u8').length, 0);
});

test('hlsDuration sums #EXTINF segment durations', () => {
  const media = '#EXTM3U\n#EXTINF:9.5,\na.ts\n#EXTINF:10.0,\nb.ts\n#EXTINF:0.5,\nc.ts';
  assert.strictEqual(bg.hlsDuration(media), 20);
});

console.log(`\n${passed} passed` + (process.exitCode ? ' (with failures)' : ''));
process.exit(process.exitCode || 0);
