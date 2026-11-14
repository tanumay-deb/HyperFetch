# "What's new" window - short design (2026-11-14)

## Why

After an update, people do not know what changed unless they open GitHub.
The first time a new version starts, a small window tells them, in plain
words, what is new and what was fixed.

## Approaches

- **A - a notes file per version, shipped in the app (picked by the user).**
  `notes/<version>.md`, plain lines written for users. Works offline; the
  same file is the GitHub release notes, so there is one text.
- B - fetch the GitHub release notes at first start. No new file, but needs
  the internet at that moment and shows whatever GitHub's notes say.

## Design

- **When:** the app remembers the version it last ran as (`last_version`,
  settings.json). At start, if that is older than `utils.APP_VERSION` and
  `notes/<APP_VERSION>.md` exists, the window shows once and the version is
  recorded. A fresh install (nothing recorded) gets the Welcome window as
  today, no What's new, and the version is recorded.
- **Skipped versions** (2.8.7 -> 2.8.9): this version's notes only. *(my call)*
- **The text:** `notes/<version>.md`, Markdown, shipped in the build
  (`HyperFetch.spec` datas, pyproject data-files). The release uses it:
  `gh release create --notes-file notes/<version>.md`. A test fails when
  `APP_VERSION` has no notes file, so a release cannot go out without one.
- **The window:** small, the app's dialog look, the notes rendered (Qt reads
  Markdown), Close and "All release notes" (GitHub releases page). Shown
  after the main window appears and not modal - never exec()'d from
  `refresh()` (CLAUDE.md gotcha).
- **Not shown:** `--selftest`, the headless server, no notes file, a
  downgrade.

## Non-goals

Fetching notes from the internet; a full history of every version; a
"don't show again" switch (it shows once per version anyway).

## Left to the builder

Window size and wording of the buttons; where in start-up the check sits
(after `_restore_window`, so the main window is there).

## Notes for 2.8.9 (draft, for the user to edit)

```markdown
## HyperFetch 2.8.9

**Browser extension 1.9.6**
- The Download button now also shows on videos playing inside embedded
  players, the way many video sites show them. Update the extension from
  your browser's store.

**YouTube**
- HyperFetch now includes YouTube's challenge solver: with Deno installed,
  videos that need you signed in download again. Without Deno, the download
  says so and how to get it, and picks it up without a restart.
- A video sent from the browser that fails with your sign-in is tried
  again without it.
- When YouTube refuses the connection ("confirm you're not a bot"), the
  download says what to do instead of showing yt-dlp's options.

**Fixes**
- The speed limit now applies to streams and yt-dlp videos too.
- A download from a server that cannot resume, cut off early, now fails
  with "Restart" instead of showing Completed with a short file.
- Move to... takes an unfinished download's partial file along to the
  other drive.
- A stream that fills the disk names the drive and suggests Move to...
- Removing a paused torrent no longer stalls the window; a finished
  torrent keeps its name when moved; magnet links written in Base32 are
  recognised as duplicates.
- The finish window's time and average speed no longer count time spent
  waiting in the queue.
- The "System" theme follows light-mode Windows, and the accent colour
  you chose sticks.
- Plus smaller fixes to torrents, the web page, settings and checksums.
```
