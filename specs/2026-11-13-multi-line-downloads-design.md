# Combining internet connections ("lines") - design

Asked 2026-11-13: "implement combining several internet connections". The
second connection would be a phone over USB tethering, or a cable to a second
router, beside the Wi-Fi. The user picked approach 1 below.

## Intent

The home line is one Airtel connection (Wi-Fi; about 93 Mbit/s measured on
2026-11-12; CGNAT). With a second line plugged in, one big download should use
both at once and finish in roughly the time their speeds added together allow.
The idea is plexo's (github.com/anmolkapil/plexo, parked in CLAUDE.md since
2026-10-30): tie each of a download's connections to one network adapter.

## Goals

- One plain HTTP(S) download spreads its connections over the chosen lines,
  and the faster line ends up fetching more of the file.
- Off by default. Settings turns it on and picks the lines.
- A line that goes away mid-download (phone unplugged, tethering off) costs
  nothing but its speed: the rest comes over the other lines, no error.

## Non-goals (this version)

- Torrents (aria2), streams (HLS) and yt-dlp downloads keep one line. Torrents
  come later, after measuring aria2's `--multiple-interface` with peers.
- Downloads through the relay ("Get past provider blocks") or a proxy keep one
  line: they connect to one place.
- Only IPv4 lines; a host with no IPv4 address keeps one line.
- Windows only. Binding a connection to an adapter's address routes it out of
  that adapter on Windows; Linux needs SO_BINDTODEVICE or policy routing.
- No per-line speed limit or mobile-data cap.

## Anti-goals

- Turning it on must never make a download fail that one line would finish.
- Off - or with one line - nothing changes: the same parts, connections,
  session and code path as today.
- No new dependency.

## Facts and constraints

- A second adapter adds speed only when it is a different internet line. A
  cable to the SAME Airtel router as the Wi-Fi is the same line: no gain. The
  phone (mobile data) or a second router on another provider is.
- Windows (strong host model) sends a connection bound to an adapter's address
  out of that adapter when the adapter has its own default gateway; a tethered
  phone and a second router give one by DHCP. To be measured on the real lines
  before release.
- This PC now: Wi-Fi (gateway 192.168.1.1); Tailscale and WSL have no gateway,
  so they are not lines.
- The engine today (`downloader.py`): a download is cut into parts
  (`parts_for`), one thread each; a gate lets as many connections run as there
  are parts (`_transfer`: `_conn_cap = len(segments)`), opening them slowly
  at first (`first`, 4) and halving on 429s/resets. With one line parts equal
  connections, so nothing waits for a free connection.

## Approaches

1. **Split each download across the lines (picked).** Each time a connection
   starts a part, it goes out on the line with the fewest connections running.
   With two or more lines the file is cut into more parts than connections, so
   a line that finishes its parts sooner takes the next ones: the faster line
   fetches more. One download gets both lines' speed. It changes the core
   engine, so it needs the most testing.
2. **Whole downloads on different lines.** Simpler, but one big download is no
   faster. Not picked.
3. **Torrents too, through aria2's `--multiple-interface`.** Unverified for
   peer connections; measured later, not built now.

## Design

**Lines (`lines.py`, new).** `available()` lists the adapters that are up and
have an IPv4 address and a default gateway, as (Windows' name, address) -
from Windows' `GetAdaptersAddresses` through ctypes: fast, no dependency, and
not dependent on the system language (as `ipconfig` text is). `addresses(names)`
gives the chosen adapters' addresses as they are now (DHCP moves them), read at
each download's start.

**Settings -> Network.** A switch "Use more than one internet connection"
(extras `multi_line`, off) and, under it, the lines found, each with a tick
(extras `lines`: adapter names; all ticked when first switched on). A line of
text: "Adds speed only when each is its own internet line - a phone, another
router. A phone's data counts against its plan." Saved into `utils.LINES` (the
ticked names, or empty when off).

**Engine (`downloader.py`).** Only when two or more ticked lines have an
address now, and the download is plain HTTP(S), takes ranges, and goes neither
through the relay nor a proxy:

- one session per line, its connection pool bound to that line's address
  (urllib3 `source_address`); the browser's cookies copied into each;
- parts: `max(parts_for(...), 4 x connections x lines)`, no part under 1 MiB;
  each line may run the download's connection count (adding a line never
  takes connections from the first); the gate's cap is connections x lines;
  the slow start stays as it is;
- each request takes the line with the fewest connections running (ties: the
  order in Settings), counted per line;
- a line whose requests fail twice in a row without streaming (no route,
  unplugged) is dropped for this run, with a line in the download's log; its
  parts go to the others. If every line drops, the download goes on unbound,
  as today;
- the log says at the start which lines it uses; the drawer's Overview shows
  connections per line ("Wi-Fi 6 · Phone 4");
- a resume reads the lines afresh; parts are saved as today (more of them).

Mirrors ("Add another link…") still pick the link; the line is picked apart
from it. The speed limit still paces every byte through the one bucket.

## Tests

- A local server reached as two lines (127.0.0.1 and 127.0.0.2 as source
  addresses), one throttled by client address: the file arrives whole, both
  addresses are served, the faster one serves more parts.
- A line that cannot connect is dropped and the download finishes on the other.
- Switched off, or one line: the parts and the session are exactly today's
  (the existing engine tests unchanged).
- `available()`: a fake adapter table; on Windows, the real call lists the
  adapter of the default route.
- Settings: the switch and ticks reach extras and `utils.LINES`.
- Real, before release: the user's tethered phone beside the Wi-Fi, the same
  148 MB file on one line and on two.

## Left to the builder

The ctypes structures; the log and drawer wording; how the per-line counts
reach the drawer.
