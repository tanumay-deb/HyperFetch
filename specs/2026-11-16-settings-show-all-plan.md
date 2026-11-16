# Settings "Show all settings" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Settings shows the everyday options; a "Show all settings" switch in its sidebar reveals the rest in place.

**Architecture:** `_row` tags each row with its label; one set `EVERYDAY` (row labels) in `gui2/dialogs/settings_pages.py`, and a `everyday` property on the two untagged panels that stay (pairing token, About). `SettingsDialogV2._apply_search` - which already hides rows, panels and sidebar entries for a search - also applies the switch when nothing is searched.

**Tech Stack:** PySide6; pytest with the `qapp` fixture (as tests/test_default_queue.py).

**Spec:** specs/2026-11-16-settings-show-all-design.md

## Global Constraints

- Everyday rows, by label exactly: Default Download Folder, Check for updates automatically, Close button behavior, Concurrent Downloads, Download Speed Limit, When download is complete, Ask before adding a download, Seed after completing, Upload speed limit, Browser Extension, Font Size. Untagged panels kept: the pairing token panel, About. Theme and accent are outside panels: always shown.
- Switch text "Show all settings", off by default, saved as `settings_show_all` through `values()`.
- A search looks through every row whatever the switch says. Switching changes no value.

## Review Focus

- The page open when the switch goes off leaves the sidebar -> another page is selected (test in Task 1).
- A search for a tucked-away option ("proxy") finds it with the switch off (test in Task 1).
- Clearing the search with the switch off tucks the rows away again (test in Task 1).
- `values()` is the same with the switch on or off, apart from `settings_show_all` (test in Task 1).
- A saved `settings_show_all: True` opens with everything shown (test in Task 1).

---

### Task 1: the switch, the everyday list, the filter

**Files:**
- Modify: `gui2/dialogs/settings_pages.py` (`EVERYDAY`; `_row` sets property `"label"`; pairing-token panel in `_p_browser` and the About panel get property `"everyday"` True)
- Modify: `gui2/dialogs/settings.py` (switch under the sidebar list; `_apply_search` applies it; `values()` adds `settings_show_all`; when the current page is hidden, the first shown one is selected)
- Test: `tests/test_settings_show_all.py`

- [ ] Step 1: tests - a dialog built as tests/test_default_queue.py does (`extras={}`); "shown" = `not w.isHidden()` (the dialog is never shown):
  - `test_everyday_options_only_by_default`: shown row labels == the constraint's list; sidebar rows hidden exactly for Network, Browser Access, Advanced; on Browser the token panel shown, the capture panel hidden.
  - `test_show_all_shows_everything`: switch on -> every row shown, no sidebar row hidden.
  - `test_search_finds_a_tucked_away_option`: switch off, search "proxy" -> the "Proxy Settings" row and Network's sidebar row shown; search cleared -> hidden again.
  - `test_the_open_page_leaving_the_sidebar_moves_to_another`: switch on, Network selected, switch off -> current row is not Network and not hidden.
  - `test_the_switch_is_saved_and_changes_no_value`: `values()` has `settings_show_all`; equal apart from it after switching; `extras={"settings_show_all": True}` starts on.
- [ ] Step 2: run, expect FAIL (no switch).
- [ ] Step 3: implement as above.
- [ ] Step 4: run it + tests/test_default_queue.py, test_host_rules.py, test_about_page.py, test_pair_prompt.py; then the full suite; then a screenshot off screen with real fonts (scratchpad, as whatsnew_shot.py), both switch states.
- [ ] Step 5: commit, push; CLAUDE.md note (local).
