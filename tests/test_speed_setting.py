"""The download speed limit can be any number, not one of three.

Settings offered Unlimited, 1, 5 and 10 Mb/s, and the scheduled limit 1, 2, 5
and 10. On a 300 Mb/s line none of them is "leave me a third of it". Both
boxes take a typed value now, read by one function.
"""
import inspect

import pytest

import utils
from gui2 import app_settings, app_system
from gui2.dialogs import settings_pages

MBIT = 1000 * 1000 // 8                 # one megabit a second, in bytes


@pytest.mark.parametrize("text,bps", [
    ("5 Mb/s", 5 * MBIT),               # what the list has always held
    ("25", 25 * MBIT),                  # a bare number is megabits, like the list
    ("2.5 Mb/s", 2 * MBIT + MBIT // 2),
    ("  40  mb/s ", 40 * MBIT),
    ("12mbps", 12 * MBIT),
    ("500 Kb/s", 500 * 1000 // 8),
    ("1 Gb/s", 1000 * MBIT),
    ("5 MB/s", 40 * MBIT),              # a capital B is bytes: eight times as much
    ("Unlimited", 0),
    ("", 0),
    (None, 0),
    ("0", 0),
    ("-5", 0),
    ("fast", 0),
])
def test_what_a_typed_limit_means(text, bps):
    assert utils.parse_speed(text) == bps


@pytest.mark.parametrize("typed,shown", [
    ("25", "25 Mb/s"),
    ("2.50 mb/s", "2.5 Mb/s"),
    ("5 Mb/s", "5 Mb/s"),
    ("500 kb/s", "500 Kb/s"),
    ("1 gb/s", "1 Gb/s"),
    ("5 MB/s", "40 Mb/s"),
    ("0", "Unlimited"),
    ("nonsense", "Unlimited"),
    ("", "Unlimited"),
])
def test_it_is_kept_the_way_the_list_writes_it(typed, shown):
    assert utils.speed_text(typed) == shown


def test_what_is_kept_reads_back_as_the_same_speed():
    for typed in ("25", "2.5 Mb/s", "500 Kb/s", "1 Gb/s", "0.3"):
        assert utils.parse_speed(utils.speed_text(typed)) == utils.parse_speed(typed), typed


def test_both_boxes_can_be_typed_into():
    page = inspect.getsource(settings_pages.PageBuilderMixin._p_downloads)
    for box in ("self.speed_limit", "self.thr_limit"):
        line = next(ln for ln in page.splitlines() if ln.strip().startswith(box + " ="))
        assert "editable=True" in line, f"{box} still only offers its list"


def test_a_saved_value_that_is_not_in_the_list_is_shown():
    """Typed once, it has to be there the next time Settings opens."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    box = settings_pages.PageBuilderMixin._combo(None, ["Unlimited", "5 Mb/s"], "37 Mb/s",
                                                 editable=True)
    assert box.isEditable() and box.currentText() == "37 Mb/s"
    # A box sizes itself to its list, and gives up the arrow's share of that
    # once it can be typed into. Seen by drawing the page: a saved "2.5 Mb/s"
    # showed as "5 Mb/s", the end of the text in a box too narrow for it.
    assert box.minimumWidth() >= 140
    assert box.lineEdit().cursorPosition() == 0, "shows the end of its value, not the start"
    plain = settings_pages.PageBuilderMixin._combo(None, ["a", "b"], "zzz")
    assert not plain.isEditable() and plain.currentText() == "a"
    del app


def test_the_window_reads_both_with_the_one_function():
    applied = inspect.getsource(app_settings.SettingsMixin._apply_settings)
    assert 'utils.parse_speed(v.get("speed_limit"))' in applied
    assert 'int(v["speed_limit"].split()[0])' not in applied


class _Window(app_system.SystemMixin):
    def __init__(self, extras, limit=0):
        self._extras = extras
        self.global_speed_limit = limit


def test_the_scheduled_limit_takes_a_typed_value(monkeypatch):
    monkeypatch.setattr(app_system, "_in_window", lambda a, b: True)
    w = _Window({"throttle_enabled": True, "throttle_limit": "2.5"}, limit=9 * MBIT)
    assert w._throttle_bps() == utils.parse_speed("2.5 Mb/s")


def test_outside_its_hours_the_ordinary_limit_holds(monkeypatch):
    monkeypatch.setattr(app_system, "_in_window", lambda a, b: False)
    w = _Window({"throttle_enabled": True, "throttle_limit": "2"}, limit=9 * MBIT)
    assert w._throttle_bps() == 9 * MBIT


def test_a_scheduled_limit_nobody_can_read_does_not_lift_the_limit(monkeypatch):
    monkeypatch.setattr(app_system, "_in_window", lambda a, b: True)
    w = _Window({"throttle_enabled": True, "throttle_limit": "soon"}, limit=9 * MBIT)
    assert w._throttle_bps() == 9 * MBIT
