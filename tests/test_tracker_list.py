"""The public tracker list is fetched from maintained lists (trackers.py)."""
import torrent
import trackers

A = "udp://tracker.a.example:1337/announce"
B = "udp://tracker.b.example:6969/announce"
C = "https://tracker.c.example:443/announce"
D = "http://tracker.d.example/announce"


def _lists(*texts):
    texts = list(texts)
    return lambda url: texts.pop(0)


def test_only_plain_announce_addresses_are_taken():
    text = "\n".join([A, "", A, B + ",udp://evil.example:1/announce",
                      "udp://x.example:1/announce --on-download-complete=calc",
                      "file:///etc/passwd", "udp://[::1]:1/announce", C, "  " + D + "  "])
    assert trackers.parse(text) == [A, C, D]


def test_trackers_on_both_lists_come_first():
    assert trackers.pick([[A, B, C], [C, D, A]]) == [A, C, B, D]
    assert trackers.pick([[], [D]]) == [D]


def test_until_fetched_the_built_in_list_is_used():
    assert trackers.current() == torrent.PUBLIC_TRACKERS


def test_a_fetch_replaces_the_list_and_a_day_passes_before_the_next():
    assert trackers.refresh(_lists("\n".join([A, B, C]), "\n".join([C, D])), now=1000.0)
    assert trackers.current() == [C, A, B, D]
    assert not trackers.refresh(lambda url: 1 / 0, now=1000.0 + 3600)       # not due
    assert trackers.current() == [C, A, B, D]


def test_too_little_fetched_keeps_the_last_good_list_and_tries_in_an_hour():
    trackers.refresh(_lists("\n".join([A, B, C]), ""), now=0.0)
    assert not trackers.refresh(_lists(A, ""), now=trackers.EVERY + 1)
    assert trackers.current() == [A, B, C]
    assert trackers.refresh(_lists(D + "\n" + B, C), now=trackers.EVERY + 1 + trackers.RETRY)
    assert trackers.current() == [D, C, B]


def test_a_private_torrent_excludes_every_tracker_the_list_ever_held(tmp_path, monkeypatch):
    trackers.refresh(_lists("\n".join([A, B, C]), ""), now=0.0)
    trackers.refresh(_lists("\n".join([D, B, C]), ""), now=trackers.EVERY + 1)
    monkeypatch.setattr(torrent, "is_private_torrent", lambda p: True)
    t = type("T", (), {"url": "magnet:?xt=urn:btih:" + "a" * 40})()
    excluded = torrent.tracker_opts(t, "x.torrent")["bt-exclude-tracker"].split(",")
    assert {A, B, C, D} <= set(excluded)
    assert set(torrent.PUBLIC_TRACKERS + torrent.RETIRED_TRACKERS) <= set(excluded)


def test_the_daemon_is_told_a_new_list_once():
    calls = []

    class Daemon:
        port, secret = 1, "s"

        def call(self, method, opts):
            calls.append((method, opts))

    d = Daemon()
    torrent.tell_trackers(d)
    torrent.tell_trackers(d)
    assert calls == [("aria2.changeGlobalOption",
                      {"bt-tracker": ",".join(torrent.PUBLIC_TRACKERS)})]
    trackers.refresh(_lists("\n".join([A, B, C]), ""), now=0.0)
    torrent.tell_trackers(d)
    assert calls[-1] == ("aria2.changeGlobalOption", {"bt-tracker": ",".join([A, B, C])})
