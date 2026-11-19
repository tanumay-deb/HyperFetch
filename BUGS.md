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

## B8 - Must - A download through a site that sends each request to a random mirror fails "The file changed on the server"

- **First try (Gemini, local commit "fix: send If-Range (ETag or
  Last-Modified) on ranged requests and error if server file changed (B8)",
  not pushed):** the first probe keeps `t.etag` and `t.last_modified`; every
  ranged request to the first link sends `If-Range` (a strong ETag, else
  Last-Modified); a 200 answer stops the download: ERROR "The file changed
  on the server - Restart to download it again". Its tests in
  `tests/test_server_habits.py`:
  `test_resume_fails_when_the_file_changed_at_the_same_size_on_the_server`,
  `test_weak_etag_uses_last_modified_for_if_range`. Keep them.
- **What is wrong with it:** mirror redirectors send each request of one
  link to a mirror of their choosing, and mirrors running other server
  software give the SAME file another ETag. Measured 2026-11-19 on
  `download.fedoraproject.org` (Fedora's MirrorManager): 8 requests for one
  file went to 6 mirrors; most answered ETag `"6720fe49-429"` (nginx's form),
  `ftp-stud.hs-esslingen.de` answered `"429-6259f30189361"` (Apache's form) -
  same Last-Modified, same size. `If-Range: "6720fe49-429"` sent to the
  Apache mirror: 200 with the whole file; to an nginx mirror: 206. The first
  try sends If-Range on the parts of the FIRST run too, so such a download
  fails at once. The real engine on
  `.../releases/41/Everything/x86_64/iso/Fedora-Everything-netinst-x86_64-41-1.4.iso`,
  8 connections: with the first try, ERROR "The file changed on the server"
  at 384 KB, 2 runs of 2; without it, 46 MB in 25 s and no error, 2 of 2
  (scratchpad harness `gem2/fedora_ifrange.py <code tree>`). The same goes
  for other redirectors (mirrorbits, MirrorBrain: VideoLAN, LibreOffice and
  others) and any host whose servers make their own ETags.
- **Expected:**
  1. No If-Range in the run that probed: the file was just looked at. Only
     a resume (a run that starts with segments) sends it.
  2. The validator is Last-Modified when the server gave one (mirrors copied
     with rsync keep the file's time; ETag formats differ by server
     software); the ETag only when there is no Last-Modified; never a weak
     ETag.
  3. B8's own case (same size, new ETag AND new time) still ends in that
     ERROR.
  Known limit, accepted: a resume through a redirector whose mirrors give
  no Last-Modified and ETags of different forms still reads as changed.
- **Test:** a local "redirector" that sends each GET to one of two local
  mirrors holding the same bytes, the same Last-Modified and DIFFERENT ETags
  (each honours If-Range against its own ETag and Last-Modified): a fresh
  download completes; a paused one resumes and the file is whole. The first
  try fails this test. The two tests above still pass.

## B9 - Should - A PC whose internet goes through a Hyper-V virtual switch loses its line

- **First try (Gemini, local commit "fix: exclude VPN and virtual adapters
  from lines.available() by IfType and Description (B9)", not pushed):**
  `lines.is_vpn_or_virtual` keeps VPN, tunnel and virtual adapters out of
  `available()` by `IfType` (53 virtual, 131 tunnel, 24 loopback; PPP by its
  description) and by `Description` (`_VPN_DESC_RE`). TAP, Wintun, WireGuard
  and OpenVPN adapters with a gateway are no longer lines - keep that, and
  its tests: `tests/test_lines.py::test_vpn_and_virtual_adapters_are_excluded_but_pppoe_is_kept`,
  `tests/test_multi_line.py::test_a_vpn_adapter_with_a_gateway_is_not_a_line_and_blocks_multi_line`.
- **What is wrong with it:** `_VPN_DESC_RE` also matches
  `hyper-v|vmware|virtualbox|virtual\s+ethernet`. With a Hyper-V EXTERNAL
  virtual switch, Windows binds the physical network card to the switch, and
  the PC itself reaches the internet through "vEthernet (<switch name>)":
  Description "Hyper-V Virtual Ethernet Adapter", IfType 6, with the default
  gateway. That is the PC's real line, and it is now excluded: no line is
  left, and `_pick_lines`' route check sees the route leave from an address
  not in `available()` and refuses lines as if a VPN held it. Read from how
  Hyper-V's external switch works; this PC has only WSL's internal switch
  (no gateway, so not a line either way). The host-side adapters that are
  NOT the PC's connection - Hyper-V internal / Default Switch, VMware VMnet1
  and VMnet8, VirtualBox Host-Only - report no gateway, so the gateway rule
  already keeps them out.
- **Expected:** take the hypervisor words (`hyper-v`, `vmware`,
  `virtualbox`, `virtual\s+ethernet`) out of `_VPN_DESC_RE`. The VPN names
  and the IfType rules stay.
- **Test:** `lines._adapters` fed a "Hyper-V Virtual Ethernet Adapter"
  (IfType 6, up, an IPv4 address, a gateway): it is in `available()`. The
  first try fails this test; its two tests still pass.
