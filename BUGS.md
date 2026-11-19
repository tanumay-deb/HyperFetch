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

_No open bugs._
