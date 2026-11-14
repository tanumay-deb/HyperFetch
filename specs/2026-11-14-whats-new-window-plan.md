# "What's new" window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the first start of a new version after an update, show a small non-modal window with that version's notes from `notes/<version>.md`.

**Architecture:** `gui2/dialogs/whats_new.py` holds the decision (`due`), the reader (`notes_for`) and the window (`WhatsNewDialog`). `DownloadAppV2.__init__` asks `due` beside the existing Welcome check and shows the window from a `QTimer.singleShot`, recording `last_version` in `_extras`. Notes ship as data files (PyInstaller spec + pyproject).

**Tech Stack:** PySide6 (`QTextBrowser.setMarkdown`), pytest (subprocess window tests, as tests/test_start_with_saved_state.py).

**Spec:** specs/2026-11-14-whats-new-window-design.md

## Global Constraints

- Notes path: `utils.resource_path("notes", f"{version}.md")`; version read as `utils.APP_VERSION` at call time (tests patch it).
- Settings key: `_extras["last_version"]` (settings.json).
- The window is shown, never `exec()`'d, and never from `refresh()`; `Qt.NonModal`.
- Not shown in `--selftest`, and the self-test records nothing (it runs on the real app-data).
- Version compare: `updater._newer(a, b)` (tolerant semver, already there).

## Review Focus

- A 2.8.8 user (`welcomed` True, no `last_version`) updating to 2.8.9 must see it -> test in Task 2.
- A downgrade (`last_version` newer) shows nothing -> test in Task 1 (`due`).
- `--selftest` on a used install must not record `last_version` -> test in Task 2.
- A version with no notes file: no window, no error -> test in Task 1 (`notes_for` returns None) and Task 2.
- A fresh install gets Welcome only, and the next start shows nothing -> test in Task 2.

---

### Task 1: notes, packaging, decision and window

**Files:**
- Create: `notes/2.8.9.md` (the approved draft, without its `##` title line), `gui2/dialogs/whats_new.py`
- Modify: `HyperFetch.spec` datas (`('notes', 'notes')`), `pyproject.toml` data-files (`"share/hyperfetch/notes" = ["notes/*.md"]`)
- Test: `tests/test_whats_new.py`

**Interfaces:**
- Produces: `due(extras: dict, version: str) -> bool` (welcomed and `_newer(version, extras.get("last_version") or "0")`); `notes_for(version: str) -> str | None` (file text, None when absent or empty); `WhatsNewDialog(parent, version: str, text: str)` (DialogHeader "What's new in {version}", QTextBrowser with the Markdown, "All release notes" -> `https://github.com/{updater.REPO}/releases`, "Close").

- [ ] Step 1: tests - `test_due` table: not welcomed -> False; welcomed, no last_version -> True; "2.8.8" -> True; "2.8.9" -> False; "2.9.0" -> False. `test_notes_for_missing_version_is_none` ("0.0.1"). `test_this_version_has_notes`: skip when `_parse_semver(APP_VERSION) <= (2, 8, 8)`, else `notes_for(APP_VERSION)` truthy. `test_2_8_9_notes_exist`. `test_notes_ship_in_both_builds`: spec text has `('notes', 'notes')`, pyproject has `share/hyperfetch/notes`.
- [ ] Step 2: run, expect ImportError / FAIL.
- [ ] Step 3: implement; Step 4: run, PASS; Step 5: commit.

### Task 2: the window shows it once

**Files:**
- Modify: `gui2/app.py` (after the Welcome check in `__init__`; `_show_whats_new`; `_self_test_v2` sets module flag `_SELFTEST = True` before building the window)
- Test: `tests/test_whats_new.py` (subprocess, real window, `run_server` stubbed, `utils.APP_VERSION = "2.8.9"` patched in the child)

**Interfaces:**
- Consumes: Task 1's `due`, `notes_for`, `WhatsNewDialog`.
- Behaviour: not welcomed -> record `last_version` now (Welcome as today). `due` and not `_SELFTEST` -> `QTimer.singleShot(800, self._show_whats_new)`; it records `last_version`, saves settings, and shows the dialog (kept on `self._whats_new`, `WA_DeleteOnClose`) only when `notes_for` has text.

- [ ] Step 1: tests - `test_an_update_shows_its_notes_once` (settings `welcomed` True, no `last_version`: child prints the visible dialog's title and modality; title has "2.8.9", NonModal; settings.json then has `last_version` "2.8.9"; a second start shows none). `test_a_fresh_install_gets_welcome_only` (no settings: no WhatsNewDialog; `last_version` recorded). `test_the_selftest_records_nothing` (child sets `appmod._SELFTEST = True` path via `_self_test_v2()`; settings keep `last_version` "2.8.7").
- [ ] Step 2: run, FAIL; Step 3: implement; Step 4: run + `tests/test_start_with_saved_state.py`, PASS; `python main.py --selftest` isolated, exit 0; Step 5: commit + push.

### Task 3: release step and notes

**Files:** CLAUDE.md (gitignored, local): a release is created with `gh release create --draft --verify-tag --notes-file notes/<v>.md`; bumping `APP_VERSION` needs `notes/<v>.md` (the test enforces it).

- [ ] Step 1: write the note. No commit (file is ignored).
