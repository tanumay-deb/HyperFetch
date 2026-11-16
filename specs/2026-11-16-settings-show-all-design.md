# Settings: everyday options first, the rest behind "Show all settings" - short design (2026-11-16)

## Why

Settings has grown to about 53 options in 9 sections. Someone who only wants
to download things has to wade through disk caches, listen ports and debug
logging to find the folder or the speed limit. The user: "too many options ...
for normal users it will be hard to navigate".

## Decided by the user

- **Keep every option.** Nothing is removed or merged; the extra ones are
  tucked away behind a **"Show all settings"** switch.
- **Approach A - hide rows in place** (over B, a separate Basic page that
  duplicates the everyday options, and C, hiding whole sections only, which
  leaves Downloads at 15 rows and Torrents at 11).
- **The everyday list** (about 13), as proposed:
  - General: Default Download Folder, Check for updates automatically, Close
    button behavior
  - Downloads: Concurrent Downloads, Download Speed Limit, When download is
    complete, Ask before adding a download
  - Torrents: Seed after completing, Upload speed limit
  - Browser Integration: Browser Extension (and the pairing token under it)
  - Appearance: theme, accent, Font Size

## Design

- **The switch** sits at the bottom of the Settings sidebar (always in view),
  off by default. Its state is remembered (`settings_show_all` in
  settings.json). *(my call: the sidebar, not each page)*
- **Off:** each section shows only its everyday rows; a section with none
  (Network, Web Client, Advanced) leaves the sidebar. About stays. **On:**
  everything as today, in today's order.
- **Search finds every option.** A search is for finding one, so it looks
  through hidden rows too and shows each match (and its section) whatever the
  switch says. *(my call: a search for "proxy" must not come back empty)*
- **Hidden is only hidden.** Every row is built as today; its value is read
  and saved exactly as now. Switching never changes a setting.
- Which rows are everyday is one list in the code, by row label, next to the
  page builders, so a new option is tucked away unless someone adds it there.

## Non-goals

Removing or merging options; changing defaults; moving options between
sections; renaming them.

## Left to the builder

The switch's look (the app's AnimatedToggle and a label); how hidden rows
are hidden (`setVisible` on the row widget `_row` makes, as search does); the
page shown first when the current one leaves the sidebar.
