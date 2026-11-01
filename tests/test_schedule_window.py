"""The download schedule (Settings -> Scheduler): what the closing window
paused comes back when it opens again."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import task as T                                       # noqa: E402
import gui2.app_system as S                            # noqa: E402


class _Queue:
    def __init__(self, tasks):
        self.tasks, self.resumed = tasks, []

    def pause_task(self, t):
        t.request_pause()

    def resume_task(self, t):
        self.resumed.append(t)
        t.status = T.QUEUED


class _Win:
    scheduler_enabled = True
    scheduler_start, scheduler_stop = "01:00", "07:00"
    _scheduler_active = True
    _check_scheduler = S.SystemMixin._check_scheduler

    def __init__(self, tasks):
        self.queue = _Queue(tasks)

    def _apply_throttle(self):
        pass

    def refresh(self):
        pass


def test_a_download_running_when_the_window_closed_comes_back_the_next_night(monkeypatch):
    t = T.DownloadTask("https://a.example/f.bin", "f.bin")
    t.status = T.DOWNLOADING
    mine = T.DownloadTask("https://a.example/g.bin", "g.bin")
    mine.status = T.PAUSED                              # paused by hand: stays so
    w = _Win([t, mine])
    monkeypatch.setattr(S, "_in_window", lambda a, b: False)
    w._check_scheduler()
    assert t.status == T.SCHEDULED
    t.status = T.PAUSED                                 # what its worker writes as it stops
    monkeypatch.setattr(S, "_in_window", lambda a, b: True)
    w._check_scheduler()
    assert w.queue.resumed == [t]
