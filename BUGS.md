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
     Never add a code path that only a test's fake takes: change the fake.
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

## B2 - Should - New Download's answers: the code users run has no test of most buttons

- **Fixed already (5d48beb, Gemini; checked 2026-11-19):** New Download and
  the "Already added" / "Torrent already added" questions are shown, not
  `exec()`'d, so web-page adds and "come forward" no longer wait behind
  them; `_drain_pending` cannot run inside itself (`_draining_pending`).
- **Still wrong:** `gui2/app.py` keeps the old `exec()` branch beside each
  shown one (about lines 1378, 1480, 1632:
  `type(dlg).exec is not QDialog.exec or not isinstance(self, QWidget)` and
  `type(box).exec is QMessageBox.exec`). Only tests take those branches:
  their fake windows (`tests/test_torrent_duplicates.py` `window` + `_Box`)
  and fake dialogs (`tests/test_connections_per_download.py`,
  `tests/test_typed_video_names.py`, `tests/test_update_download.py`). So
  the tests check a path users never run, and the path users run
  (`_on_new_download_finished`, `_on_dup_box_finished`) is tested for two
  answers only: New Download accepted, "Already added" cancelled. "Show
  Existing", "Add Anyway", "Load Trackers" and "Keep Existing" through a
  shown box have no test, and "Don't show this again" is saved in two places.
- **Expected:** one path. The fakes answer through `finished` (a fake
  `show()` calls the connected slot) and the `exec()` branches are deleted.
  Do not add code that only a test's fake takes - change the fake.
- **Test:** the existing duplicate tests pass through the shown path; plus a
  real-window test in a subprocess (like
  `test_web_add_does_not_wait_while_new_download_window_is_open`) that presses
  "Add Anyway" on a shown "Already added" box: the second download is in the
  list.
- **Also (Nice, made by the same change):** New Download no longer blocks the
  window, so Manage Queues can delete the queue picked in an open New
  Download. Pressing Download then makes `QueueManager` create a queue of that
  name with limit 3 (`queue_manager.py`, about lines 300 and 493: "not there
  -> `Queue(name, 3)`"). Expected: a queue that is gone means Main
  (`_create_download_task`). Test: continue an add whose values name a queue
  that was deleted: the task's queue is Main and no queue of the old name
  exists.

## B4 - Should - A delete cut short by closing can be skipped without a word

- **Fixed already (dbb38b2, Gemini; checked 2026-11-19):**
  `pending_deletes.json` keeps each path's size and modification time, and
  the next start deletes only what still matches, so a new file saved at
  that path is kept.
- **Still wrong:** the size and time are taken when the delete is asked
  (`gui2/app_shortcuts.py` `_delete_tasks`, about line 161), while a running
  torrent or video is still being written: aria2 keeps writing until
  `_delete_files` has waited for the worker and called `_let_go`. Close
  HyperFetch in that wait - the case pending deletes exist for (11ce5ae) -
  and the time differs at the next start: `_resume_deletes` keeps the files
  and drops them from the list, and only `hyperfetch.log` says so. The user
  asked for them to go, and they stay. Read from the code; not reproduced
  with aria2.
- **Expected:** keep the file (a new file must never be deleted), but tell
  the user: put the kept path in `self._deletes` with a reason such as "it
  changed after you asked to delete it, so it was kept". `_finish_deletes`
  already shows that list in a toast.
- **Test:** note a path, change the file, run `_resume_deletes`: the file is
  still there, and `_deletes` names it with that reason.
- **Also (Nice):** `_note_pending(add=...)` measures on the window's thread;
  for a folder `_path_stamp` walks and stats every file. A torrent of
  thousands of files on a slow or network drive freezes the window - the
  delete was moved off that thread for this reason (2026-11-04). Expected:
  the window notes the paths as before (no size or time; for those few
  milliseconds the old rule holds), and `_delete_files`, on its own thread,
  measures them first thing. Test: record the thread `_path_stamp` runs on
  during `_delete_tasks`: not the main thread.

## B6 - Nice - Every new download asks for one byte first; a link that may be fetched only so many times loses a fetch

- **Where:** `downloader.py` `Downloader._probe_at` (about line 653). Since
  8f1e952 every new plain download sends a HEAD, then ALWAYS a GET with
  `Range: bytes=0-0`, closes it, and then the parts' GETs. Before, the
  one-byte GET was sent only when HEAD gave no size or no ranges.
- **What happens:** a link that counts its fetches (a one-time or N-times
  token) loses one to the one-byte GET. With a limit equal to the parts the
  download uses, the last part is refused for good and the download fails
  "URL expired". Not reproduced: no such host at hand, and some hosts may
  count HEAD too. Browser captures are not changed by this: the browser has
  already asked for the file before it hands it over.
- **Expected:** no request of its own: the probe's GET asks `bytes=0-` and,
  on a 206, goes on as the first part's stream (its `Content-Range` gives the
  size); the other parts start after it. Keep what 8f1e952 fixed: a 200 to
  the range means one piece - a HEAD that promises ranges the GET does not
  keep still downloads in one piece.
- **Test:** a local server that answers at most 4 GETs per link (the 5th:
  403; HEAD not counted), a file big enough for 4 parts, 4 connections: the
  download completes and the file is whole. `tests/test_server_habits.py`'s
  "ranges promised but not kept" still passes.
- **Note:** B7 adds a look at the server before New Download opens. Do B6
  and B7 in one session.

## B7 - Nice - New Download cannot show the server's name for a link that does not name its file

- **Where:** `gui2/dialogs/new_download.py` (the name box, and the folder line
  `_update_dest_hint`, about line 205) and `gui2/app.py` `_add_download` /
  `_create_download_task`. The server's name (`Content-Disposition`, or where
  the link leads) is read only when the download starts:
  `Downloader._take_server_name` (5c3817a, ddf0ffd).
- **What happens:** a link such as `.../download.php?id=7` or
  `go.microsoft.com/fwlink/?linkid=...`, added by hand, from the clipboard or
  by right-click "Download with HyperFetch", opens New Download with an empty
  name box ("auto-detected") and the folder of the link's own name - `Other`
  for `download.php`. The user cannot see what the file is or where it will
  go; it is renamed and filed only as it starts. Browser captures are fine:
  the browser hands over the name.
- **Expected:** while the name box is untouched, New Download asks the server
  on a thread - one HEAD with the download's headers, cookies and proxy
  (`downloader.Session`, `utils.PROXIES`, `utils.VERIFY_TLS`); never a GET
  (see B6) - and fills in the name and folder when the answer comes. The
  filled-in name must count as `suggested`, not as typed: the dialog hands its
  new `suggested` back with its values, because `_create_download_task`
  compares the box with the caller's `suggested` (CLAUDE.md: "typed" is
  "differs from suggested", or every video gets a typed name). Nothing for
  torrents, video pages (`yt_dl.is_ytdlp_url`), or once the user has typed.
- **Test:** a local server that answers HEAD of `/download.php` with
  `Content-Disposition: attachment; filename="report.pdf"`: the dialog's name
  box reads `report.pdf` and its folder line names the PDF's category
  (`utils.category_for`); accepted untouched, the task's `name_chosen` is
  empty. With a name typed first, the answer changes nothing.

## B8 - Should - A resume after the file changed on the server joins two versions

- **Where:** `downloader.py` `Downloader.run`, the resume branch (`else:`
  after `if not self.t.segments`, about line 1156): it does not look at the
  server, and `_the_range` checks each ranged answer only for 206, the asked
  start and the same total. `task.etag` exists and is saved, but only
  `run_media` (a video page's file) uses it.
- **What happens:** a paused download whose file is replaced at the same link
  by another of the SAME size (a nightly build, a re-uploaded file, a fixed
  installer) resumes: the bytes before the pause are the old file's, the rest
  the new one's. It ends "Completed" with a file that is neither. Only the
  `.sha256` check (`utils.HASH_CHECK`, off by default) would catch it. A
  different size is caught (`_the_range`'s total).
- **Expected:** the first probe keeps the server's ETag (`t.etag`) and
  `Last-Modified` (a new saved field); every ranged request to the FIRST link
  sends `If-Range` (the ETag; `Last-Modified` when the ETag is weak `W/...` or
  missing). A 200 answer to it means the file changed: ERROR "The file
  changed on the server - Restart to download it again", not `_the_range`'s
  "no longer sends this file in parts". Mirrors get no `If-Range` (their
  validators are their own; they get no browser headers either). A server
  that sends neither validator works as today.
- **Test:** a local server with ETag "v1" that honours `If-Range` (200 and the
  whole file when it does not match): download part of a file and pause; the
  server now serves other bytes of the same size with ETag "v2"; resume: the
  task is ERROR with that message, never Completed. The old code ends
  Completed with mixed bytes. An unchanged file resumes as before.

## B9 - Should - A VPN adapter that reports a gateway is taken for an internet connection

- **Where:** `lines.py` `available()`: a line is any adapter that is up with
  an IPv4 address and a default gateway. `IfType` and `Description` are read
  into `_ADAPTER` but not used. `Downloader._pick_lines`'s VPN check (7518f03,
  849001d) refuses lines only when the route to the server leaves from an
  address that is NOT in `available()`.
- **What happens** (only with Settings -> Network "Use more than one internet
  connection" on; it is off by default): a VPN whose adapter reports a
  gateway - OpenVPN's TAP adapter can; WireGuard/Wintun usually reports none -
  is in `available()`. Switched on for the first time, every line is ticked,
  the VPN too: a download is spread over the VPN and the Wi-Fi, and the Wi-Fi
  part goes around the VPN, from the PC's real address. With the VPN unticked
  it is worse since 8fea01d: the route leaves from the VPN's address, which
  is in `available()`, so the check passes and the whole download goes over
  Wi-Fi + phone, none of it through the VPN. Not seen on a real VPN.
- **Expected:** a VPN or virtual adapter is never a line: by `IfType`
  (53 = virtual, 131 = tunnel) and, for a TAP adapter that reports 6
  (Ethernet), by `Description` (TAP-Windows, Wintun, WireGuard, OpenVPN, ...).
  PPP (23) is how both a PPPoE broadband line and Windows' own L2TP/PPTP
  VPNs look: decide by `Description`, and test both. A VPN that is not in
  `available()` is then caught by the route check, as a TUN adapter already
  is.
- **Test:** `lines._adapters` fed a Wi-Fi adapter (IfType 71, gateway) and a
  "TAP-Windows Adapter V9" (IfType 6, gateway) that holds the route:
  `available()` lists the Wi-Fi only, and `_pick_lines` uses no lines
  (`tests/test_multi_line.py`'s `two_lines` fixture fakes `available` and
  `route_address`; this test needs the real `available`).
- **Note:** BACKLOG.md's "VPN kill switch" idea needs the same test of which
  adapter is a VPN.
