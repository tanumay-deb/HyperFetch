"""The window counts connections, not unfinished parts.

Found 2026-11-01: the sidebar, and the drawer's Overview and Connections tab,
counted a download's parts not yet finished. A file is cut in up to 32 and a
server that serves two at once keeps it on two, so it read 8 or 32 while two
were open. The engine now says how many stream (task.http_conns), and that
is what is shown.
"""
import threading
import time

import task as T
from downloader import Downloader


def test_what_is_shown_is_what_streams(media_server, tmp_path):
    media_server.put("big.bin", b"x" * 16_000_000)          # 8 parts
    media_server.limit, media_server.refuse = 2, "429"      # serves two at once
    media_server.rate = 4_000_000                           # about two seconds
    t = T.DownloadTask(media_server.url("big.bin"), str(tmp_path / "big.bin"))
    seen, done = [], threading.Event()

    def watch():
        while not done.is_set():
            seen.append((t.http_conns, sum(1 for s in t.segments if not s.complete)))
            time.sleep(0.01)

    w = threading.Thread(target=watch)
    w.start()
    try:
        Downloader(t, segments=8).run()
    finally:
        done.set()
        w.join()
    assert t.status == T.COMPLETED and len(t.segments) == 8
    assert t.http_conns == 0, "a finished download has none open"
    assert 1 <= max(s for s, _ in seen) <= 2
    assert any(p > 2 and s for s, p in seen), "the old count: parts, not connections"
