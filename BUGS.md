# Known bugs

The one list of open bugs in HyperFetch (the app, the browser extension, the
build). Started 2026-11-16. Anyone may fix an entry - a person, Claude, Gemini.

## How this list works

- Every entry has an ID (`B1`, `B2`, ...) that never changes. A fixed entry is
  **deleted**; its ID is not used again, so references stay valid.
- **To fix one** (whoever does it):
  1. Read `CLAUDE.md` first: it holds this project's measured facts and rules
     (several "obvious" fixes are known to be wrong there).
  2. One bug, one commit. Keep the change small; do not tidy other code.
  3. First write a test that **fails on the current code** for the reason the
     entry gives, then fix the code until it passes.
  4. Run the whole suite: `pytest` (about 9 min) and, for extension changes,
     `cd chrome_ext/test; npm install; npm test`.
  5. Extension: edit `chrome_ext/` only, then copy the changed files to
     `edge_ext/` and `firefox_ext/` (`tests/test_extension_sync.py` checks).
     Firefox's `manifest.json` is its own - never copy it.
- **Then say "B<n> is fixed"** to Claude. Claude checks that the new test fails
  on the old code and passes on the new, runs the suite and a real check where
  one is possible (a real browser, the real engines), reviews what else the
  change touches, and deletes the entry - or says what is still wrong.

Severity: **Must** = wrong result, data loss, or a crash users hit. **Should**
= a real fault with a way round it. **Nice** = small.

---

## B1 - Should - A plain file whose link contains a video site's name is fetched by yt-dlp, and saved with the wrong type

- **Where:** `yt_dl.py` `is_ytdlp_url` (about line 36): `any(d in u for d in _SITES)`
  matches a site name ANYWHERE in the link.
- **What happens:** measured 2026-11-16 with the real engine: a plain zip at
  `http://<host>/fox.com-backup.zip` ("x.com" is in "fox.com") went to the
  yt-dlp engine, ended **Completed**, and was saved as
  `fox.com-backup.unknown_video`. The same for any link on `box.com`,
  `dropbox.com`, `netflix.com`, `fox.com`, a file named `ok.ru.iso`, or a link
  with `youtube.com` in its query (`?ref=youtube.com`). Such downloads also lose
  the segmented engine (several connections, resume).
- **Expected:** only a link whose HOST is one of `_SITES` or a subdomain of one
  (`www.youtube.com`, `m.youtube.com`, `youtu.be`, `player.vimeo.com`) goes to
  yt-dlp. The extension already decides this way:
  `chrome_ext/background.js` `onYtdlpSite` (host equal to, or ending in
  "." + a site).
- **Test:** `is_ytdlp_url` is False for `https://app.box.com/s/x.zip`,
  `https://www.dropbox.com/s/k/setup.exe?dl=1`,
  `https://cdn.example.com/f.zip?ref=youtube.com`,
  `https://mirror.example/ok.ru.iso`; True for `https://youtu.be/abc`,
  `https://m.youtube.com/watch?v=abc`, `https://player.vimeo.com/video/1`,
  `https://www.reddit.com/r/x/comments/1/`. Callers: `gui2/app.py`
  (`use_ytdlp` of a new download), `gui2/dialogs/new_download.py` (the
  "Use yt-dlp" tick), `yt_dl.is_ytdlp_task`.

## B2 - Should - The web page's downloads wait for a click at the PC while a New Download window is open

- **Where:** `gui2/app.py` `_drain_pending` (the window's 500 ms timer) calls
  `_add_download`, which opens the New Download window with `dlg.exec()`
  (about line 1369) - and the duplicate questions in it the same way.
- **What happens:** `exec()` blocks `_drain_pending` until someone at the PC
  answers. Everything queued behind that item waits: downloads added from the
  web page (`ask=False`, meant for when nobody is at the PC), more browser
  hand-overs, and a second app launch's "come forward". Someone using the web
  client from a phone sees their adds do nothing until the PC's window is
  answered. CLAUDE.md "Never exec() a dialog from inside refresh()" is the
  same pattern (it opened "Update ready" twice once).
- **Expected:** items that need no question (`ask=False`, `focus`) are handled
  at once whatever is open; the New Download window and its questions are
  shown (`show()`, answer taken from `finished`), not `exec()`'d.
- **Test:** in the real window (in a subprocess, as
  `tests/test_start_with_saved_state.py` does, no local server): put a browser
  item (ask True) then a web item (ask False) in `pending`; with the New
  Download window left open, after a refresh the web item is in the list.

## B3 - Should - The test suite sometimes crashes (Windows access violation) in `tests/test_torrent_duplicates.py`

- **Where:** inside `gui2/app.py` `_fetch_torrent`'s local `QEventLoop`
  (`loop.exec()`), during `test_a_web_link_that_is_gone_says_so` or
  `test_a_web_link_that_is_a_web_page_says_so_and_adds_nothing`.
- **What happens:** the pytest process dies (exit 139 / access violation) now
  and then: twice locally under load, on CI windows/py3.12 (2026-11-11).
  Reproduces by running `tests/` in name order up to and including that file.
  An idle full run passes; the file alone passes. Seen again 2026-11-16 (a
  full run while other work loaded the PC; the next run passed).
- **Ruled out** (CLAUDE.md "OPEN:"): a leftover gc_guard timer; a real window
  in the test process. **Suspect:** an earlier test's pending `deleteLater()` or
  timer delivered inside that nested loop.
- **Expected:** no crash. If the nested loop is the cause, the app can hit it
  too: then fetch the `.torrent` without a nested loop (a thread and a signal).
- **Test:** the name-order run above passes 10 times in a row.
