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
- **Also (2026-11-17):** `_drain_pending` has no guard against itself. While
  one add waits in a nested loop - the New Download window's `exec()`, or
  `_fetch_torrent`'s `QEventLoop` while a web `.torrent` link is fetched - the
  500 ms timer runs `_drain_pending` again, and the next item is added inside
  the first. Two hand-overs of the same `.torrent` link in a row (a double
  click) can then both pass the duplicate check, which runs before either
  is kept. Not reproduced; read from the code.


## B4 - Should - A delete the app did not finish before closing can delete a different file at the next start

- **Where:** `gui2/app_shortcuts.py`: "Delete from disk" records the paths in
  `pending_deletes.json` (`_note_pending`, about line 121); the next start
  deletes every path still listed (`_resume_deletes`, about line 125) unless a
  download in the list now points at it.
- **What happens:** the delete waits for the download's engine to let go (up
  to 60 s, then 10 tries 0.5 s apart). Close HyperFetch in that time and the
  paths stay listed. If before the next start the user saves ANOTHER file of
  that name there - the browser downloads `report.pdf` again into the same
  folder, say - HyperFetch's next start deletes it: the list holds only the
  path, so a new file there looks like the old one.
- **Expected:** at the next start, delete only what is still the file that was
  asked for. Record each path's size and modification time (for a folder, the
  newest file's time) when the delete is asked; at start, delete a path only
  when they still match, and drop it from the list otherwise (with a log line).
  An unfinished download is still being written when the delete is asked:
  record what is there at that moment, after its engine has let go if possible.
- **Test:** ask a delete, close before it is done (call `_note_pending` as
  `_delete_files` would and stop there); replace the file with different
  content; start again (`_resume_deletes`): the new file is still there. And:
  left unchanged, it is deleted.
