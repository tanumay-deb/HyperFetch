"""A server that serves only so many connections at once.

Measured before this change, against a stand-in CDN that serves two
connections and refuses a third (60 MB, 24 Mbit/s per connection, 8 segments;
one connection takes 20 s):
  refused with 429     the gate settled at two: 10.1 s
  refused with a reset 38.6 s - twice as long as one connection. Each reset
                       halved the gate and slept 2, 4, 8, 16 s before retrying
  refused with 403     the task failed, "URL expired", though the link was
                       being served on the other two connections the whole time

A refusal while another of the download's connections is streaming is the
server saying "no more than these", not "no": the engine now settles at the
connections it is being served and retries the refused range when one frees,
without spending its retries. A link refused with nothing streaming fails
exactly as before.
"""
import time

import pytest

import task as T
import utils
from downloader import Downloader

SIZE = 6 * 1024 * 1024
RATE = 1.5 * 1024 * 1024          # bytes/s per connection: one connection takes 4 s


@pytest.mark.parametrize("refuse", ["429", "reset", "403"])
@pytest.mark.parametrize("link", ["file", "video"])
def test_a_connection_limit_is_met_at_the_limit(media_server, make_payload, tmp_path,
                                                refuse, link):
    """"file": a link to the file itself. "video": a video page's link, as
    yt-dlp hands it over (run_media), which opens one connection at a time."""
    data = media_server.put("clip.mp4", make_payload(SIZE))
    media_server.rate, media_server.limit, media_server.refuse = RATE, 2, refuse
    url = media_server.url("clip.mp4")

    t0 = time.monotonic()
    if link == "file":
        t = T.DownloadTask(url, str(tmp_path / "clip.mp4"))
        Downloader(t, segments=8).run()
    else:
        t = T.DownloadTask(media_server.url("watch/1"), str(tmp_path / "clip.mp4"))
        eng = Downloader(t, segments=8, url=url)
        assert eng.run_media()
    took = time.monotonic() - t0

    if link == "file":
        assert t.status == T.COMPLETED, t.error
    else:
        # the file is whole; Completed is yt-dlp's to say, once it is done with it
        assert eng.media_done and t.status == T.DOWNLOADING, t.error
    assert open(t.save_path, "rb").read() == data
    assert media_server.stats["max_active"] == 2
    one_connection = SIZE / RATE
    assert took < 0.85 * one_connection, (
        "%.1f s: no faster than one connection (%.1f s)" % (took, one_connection))
    # The gate grows back as streams are accepted, so it asks past the limit
    # now and then: about once a range (8 or 9 here when all goes to plan),
    # and the opening burst can cost one or two more on a busy machine - 9
    # was seen in a full run on Windows. Never in a loop: ranges retrying
    # every PUSHBACK_WAIT without the gate would be refused 60 times and more.
    refused = media_server.stats["refused"]
    assert refused <= 16, "the gate hammered the server: %d refusals" % refused


def _share_with(timeline, n):
    """The part of the transfer, from its first connection to its last, that
    the server spent sending on n connections or more."""
    with_n = sum(b[0] - a[0] for a, b in zip(timeline, timeline[1:]) if a[1] >= n)
    return with_n / (timeline[-1][0] - timeline[0][0])


def test_connections_out_of_step_both_keep_streaming(media_server, make_payload,
                                                     tmp_path):
    """The server counts a connection a moment past its last byte, as one
    that tracks connections does, so a range asked for as one of ours ends is
    refused on that one's account. A resumed download has its connections out
    of step: its first range is three quarters done and ends while the next
    one streams.

    Before, such a refusal took the gate down to the one connection
    streaming, and the gate grows only as a stream is accepted - none could
    be while its one slot was in use. The refused range waited for that one
    to end; at that end the next range got in, the one after it was refused
    on the account of the one just ended, and so on to the last range: the
    server sent on two connections for 8-10% of the transfer, every run,
    and it took 3.3 s. Now 40-44% and 2.6-2.7 s (a 15.6 ms clock, as on
    Windows, gives the lower figures). What is counted is the server's
    connections over time, not the wall clock.
    """
    data = media_server.put("clip.mp4", make_payload(SIZE))
    media_server.rate, media_server.limit, media_server.refuse = RATE, 2, "429"
    media_server.linger = 0.05
    t = T.DownloadTask(media_server.url("clip.mp4"), str(tmp_path / "clip.mp4"))
    part = SIZE // 6
    t.total_size, t.supports_range = SIZE, True
    t.segments = [T.Segment(i, i * part, (i + 1) * part - 1) for i in range(6)]
    t.segments[0].downloaded = done = part * 3 // 4
    temp = utils.temp_download_path(t.id)
    utils.allocate_file(temp, SIZE)
    with open(temp, "r+b") as f:
        f.write(data[:done])

    Downloader(t, segments=8).run()

    assert t.status == T.COMPLETED, t.error
    assert open(t.save_path, "rb").read() == data
    assert media_server.stats["max_active"] == 2
    two = _share_with(media_server.timeline, 2)
    assert two > 0.25, "two connections for only %.0f%% of the transfer" % (two * 100)
    refused = media_server.stats["refused"]
    assert refused <= 16, "the gate hammered the server: %d refusals" % refused


def test_a_link_refused_with_nothing_streaming_still_fails_at_once(media_server,
                                                                   make_payload, tmp_path):
    """An expired or forbidden link: no connection is ever served, so a 403 is
    the link's answer, and the task says so without retrying."""
    media_server.put("clip.mp4", make_payload(SIZE))
    media_server.tokens = set()                 # every request is answered 403
    t = T.DownloadTask(media_server.url("clip.mp4"), str(tmp_path / "clip.mp4"))

    t0 = time.monotonic()
    Downloader(t, segments=8).run()

    assert t.status == T.ERROR
    assert "403" in t.error
    assert time.monotonic() - t0 < 3, "a dead link was retried"
