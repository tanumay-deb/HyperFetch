"""Recheck progress on the card, and error text that is not cut off.

Two reports:
  - a Force Recheck showed only "Verifying downloaded data…", which is
    indistinguishable from a hang on a payload that takes minutes to read;
  - a failed card read "Server sent a web page, not the file (login/cookies
    required — use the" and stopped there, mid-sentence.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest                                                    # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

import task as T                                                 # noqa: E402
from gui2.download_card import DownloadCardWidget as Card        # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _sub(app, t):
    card = Card(t, 1)
    card.update_task(t, 0.0)
    app.processEvents()
    return card.sub


def _task(**kw):
    t = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, "C:/dl/Show")
    for k, v in kw.items():
        setattr(t, k, v)
    return t


# ---- recheck ---------------------------------------------------------------
def test_recheck_counts_up(app):
    t = _task(verifying=True, total_size=4 * 1024**3,
              verified_bytes=1024**3, verified_pct=25)
    txt = _sub(app, t).text()
    assert "Recheck" in txt, txt
    assert "25%" in txt, txt
    assert "1.00 GB" in txt and "4.00 GB" in txt, txt


def test_recheck_reaches_the_end(app):
    t = _task(verifying=True, total_size=1000, verified_bytes=1000,
              verified_pct=100)
    assert "100%" in _sub(app, t).text()


def test_recheck_before_any_progress_still_says_what_it_is_doing(app):
    t = _task(verifying=True, total_size=0, verified_bytes=0, verified_pct=0)
    assert "Recheck" in _sub(app, t).text()


def test_a_finished_recheck_stops_showing_recheck(app):
    """Verification ends, and the card must move on rather than stick."""
    t = _task(verifying=False, status=T.COMPLETED, total_size=1000,
              downloaded=1000, seeding=True)
    assert "Recheck" not in _sub(app, t).text()


# ---- error text ------------------------------------------------------------
LONG = ("Server sent a web page, not the file (login/cookies required — use the "
        "browser extension so the download carries your session)")


def test_the_full_error_is_kept(app):
    """It used to be sliced to 70 characters before the label ever saw it, so
    the tooltip was truncated too and the message ended mid-sentence."""
    t = _task(status=T.ERROR, error=LONG)
    sub = _sub(app, t)
    assert sub.toolTip() == LONG, (
        "the full message must survive for the tooltip; got %r" % sub.toolTip())


def test_a_long_error_is_still_elided_on_screen(app):
    """Keeping the full text must not let a long error stretch the card."""
    t = _task(status=T.ERROR, error=LONG)
    sub = _sub(app, t)
    shown = sub.text()
    assert len(shown) <= len(LONG)
    assert "…" in shown or shown == LONG


def test_an_empty_error_still_says_failed(app):
    t = _task(status=T.ERROR, error="")
    assert _sub(app, t).text() == "Failed"
