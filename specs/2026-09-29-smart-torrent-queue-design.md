# Smart torrent queue — design

2026-09-29. Agreed in conversation, section by section: approach "C, then A";
stuck torrents are swapped automatically, with a note; seeders decide what starts
next, ahead of the priority setting.

## The problem

What exists today (`torrent.py`, `queue_manager.py`):

- A torrent has to earn its slot. After `STALL_YIELD` (180 s) with **no peers and
  no new bytes**, `_yield_slot` hands the slot back: the aria2 entry is removed
  (downloaded data and aria2's control file stay), the task goes back to QUEUED
  with `retry_after` set from `STALL_BACKOFF` (120, 300, 900 s), and the next task
  starts.
- Running torrents report live counts from `aria2.tellStatus`: `tor_conns`
  (connections) and `tor_seeds` (`numSeeders`), shown on the card as
  "8 peers · 0 seeds".

Two gaps:

1. A torrent connected to peers that have nothing it needs — **0 seeders, no new
   bytes** — never counts as stalled (`peers > 0` resets the clock), so it can sit
   at 40% for good while healthy torrents wait behind it.
2. The next torrent is chosen by priority and order alone, blind to swarm health.
   Queued torrents' seeders are unknown until they run, so the replacement may be
   another dead swarm, and each one costs minutes to rule out.

## Goals and non-goals

Goals: a stuck torrent gives way to one that can make progress; the queue starts
the healthiest waiting torrent first; queued torrents show their seeders.

Not in scope: web (HTTP) downloads; how aria2 finds or picks peers; pausing a
torrent that is receiving data, however slowly.

## Part C — stuck torrents give way; seeders pick the next one

### The "stuck" rules

- **No peers** (unchanged): no connections and no new bytes for 180 s → yield,
  whether or not anything is waiting (today's behaviour).
- **No seeders** (new): `tor_seeds == 0` and no new bytes for `NO_SEED_YIELD` =
  300 s → yield, but only when another torrent is waiting for a slot and is not
  *known dead* (below). With nothing better waiting, the torrent keeps its slot
  and keeps trying.
- **Progress protects:** any new bytes reset both clocks. A torrent trickling from
  peers with partial copies is never swapped out.
- Only live observations pause anything. Tracker counts (Part A) steer the order
  and the swap guard; they never pause a torrent by themselves.

### The swap

- A yield goes through the existing `_yield_slot`: progress kept on disk, back to
  QUEUED, `retry_after` from `STALL_BACKOFF`, `stall_count` + 1. The task records
  why: `yield_reason` = `"no peers"` or `"no seeders"`.
- The queue then starts the best waiting torrent (ordering below).
- **You are told:** the window notices each new yield and shows an in-app toast and
  a Windows notification: *Paused "X" — no seeders for 5 min. Trying again in
  2 min.* (or "no peers for 3 min"). If a torrent started in its place on the same
  refresh, the note adds *Started "Y" instead.* Several at once make one
  notification, the way "Download added" batches.

### Memory

While running, a torrent records `last_seeds` (the count from its latest poll)
and `last_seeds_at`. Both are saved with the download list, so the order survives
a restart.

### Who starts next

When `QueueManager._next_ready` would start a torrent, it starts the healthiest
ready torrent **of the same queue** instead of the first one in line. Web
downloads keep their place in line, and each named queue ranks only its own
torrents. The health order:

1. **known seeders > 0**, most first;
2. **unknown** — never observed, or the newest observation is older than 24 h;
3. **known 0 seeders**.

Ties: the priority you set, then the order they were added (the heap's order
today). A task's known count is the newer of `last_seeds` (Part C) and
`swarm_seeds` (Part A). Web downloads keep today's order untouched.

## Part A — ask the trackers about queued torrents

### The checker

A background thread, `SwarmScout` in a new `scrape.py`, built like
`MetadataPrefetcher`:

- **When:** every 15 min, and within ~10 s of a torrent being added.
- **Which:** QUEUED, PAUSED and SCHEDULED torrent tasks whose infohash is known
  (`torrent.infohash_for` — magnets, and `.torrent` files). Running torrents are
  skipped: aria2 reports them live.
- **Where:** each torrent's own trackers (the magnet's `tr=`, the `.torrent`'s
  announce list) and then `PUBLIC_TRACKERS`, at most 8 per torrent. Infohashes are
  grouped per tracker, so one request covers many torrents: a UDP scrape (BEP 15,
  up to 70 infohashes per packet) or an HTTP scrape (the announce URL with
  `announce` → `scrape`, several `info_hash` parameters). No swarm is joined and
  no payload is fetched.
- **Limits:** 5 s per tracker, one try; up to 4 trackers at a time; a tracker that
  fails 3 times in a row is skipped for an hour.
- **Result:** the highest seeders and leechers any tracker reported, saved as
  `swarm_seeds`, `swarm_peers`, `swarm_at` with the download list. When no tracker
  answers, earlier numbers stay and nothing is overwritten with "unknown".
- **Settled while building:**
  - A UDP tracker answers all zeros for a torrent it has never heard of, so an
    all-zero reply counts as no answer, not as 0 seeders: unknown is not dead.
  - A private torrent (`private=1`, BEP 27) is asked about only at its own
    trackers; its infohash never goes to the public ones.
  - The 8-per-torrent cap applies after resting trackers are skipped, so the next
    tracker in line stands in for a dead one.
  - Within its 5 s, a UDP request is sent once more halfway (UDP drops packets).
    HTTP scrapes use the proxy only when torrents do (`utils.PROXY_TORRENTS`).

### What you see

A queued or paused torrent's card reads *Queued · 12 seeds · 40 peers · checked
3 min ago*.

### How it is used

- It feeds the ordering above, which then has numbers for torrents that have never
  run.
- **Swap guard:** a torrent stuck by the no-seeders rule yields only to a waiting
  torrent that is not known dead, so one dead swarm is never swapped for another.

### Settings, network, privacy

- Settings → Torrents: **"Check seeders of queued torrents"**, on by default. Off:
  no scrapes; the ordering uses what torrents saw while running.
- Scrapes go direct, not through the proxy, as torrent traffic does
  (`utils.PROXY_TORRENTS`). Names resolve through the app's resolver, so the
  automatic secure-DNS fallback for blocked sites applies to trackers too.
- The privacy policy (`PRIVACY.md`, `docs/privacy.html`) gains a line: with the
  setting on, queued torrents' trackers are asked for their counts, so a tracker
  learns your IP address and which torrent you have queued a little earlier than
  it would once the torrent runs.

### Errors

A malformed or partial tracker reply is ignored for that tracker. No exception
leaves the thread. The thread is a daemon with a stop event, like the prefetcher,
and is stopped at shutdown.

### Packaging

If `scrape.py` is imported lazily, it goes in `HyperFetch.spec`'s hidden imports,
or the frozen build breaks (CLAUDE.md).

## Testing

- **Stuck rules** (fake clock): 0 seeders, peers connected, no bytes for 300 s →
  yields when a live candidate waits; keeps its slot when nothing waits, or only
  known-dead ones do; any new bytes reset the clock; the no-peers rule is
  unchanged.
- **Ordering** (`_next_ready`): most seeders first; unknown before known 0; ties by
  priority, then order; observations older than 24 h count as unknown; web
  downloads' order unchanged.
- **Scrape:** an in-process fake UDP tracker (BEP 15 connect + scrape) and a fake
  HTTP tracker (bencoded reply): batching, the 8-tracker cap, timeouts, garbage
  replies, failure backoff, the setting switched off.
- **Persistence:** `last_seeds*` and `swarm_*` survive `to_dict` / `from_dict`.
- **UI:** the queued card's text; the swap notification's text and batching.

## Order of work

Part C first, in its own commit; Part A after. Released with the next desktop
release, when that is asked for.
