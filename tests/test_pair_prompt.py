"""The window's question when a Firefox install asks to pair (pairing.py).

It must show the code the extension shows - that is how the person tells
HyperFetch's request from another add-on's - and every way of not saying yes
(Don't allow, closing it, a stray Enter) must be a no.
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

import pairing
from gui2.dialogs.pair import show_next

FF = "moz-extension://2f1d6c3e-8a4b-4c1f-9d2e-7b6a5c4d3e21"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _asking(tmp_path):
    r = pairing.PairRequests(str(tmp_path / "paired_browsers.json"))
    assert r.request(FF, "4821") == "pending"
    return r


def test_nothing_waiting_asks_nothing(qapp, tmp_path):
    r = pairing.PairRequests(str(tmp_path / "paired_browsers.json"))
    assert show_next(None, r) is None


def test_the_question_shows_the_code_and_allow_pairs(qapp, tmp_path):
    r = _asking(tmp_path)
    dlg = show_next(None, r)
    shown = " ".join(l.text() for l in dlg.findChildren(QLabel))
    assert "4821" in shown.replace(" ", ""), "the code the extension shows is not on the question"
    dlg.accept()
    assert r.request(FF, "4821") == "approved"


@pytest.mark.parametrize("how", ["dont_allow", "close", "enter", "escape"])
def test_every_way_of_not_saying_yes_is_a_no(qapp, tmp_path, how):
    r = _asking(tmp_path)
    dlg = show_next(None, r)
    if how == "dont_allow":
        dlg.reject()
    elif how == "close":
        dlg.close()
    else:
        QTest.keyClick(dlg, Qt.Key_Return if how == "enter" else Qt.Key_Escape)
    qapp.processEvents()
    assert r.request(FF, "4821") == "denied", how


def test_one_question_per_request(qapp, tmp_path):
    r = _asking(tmp_path)
    first = show_next(None, r)
    assert show_next(None, r) is None, "asked twice about the same request"
    first.reject()


def test_settings_can_forget_the_paired_installs(qapp, tmp_path):
    """Allowing is not forever: Settings -> Browser takes it back, and each
    Firefox install then has to ask again."""
    from PySide6.QtWidgets import QPushButton
    from gui2.dialogs.settings import SettingsDialogV2

    r = pairing.PairRequests(str(tmp_path / "paired_browsers.json"))
    r.decide(FF, True)
    dlg = SettingsDialogV2(None, save_dir=str(tmp_path), max_concurrent=3, segments=8,
                           verify_tls=True, pair_token="T", theme="Dark", accent="purple",
                           sched_en=False, sched_start="00:00", sched_stop="06:00",
                           extras={}, pair_requests=r)
    forget = [b for b in dlg.findChildren(QPushButton) if b.text() == "Forget 1"]
    assert forget, "no way to forget the paired Firefox install"
    forget[0].click()
    assert r.count() == 0
    assert forget[0].text() == "None paired" and not forget[0].isEnabled()
    assert r.request(FF, "4821") == "pending", "a forgotten install must ask again"
    dlg.deleteLater()
