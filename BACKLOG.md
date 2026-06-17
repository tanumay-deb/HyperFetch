# HyperFetch Backlog

Living doc. Edit freely. Top of each tier = highest priority. When work lands,
move the item to **Done** with the commit SHA and a one-line note.

Conventions:
- **Tier 1** — fast, safe, ~20-50 lines each. Low risk.
- **Tier 2** — medium-contained, ~100-250 lines. Bounded blast radius.
- **Tier 3** — architectural. ~300-600 lines + tests. Needs a design pass.
- **Tier 4** — multi-session each. Product decisions / new infra / new deps.
- **Hygiene** — not user-visible but raises code quality.
- **Bugs** — known defects with a known fix path.
- **Watch** — flagged but not committed to. Re-evaluate before picking up.

---

## Done (most recent first)

| SHA | Summary |
|---|---|
| `9eeaac2`+ | Hygiene: property/fuzz tests for the formatters + 30-task/3-queue concurrency stress test (no deadlock, no slot leak). 143 tests. |
| `29f760d` | Fix 10 confirmed issues from adversarial review of the arch batch: **blocker** Auto-segments=0 deadlock; dead concurrency setting; move-to-queue missed wakeup; per-queue slot leak on move; queue filter showed all tasks; 403-resume scheme bypass + stale-plan corruption; finalize silent data loss; dead runtime theme switch; QMessageBox leak; error=None. |
| `d54cca5` | closeEvent 3-way Minimize/Close/Cancel dialog on tray exit (+ tests). |
| `d36e620` | Tier 3: Inline quality-picker dropdown in the extension for HLS variants; removed deprecated test logic. |
| `9693651` | Tier 2/3: Native multi-queue + per-queue concurrency, smart adaptive segments (Auto), 403/URL-expiry resume, `TaskStatus(str,Enum)`, pinned requirements, **`gui/` package split** (`main_window`/`models`/`delegates`/`dialogs`/`theme`/`icons`), `QSystemTrayIcon` minimize-to-tray + completion toasts, SVG sidebar/file icons, `%TEMP%` orphan sweep. |
| `92d7c5c` | Fixed missing `TaskTableModel` import causing blank NameDelegate cells. |
| `6d57bcf` | Local crash reporter + GitHub-Releases update check (light versions, no infra). |
| `212f25c` | Tier 1 hygiene: cancel during backoff, ENOSPC message, orphan `.hfdownload` sweep, 2nd-tab HLS regression tests. |
| `05fdc02` | Fix 11 confirmed issues from adversarial review of `164cdce`. |
| `164cdce` | Major overhaul: ABDM-style UI (Phase 1+2+3 + tray sidebar + sparkline + search), `.hfdownload` corruption fix, manual capture, HLS resume. |
---

## Tier 1 — fast, safe

_(empty — pick from below or file as discovered)_

---

## Tier 2 — medium contained

- [ ] **SHA-256 integrity verification.** After download, look for `<url>.sha256` / `<url>.sha256sum` next to the file, fetch, verify. Surface PASS/FAIL on the row. Settings toggle.
- [ ] **Schedule "start at HH:MM" / "only on Wi-Fi" / "monthly cap".** Extends `Download Later`. Needs a scheduler tick in the queue manager + a tiny UI in the file-info dialog.

---

## Tier 3 — architectural

_(empty — pick from below or file as discovered)_

---

## Tier 4 — multi-session each

- [ ] **HTTP/2 + HTTP/3.** Migrate `requests` → `httpx` (or `niquests`) in `downloader.py` + `hls.py`. Unlocks multiplexing, drops socket count. Re-run the full suite + the hls fixture server. Risk: behavioral surprises in retry/timeout semantics.
- [ ] **Auto-update — real version.** Light version (manual button) shipped in `6d57bcf`. Real version needs: code signing cert · signed Windows installer (Inno Setup already wired) · WinSparkle or a stub that swaps the running .exe · differential patches · rollback. Product decisions before code.
- [ ] **Crash reporter — networked.** Light version (local JSON dumps) shipped in `6d57bcf`. Networked version needs: submission endpoint (Sentry SaaS / Glitchtip self-hosted / custom) · opt-in consent UI · dedup · rate limit. Product decision on destination.
- [ ] **mmap-based finalize.** Map the `.hfdownload` so the GUI can stream-preview a partially-downloaded video while it grows. Cross-platform mmap gotchas (Windows file locks).
- [ ] **BitTorrent / magnet link support.** Bolt-on second engine (libtorrent or aria2 subprocess). Adds a runtime dependency + a separate task model.

---

## Bugs / regressions (none open)

_(none currently — the arch-batch review found 10 real bugs incl. 1 blocker, all fixed in `29f760d`; the prior review found 11, all fixed in `05fdc02`.)_

---

## Hygiene / quality (non-user-visible)

- [ ] **Coverage report.** `pytest --cov` to learn what % of code the 136 tests actually touch.
- [ ] **`mypy --strict` pass.** `task.py` has partial hints; the `gui/` package + `downloader.py`/`queue_manager.py` have almost none. Catches a lot.
- [ ] **Pre-commit hooks**: `ruff` · `mypy` · chrome↔edge parity check · pytest fast subset.
- [ ] **Structured logging** module-wide (`logging` not bare `print`/silent). Format: `[downloader] task=abc seg=3 retry=2 ...`.
- [ ] **Schema validation** on `downloads.json` / `settings.json` — silent failure mode today if JSON has unexpected shape.
- [ ] **GUI integration test** with offscreen Qt — exercise pause/resume/cancel via actual click events, not direct method calls.
- [ ] **Visual regression** — selftest screenshot diff against a committed PNG.

---

## Watch (flagged but not committed to)

- [ ] **YouTube-dl / yt-dlp integration.** Extension points the app at a YouTube page; app shells out to `yt-dlp` to extract the real URL. Big UX win, dependency complexity.
- [ ] **Per-host rules.** "cookies stay 1 day on `<host>`" / "always 4 segments on `<host>`". Needs a small DSL + UI.

---

## How to use this doc

1. **Adding work:** drop a bullet under the right tier with a one-line scope (lines of change, files touched, design risk).
2. **Picking work:** pull from the top of a tier. Don't skip Tier 1 to chase Tier 4 — small wins compound.
3. **Finishing work:** move the bullet to **Done** with the commit SHA. Keep the note short.
4. **Bugs found in review:** if confirmed real, file under **Bugs** with the file:line and the fix path. If declined, file under **Watch** with the reason.

Three adversarial reviews so far have each found ~10 real bugs in newly-committed code (incl. a download-hangs-forever blocker in the multi-queue/Auto-segments batch). Treat every architectural change as an invitation for adversarial review before declaring done — "tests pass" is necessary, not sufficient.
