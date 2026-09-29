"""The Windows notification when downloads are added (gui2/notify.py).

It is for downloads that arrive while you are not looking at HyperFetch: from
the browser, the web client, another launch, or with the window in the tray.
A burst - "Download all images" sends forty - is one notification, not forty.
"""
from types import SimpleNamespace

from gui2.notify import AddedNotifier


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _task(i, name=None):
    return SimpleNamespace(id="t%d" % i, filename=name if name is not None else "file%d.zip" % i,
                           url="https://x/file%d.zip" % i)


def _run(n, tasks, clock, looking=False, enabled=True):
    return n.tick(tasks, looking=looking, enabled=enabled)


def test_the_list_restored_at_startup_is_not_news():
    clock = _Clock()
    n = AddedNotifier(clock=clock)
    restored = [_task(1), _task(2)]
    assert _run(n, restored, clock) is None
    clock.t += 5
    assert _run(n, restored, clock) is None


def test_one_download_added_is_one_notification_after_a_short_quiet():
    clock = _Clock()
    n = AddedNotifier(clock=clock)
    tasks = [_task(1)]
    _run(n, tasks, clock)
    tasks.append(_task(2, "setup.exe"))
    assert _run(n, tasks, clock) is None, "waits a moment in case more follow"
    clock.t += AddedNotifier.QUIET
    assert _run(n, tasks, clock) == ("Download added", "setup.exe")
    clock.t += 5
    assert _run(n, tasks, clock) is None, "the same download was announced twice"


def test_a_burst_is_one_notification():
    clock = _Clock()
    n = AddedNotifier(clock=clock)
    tasks = []
    _run(n, tasks, clock)
    for i in range(40):                      # arriving over a few ticks
        tasks.append(_task(i))
        if i % 10 == 9:
            assert _run(n, tasks, clock) is None
            clock.t += 0.5
    clock.t += AddedNotifier.QUIET
    title, body = _run(n, tasks, clock)
    assert title == "40 downloads added"
    assert body.startswith("file0.zip, file1.zip, file2.zip") and body.endswith("…")


def test_while_you_are_looking_at_the_window_it_stays_quiet():
    clock = _Clock()
    n = AddedNotifier(clock=clock)
    tasks = []
    _run(n, tasks, clock)
    tasks.append(_task(1))
    _run(n, tasks, clock, looking=True)      # the card appears in front of you
    clock.t += 5
    assert _run(n, tasks, clock) is None


def test_switched_off_it_stays_quiet():
    clock = _Clock()
    n = AddedNotifier(clock=clock)
    tasks = []
    _run(n, tasks, clock)
    tasks.append(_task(1))
    _run(n, tasks, clock, enabled=False)
    clock.t += 5
    assert _run(n, tasks, clock, enabled=False) is None


def test_a_nameless_download_is_named_by_its_address():
    clock = _Clock()
    n = AddedNotifier(clock=clock)
    tasks = []
    _run(n, tasks, clock)
    tasks.append(_task(7, name=""))
    _run(n, tasks, clock)
    clock.t += AddedNotifier.QUIET
    assert _run(n, tasks, clock) == ("Download added", "https://x/file7.zip")


def test_settings_switch_it_off_and_on():
    """Settings -> Downloads carries the switch, on unless turned off."""
    from PySide6.QtWidgets import QApplication
    from gui2.dialogs.settings import SettingsDialogV2

    QApplication.instance() or QApplication([])
    common = dict(save_dir=".", max_concurrent=3, segments=8, verify_tls=True, pair_token="T",
                  theme="Dark", accent="purple", sched_en=False, sched_start="00:00",
                  sched_stop="06:00")
    on = SettingsDialogV2(None, extras={}, **common)
    assert on.values()["notify_added"] is True
    off = SettingsDialogV2(None, extras={"notify_added": False}, **common)
    assert off.values()["notify_added"] is False
    off.notify_added.setChecked(True)
    assert off.values()["notify_added"] is True
    on.deleteLater(); off.deleteLater()


def test_the_window_shows_it_through_the_tray():
    """The glue in the window: a burst becomes one tray message, and nothing
    is shown while the tray icon is hidden (Windows then has nowhere to put it)."""
    from PySide6.QtWidgets import QApplication
    from gui2.app import DownloadAppV2

    QApplication.instance() or QApplication([])
    shown = []
    tray = SimpleNamespace(isVisible=lambda: True,
                           showMessage=lambda title, body, *_: shown.append((title, body)))
    clock = _Clock()
    win = SimpleNamespace(_added_notifier=AddedNotifier(clock=clock), _extras={}, tray=tray,
                          queue=SimpleNamespace(tasks=[]))
    DownloadAppV2._notify_added(win)                    # the restored list
    win.queue.tasks += [_task(1, "a.zip"), _task(2, "b.zip")]
    DownloadAppV2._notify_added(win)
    clock.t += AddedNotifier.QUIET
    DownloadAppV2._notify_added(win)
    assert shown == [("2 downloads added", "a.zip, b.zip")]

    tray.isVisible = lambda: False
    win.queue.tasks.append(_task(3))
    DownloadAppV2._notify_added(win)
    clock.t += AddedNotifier.QUIET
    DownloadAppV2._notify_added(win)
    assert len(shown) == 1
