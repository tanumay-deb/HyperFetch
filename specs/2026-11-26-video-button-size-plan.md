# Video download button: size and hide-all, implementation plan

**Goal:** a small video box gets a small round ⬇ button, and the panel's hide
drop hides every video button in the tab, frames included.
**Spec:** specs/2026-11-26-video-button-size-design.md

## Global constraints

- Edit `chrome_ext/`, then copy `content.js` and `background.js` byte for byte
  to `edge_ext/` and `firefox_ext/` (`tests/test_extension_sync.py`).
- Extension version 1.9.8 in all three `manifest.json` files.
- The full button is unchanged for a box of at least 360 x 200 px.

## Review focus

1. A box that grows past 360 x 200, or shrinks below it, switches size on the
   next placement, with no reload.
2. A frame whose question about hiding gets no answer (worker restarting, page
   gone) still shows its button.
3. The top frame never asks itself (`IN_FRAME` false: the state is already known).
4. The quality arrow is shown inline by `updateState`, and a small button still
   hides it.
5. A drop hides the buttons at once in the top frame, even when the worker
   cannot be reached.

### Task 1: size by the box (`content.js` `createOverlay`, `positionOverlay`)

- [ ] Test (`test/overlay.test.js`), "a thumbnail gets the arrow alone, a
  player the full button": a 200 x 112 video at (100, 100) has class `small`
  on `.badge-container`, `--side` 24px, left 270, top 106. A 640 x 360 video
  has no `small` class. Resizing the first one to 640 x 360 drops the class on
  the next scroll.
- [ ] Code: `FULL_W = 360`, `FULL_H = 200`. `createOverlay` returns
  `fit(w, h) -> {small, side}`, which sets the class and the `--side` variable
  only when they change. CSS `.small`: label and arrow hidden (`!important`
  beats the inline `display` from `updateState`), the button `var(--side)`
  square. `positionOverlay` uses a gap of 6 px when small, and falls back to
  `side` for the width and height when there is no layout.

### Task 2: hide all (`content.js`, `background.js`)

- [ ] Test (`test/panel-dismiss.test.js`): change "per-video badges are
  untouched" to "a panel dismissal hides the video buttons too". After the
  drop, 0 badges, the runtime got `{type: 'HYPERFETCH_HIDE_BADGES'}`, and a
  new video adds none.
- [ ] Test (`test/pick-stream.test.js`, `inFrame`): a frame told
  `HYPERFETCH_HIDE_BADGES` drops its button. A frame whose page answers
  `{hidden: true}` shows none. One answering nothing shows its button.
- [ ] Test (`test/sniff-facts.test.js`, the worker): `HYPERFETCH_HIDE_BADGES`
  from tab 4 goes to tab 4 with no frameId. `HYPERFETCH_BADGES_HIDDEN` goes to
  frame 0, and its answer comes back as `{hidden}`.
- [ ] Code: `hideBadges()` removes the hosts, clears `videoOverlays` and sets
  `badgeHidden`. `syncOverlays` returns while hidden. In a frame,
  `hiddenKnown` stays false until `askHidden()` is answered. The message
  listener handles both types. The worker relays them.

### Task 3: copies, version, suite

- [ ] Copy the two files to both browsers and bump all three manifests to
  1.9.8. Run `npm test` in `chrome_ext/test` and the full pytest twice, then
  commit and push.
