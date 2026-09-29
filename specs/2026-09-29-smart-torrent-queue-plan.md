# Smart torrent queue — implementation plan

> For agentic workers: REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use
> checkbox (`- [ ]`) syntax. Compact on purpose (the user asked for short plans).

**Goal:** implement `specs/2026-09-29-smart-torrent-queue-design.md`: stuck
torrents give way, seeders pick the next one (Part C), and trackers are asked
about queued torrents (Part A).

**Architecture:** pure health rules in a new `swarm.py`; `torrent.py` observes
and yields; `queue_manager.py` decides who waits and who starts; `gui2/notify.py`
says what happened; a new `scrape.py` holds the tracker client and the
`SwarmScout` thread.

**Tech:** Python 3.10+, aria2 JSON-RPC (existing), UDP BEP 15 and HTTP scrape,
PySide6, pytest.

## Files

| File | Change |
|---|---|
| `swarm.py` (new) | `NO_SEED_YIELD`, `FRESH_FOR`, `known_seeds`, `health_rank`, `is_known_dead` |
| `task.py` | fields `last_seeds`, `last_seeds_at`, `swarm_seeds`, `swarm_peers`, `swarm_at` (saved); `yield_reason`, `yielded_at` (runtime) |
| `torrent.py` | record `last_seeds*`; `_note_no_seeds`; `_better_waiting`; `_stuck_reason`; `_yield_slot(..., reason)` |
| `queue_manager.py` | health-aware `_next_ready`; `_live_candidate_waiting`; `task._better_waiting` hook in `_execute` |
| `gui2/notify.py` | `YieldNotifier` |
| `gui2/app.py` | yield notes (toast + tray); start and stop `SwarmScout` |
| `gui2/download_card.py` | `_queue_state` names the reason and the swarm counts |
| `scrape.py` (new) | `udp_scrape`, `http_scrape`, `trackers_for`, `SwarmScout` |
| settings (`settings_pages.py`, `settings.py`) | Torrents: "Check seeders of queued torrents" (`scrape_trackers`) |
| `PRIVACY.md`, `docs/privacy.html` | one line on scraping |

## Part C

- [ ] **1. Health rules and fields.** Tests `tests/test_swarm_health.py`: the newer of
  `last_seeds`/`swarm_seeds` wins; older than 24 h is unknown; rank order is
  seeds>0 (most first) < unknown < known 0; the fields survive `to_dict`/`from_dict`.
  Red, then implement `swarm.py` and the `task.py` fields, then green.
- [ ] **2. torrent.py.** Tests in `tests/test_stall_yield.py`:
  - `_note_no_seeds(0, same)` is True after 300 s; seeds > 0 is never; new bytes
    reset the clock.
  - `_stuck_reason()` gives "no peers" (unchanged rule); "no seeders" only when
    `_better_waiting()` is True; None when the hook is missing or False.
  - `_yield_slot(..., "no seeders")` sets `yield_reason` and `yielded_at`.
  - The poll records `last_seeds*`.

  Implement it, and have the poll loop call `_stuck_reason()` in place of the
  `_note_stall` call.
- [ ] **3. queue_manager.py.** Tests `tests/test_queue_health.py`:
  - `_next_ready` starts the torrent with the most seeders over a higher-priority
    dead one;
  - unknown before known 0;
  - ties by priority, then order;
  - a web download first in line still goes first;
  - each queue ranks only its own torrents;
  - `_live_candidate_waiting` excludes known-dead, backed-off, other-queue and
    self.

  Implement `_next_ready` over `sorted(self._heap)` (the heap's own order), a
  health pick among ready torrents of the chosen one's queue, and the
  `_better_waiting` hook set and cleared in `_execute`.
- [ ] **4. GUI.** Tests:
  - `YieldNotifier` announces each yield once, batches several, and names a
    torrent that started in the same window;
  - the card says "Paused — no seeders, retrying in …" for that reason, and keeps
    "Stalled — no peers" otherwise.

  Wire the toast and tray note into `refresh()`.
- [ ] **5.** Full suite plus `--selftest`; commit Part C; push.

## Part A

- [ ] **6. scrape.py client.** `tests/test_scrape.py` with an in-process fake UDP
  tracker (connect id, then scrape, multiple infohashes) and a fake HTTP tracker
  (bencoded `files`). Covers garbage replies, timeouts, and the 70-hash batch
  split. `trackers_for(task)`: its own (magnet `tr=` / `.torrent` announce-list),
  then `PUBLIC_TRACKERS`, deduplicated, at most 8.
- [ ] **7. SwarmScout.** Tests with injected scrape functions:
  - candidates are QUEUED/PAUSED/SCHEDULED torrents with a known infohash;
  - never-checked ones go within about 10 s, the rest every 15 min;
  - infohashes are grouped per tracker; the most seeders/peers wins;
  - nothing is overwritten when no tracker answers;
  - a tracker that fails 3 times in a row is skipped for 1 h;
  - off means no scrapes.
- [ ] **8. Wiring.**
  - The app starts and stops the scout, gated by `scrape_trackers`.
  - The settings switch.
  - The card reads "Queued · 12 seeds · 40 peers · checked 3 min ago".
  - The privacy line.
  - If `scrape` is imported lazily, add it to `HyperFetch.spec`.
  - Full suite plus `--selftest`; commit Part A; push; watch CI.
