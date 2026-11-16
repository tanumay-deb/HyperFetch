# Combining internet connections - implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** One plain HTTP(S) download spreads its connections over two or more
internet connections ("lines") the user ticked in Settings; the faster line
fetches more of the file.

**Architecture:** `lines.py` lists the PC's adapters that are lines (ctypes
`GetAdaptersAddresses`). Settings -> Network saves which are used into
`utils.LINES`. `Downloader` builds one requests session per line, bound to the
line's IPv4 address (urllib3 `source_address`), cuts the file into more parts
than connections, and gives each request the line with the fewest running.

**Tech Stack:** Python 3.10+, requests/urllib3, ctypes (Windows), PySide6.

**Spec:** `specs/2026-11-13-multi-line-downloads-design.md`

## Global Constraints

- Off by default (extras `multi_line` False). Off, or with fewer than two
  lines found, the engine's parts, sessions and code path are today's.
- Plain HTTP(S) only, and only when the server takes ranges. Torrents, HLS,
  yt-dlp (incl. `run_media`), a proxy (Settings or environment) and the relay
  ("Get past provider blocks") keep one line.
- IPv4 lines only. Windows only: `lines.available()` returns `[]` elsewhere.
- No new dependency.
- Turning it on never fails a download one line would finish.

## Review Focus

1. A link tied to the client's address (signed CDN links): line B is refused
   403 before it ever streams - it must be dropped, never an ERROR.
   Test: `test_a_line_the_server_refuses_is_dropped_not_an_error` (Task 2).
2. A resume with lines off of a download cut for two lines (64 parts) must not
   open 64 connections. Rule (my call): the gate's cap is
   `min(len(segments), C x L)`, C = `parts_for(total, num_segments)`, L = 1
   without lines. A resume after the user changed the connection count now
   runs on the new count (it ran on the old part count). Test:
   `test_parts_cut_for_two_lines_resume_on_one_line_count` (Task 2).
3. The phone unplugged mid-download: its stream breaks, its next requests
   cannot bind - dropped after two failures in a row with no stream.
   Covered by `test_a_line_that_cannot_connect_is_dropped` (bind to an address
   not on this PC).
4. Every line gone: requests go out unbound, as today. Test:
   `test_every_line_dropped_goes_on_unbound` (Task 2).
5. A saved line not plugged in while Settings is open keeps its tick.
   Test: `test_a_saved_line_not_found_now_is_kept` (Task 3).

---

### Task 1: `lines.py` - which adapters are lines

**Files:** Create `lines.py`; Test `tests/test_lines.py`; Modify
`HyperFetch.spec` (hiddenimports: `lines`, imported lazily by the engine) and
`pyproject.toml` py-modules.

**Interfaces - Produces:**
- `lines.available() -> list[tuple[str, str, str]]`: (FriendlyName, IPv4,
  gateway IPv4), adapters that are up (OperStatus 1), have an IPv4 unicast
  address and an IPv4 default gateway; Windows order. `[]` off Windows or on
  any ctypes failure (logged once at debug).
- `lines.addresses(names) -> list[tuple[str, str]]`: (name, IPv4) for the
  names given that are in `available()` now, in the order of `names`.
- `lines.describe(conns: dict[str, int]) -> str`: `"Wi-Fi 6 · Phone 4"`, in
  dict order; `""` for an empty dict.
- `lines._adapters()`: the raw table (list of dicts name/up/ipv4/gateway),
  the one place ctypes is used; tests replace it.

- [ ] Tests:
  - `test_available_keeps_up_adapters_with_ipv4_and_gateway`: `_adapters`
    faked with Wi-Fi (up, 192.168.1.5, gw 192.168.1.1), Tailscale (up,
    100.64.0.2, no gw), Ethernet (down), Phone (up, 192.168.42.7, gw
    192.168.42.129) -> `[("Wi-Fi", "192.168.1.5", "192.168.1.1"), ("Phone",
    "192.168.42.7", "192.168.42.129")]`.
  - `test_addresses_in_the_order_given_and_only_present`: `addresses(["Phone",
    "Gone", "Wi-Fi"]) == [("Phone", ...), ("Wi-Fi", ...)]`.
  - `test_describe`: `describe({"Wi-Fi": 6, "Phone": 4}) == "Wi-Fi 6 · Phone 4"`.
  - `test_real_call_lists_the_default_route_adapter` (skip off Windows, or if
    a UDP `connect(("8.8.8.8", 53))` + `getsockname()` gives no address): that
    address is among `available()`'s.
- [ ] Run `pytest tests/test_lines.py` - fails (no module); implement
  (GAA_FLAG_INCLUDE_GATEWAYS 0x80, AF_UNSPEC, buffer grown on
  ERROR_BUFFER_OVERFLOW); passes.
- [ ] Commit `feat: lines.py lists the PC's internet connections`.

### Task 2: the engine spreads a download over the lines

**Files:** Modify `downloader.py`, `utils.py` (`LINES = []` beside
`MAX_CONNECTIONS`), `task.py` (transient `self.line_conns = {}` beside
`http_conns`); Test `tests/test_multi_line.py`.

**Interfaces - Consumes:** `lines.addresses`. **Produces:** `utils.LINES`
(list of names; empty = off), `task.line_conns` (name -> connections
streaming), `Downloader._lines` (list of (name, ip) in use).

- [ ] Tests (a local server in the test file that records each request's
  client address and serves ranges at a per-client-address rate; lines faked
  by `monkeypatch.setattr(lines, "addresses", ...)` with 127.0.0.1 and
  127.0.0.2 - Windows and Linux route 127/8 on loopback):
  - `test_two_lines_share_the_file_and_the_faster_serves_more`: 32 MiB,
    8 connections, 127.0.0.2 four times slower: file whole, both addresses
    served ranges, 127.0.0.1 served more bytes; log has "2 internet connections".
  - `test_a_line_that_cannot_connect_is_dropped`: second line 192.0.2.1 (not
    on this PC): Completed, log has "Stopped using".
  - `test_a_line_the_server_refuses_is_dropped_not_an_error`: server answers
    403 to 127.0.0.2: Completed.
  - `test_every_line_dropped_goes_on_unbound`: both lines 192.0.2.1/.2:
    Completed.
  - `test_one_line_or_off_changes_nothing`: `utils.LINES = ["A"]` (one line)
    and `[]`: `len(t.segments) == parts_for(total, 8)`, no line sessions.
  - `test_parts_for_two_lines`: 64 MiB, 8 connections, 2 lines -> 64 parts,
    cap 16; 10 MiB -> 10 parts (1 MiB floor).
  - `test_parts_cut_for_two_lines_resume_on_one_line_count`: a task with 64
    saved parts, lines off, 8 connections -> `_conn_cap == 8`.
  - `test_proxy_or_relay_keeps_one_line`: `utils.PROXIES` set, or the host a
    name `socket.getaddrinfo` answers with 127.0.0.1 -> `_pick_lines() == []`.
- [ ] Run - fails. Implement:
  - `_make_session(pool, source=None)`: with `source`, mount a
    `HTTPAdapter` subclass whose `init_poolmanager` passes
    `source_address=(source, 0)`.
  - `Downloader._pick_lines() -> list[tuple[str, str]]`: `[]` unless
    `utils.LINES`, not `_media`, `t.supports_range`, `t.total_size >= 2 MiB`,
    no `utils.PROXIES` nor `requests.utils.get_environ_proxies(url)`, the
    host is not a name resolving to loopback (relay); else
    `lines.addresses(utils.LINES)` when it has two or more. Called in `_run`
    before `_build_segments` in both branches (a resume reads lines afresh).
  - `_build_segments`: with L lines, n = `max(C, 4*C*L)` then
    `min(n, max(1, total // MiB))`, C = `parts_for(total, num_segments)`.
  - `_transfer`: `_conn_cap = min(len(segments), C * max(1, L))`; with lines,
    one session per line (cookies copied from `_session`) and
    `t.log_event("Using N internet connections: A, B")`.
  - `_worker`: after `_acquire_conn`, `line = _take_line()` (fewest running,
    ties by order; None when no line is live) and its session; `finally`
    hands it back. Accepted: that line's failure count to 0, `t.line_conns`
    updated in `_streams`. In `except`, first: a line request not accepted ->
    `_line_failed(line, why)`: count; at 2 in a row drop it (log event
    "Stopped using <name>: <why>"); `continue` at once while another line is
    live, else fall through to today's handling.
  - `run()`/`run_media()` `finally`: close line sessions too.
- [ ] Run the new tests, then the whole suite - pass.
- [ ] Commit `feat: a download spreads its connections over the chosen internet connections`.

### Task 3: Settings, the apply and the drawer

**Files:** Modify `gui2/dialogs/settings_pages.py` (`_p_network`),
`gui2/dialogs/settings.py` (`values()`), `gui2/app_settings.py`
(`_apply_network_settings`), `gui2/details_drawer.py` (Overview
connections); Test `tests/test_multi_line_settings.py`.

**Interfaces - Consumes:** `lines.available`, `lines.describe`, `utils.LINES`.

- [ ] Tests:
  - `test_switch_and_ticks_reach_values`: lines faked (Wi-Fi, Phone);
    switch on, Phone unticked -> `values()["multi_line"] is True`,
    `values()["lines"] == ["Wi-Fi"]`.
  - `test_first_switch_on_ticks_every_line`: no `lines` saved -> both ticked.
  - `test_a_saved_line_not_found_now_is_kept`: saved `["Phone"]`, only Wi-Fi
    found -> `"Phone" in values()["lines"]`.
  - `test_same_router_is_said`: Wi-Fi and Ethernet with one gateway -> the
    Ethernet tick's text contains "same router as Wi-Fi".
  - `test_apply_sets_utils_lines`: extras on + `["Wi-Fi"]` -> `utils.LINES ==
    ["Wi-Fi"]`; off -> `[]`.
- [ ] Implement. Row "Use more than one internet connection", description
  "Adds speed only when each is its own internet line - a phone, another
  router. A phone's data counts against its plan."; the ticks under it via
  `_row_more` (one QCheckBox per line: "Wi-Fi (192.168.1.5)", plus " - same
  router as <name>: adds no speed"), enabled only while the switch is on;
  "No second connection found" when fewer than two. Not in EVERYDAY. Drawer
  Overview: `lines.describe(t.line_conns)` while downloading on two or more
  lines, else today's number.
- [ ] Run new tests + whole suite; render Settings -> Network off screen and
  look at it.
- [ ] Commit `feat: Settings picks the internet connections a download uses`.

### After the tasks

Review the whole change (working rule). Real check needs the user: the phone
tethered beside the Wi-Fi, the same big file on one line and on two.
