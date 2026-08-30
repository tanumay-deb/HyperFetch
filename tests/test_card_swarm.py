"""Peers/seeds on the card, and what the sidebar total counts.

Requested: show peers and seeds on a torrent card whenever it is active or
seeding; and keep a seeding torrent's peers OUT of the sidebar total, since
those are people downloading from us, not sources we are pulling from.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest                                        # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

import task as T                                     # noqa: E402
from gui2.download_card import DownloadCardWidget as DownloadCard  # noqa: E402
from gui2.download_card import _swarm                # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _tor(status=T.DOWNLOADING, total=0, peers=0, seeds=0, seeding=False):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Show", "C:/dl/Show")
    t.status = status
    t.total_size = total
    t.downloaded = total if seeding else 0
    t.tor_conns, t.tor_seeds = peers, seeds
    t.seeding = seeding
    return t


def _sub(app, t):
    card = DownloadCard(t, 1)
    card.update_task(t, 0.0)
    app.processEvents()
    return card.sub.text()


def test_metadata_phase_shows_the_swarm(app):
    """"Fetching metadata…" alone cannot distinguish talking-to-peers from
    found-nobody — the exact question asked of a stalled queue."""
    txt = _sub(app, _tor(total=0, peers=5, seeds=2))
    assert "Fetching metadata" in txt
    assert "5 peers" in txt and "2 seeds" in txt, txt


def test_downloading_shows_the_swarm(app):
    txt = _sub(app, _tor(total=1000, peers=12, seeds=3))
    assert "12 peers" in txt and "3 seeds" in txt, txt


def test_seeding_shows_peers_and_seeds(app):
    t = _tor(status=T.COMPLETED, total=1000, peers=4, seeds=1, seeding=True)
    txt = _sub(app, t)
    assert "Seeding" in txt
    assert "4 peers" in txt and "1 seed" in txt, txt


def test_zero_is_shown_not_hidden(app):
    """0 peers is information, not an empty state."""
    txt = _sub(app, _tor(total=0, peers=0, seeds=0))
    assert "0 peers" in txt and "0 seeds" in txt, txt


@pytest.mark.parametrize("p,s,expect", [
    (1, 1, "1 peer · 1 seed"),
    (2, 0, "2 peers · 0 seeds"),
    (0, 3, "0 peers · 3 seeds"),
])
def test_singular_and_plural(p, s, expect):
    assert _swarm(_tor(peers=p, seeds=s)) == expect


# ---- sidebar total ---------------------------------------------------------
def _sidebar_total(tasks):
    """Mirror the accumulation in DownloadAppV2.refresh()."""
    import torrent as _torrent
    conns = seeds = 0
    for t in tasks:
        if t.status == T.DOWNLOADING and _torrent.is_torrent_task(t.url, t.filename):
            if not getattr(t, "seeding", False):
                conns += getattr(t, "tor_conns", 0)
                seeds += getattr(t, "tor_seeds", 0)
    return conns, seeds


def test_a_seeding_torrent_is_left_out_of_the_total():
    downloading = _tor(total=1000, peers=7, seeds=2)
    seeding = _tor(status=T.DOWNLOADING, total=1000, peers=99, seeds=40, seeding=True)
    assert _sidebar_total([downloading, seeding]) == (7, 2), (
        "a seeding torrent's peers are downloading FROM us and must not inflate "
        "the total")


# --- the taskbar should glow when something finishes ------------------------
def test_flash_only_when_the_window_is_not_active(app, monkeypatch):
    """Flashing a window the user is already looking at is just noise."""
    from gui2.app import DownloadAppV2
    from PySide6.QtWidgets import QApplication as _QA

    calls = []
    monkeypatch.setattr(_QA, "alert", staticmethod(lambda w, m=0: calls.append(m)))

    win = DownloadAppV2.__new__(DownloadAppV2)
    monkeypatch.setattr(type(win), "isActiveWindow", lambda self: True, raising=False)
    win._flash_taskbar()
    assert calls == [], "flashed while the window was already focused"

    monkeypatch.setattr(type(win), "isActiveWindow", lambda self: False, raising=False)
    win._flash_taskbar()
    assert calls and calls[0] > 0, "no taskbar flash when the window was in the background"
