"""Queue Manager dialog.

The screen has to convey one idea — a queue runs N downloads at once and the
rest wait — so these assert the things that carry it: the slot meter, the
per-queue counts, and the per-task state.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

import task as T
from gui2.dialogs.queues import QueueManagerDialog, SlotMeter
from queue_manager import QueueManager


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def qm():
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 3},
                             {"name": "Night", "max_concurrent": 1}])
    q.shutdown()                      # no scheduler thread; this is a UI test
    yield q


def _add(q, name, queue_name, status, total=2_000_000_000, done=800_000_000):
    t = T.DownloadTask(f"https://x/{name}", f"C:/dl/{name}", filename=name,
                       total_size=total, queue_name=queue_name)
    t.downloaded = done
    t.status = status
    q.tasks.append(t)
    return t


def _dlg(qm):
    host = QWidget()
    d = QueueManagerDialog(host, qm)
    d._host_ref = host                # keep the parent alive for the test
    return d


def test_the_meter_shows_used_against_total():
    _app()
    m = SlotMeter(2, 3)
    assert (m._used, m._total) == (2, 3)
    m.set_state(1, 5)
    assert (m._used, m._total) == (1, 5)


def test_the_meter_never_divides_by_zero():
    _app()
    m = SlotMeter(0, 0)
    m.set_state(0, 0)
    assert m._total >= 1


def test_running_count_comes_from_the_tasks_not_a_counter(qm):
    """q.active is a live counter this dialog only snapshots; if it were stale
    the screen would misreport the one thing it exists to explain."""
    _app()
    _add(qm, "a", "Main", T.DOWNLOADING)
    _add(qm, "b", "Main", T.DOWNLOADING)
    _add(qm, "c", "Main", T.QUEUED)
    qm.queues["Main"].active = 99                 # deliberately wrong
    d = _dlg(qm)
    labels = d.findChildren(type(d.total_lbl))
    texts = [l.text() for l in labels]
    assert any("2 of 3 running" in t for t in texts)


def test_the_summary_names_only_the_states_present(qm):
    _app()
    _add(qm, "a", "Main", T.DOWNLOADING)
    _add(qm, "b", "Main", T.QUEUED)
    _add(qm, "c", "Main", T.COMPLETED)
    d = _dlg(qm)
    summary = d._summary([t for t in qm.tasks if t.queue_name == "Main"]).text()
    assert "1 downloading" in summary
    assert "1 waiting" in summary
    assert "1 done" in summary
    assert "paused" not in summary                 # nothing is paused
    assert "failed" not in summary


def test_an_empty_queue_says_so(qm):
    _app()
    d = _dlg(qm)
    assert d._summary([]).text() == "empty"


def test_a_downloading_task_shows_progress(qm):
    _app()
    t = _add(qm, "a", "Main", T.DOWNLOADING, total=1000, done=400)
    d = _dlg(qm)
    line = d._task_line(t)
    texts = [c.text() for c in line.findChildren(type(d.total_lbl))]
    assert any("40%" in x for x in texts)


def test_a_waiting_task_shows_its_state_not_a_percentage(qm):
    _app()
    t = _add(qm, "a", "Main", T.QUEUED, total=1000, done=0)
    d = _dlg(qm)
    line = d._task_line(t)          # hold it: an unparented widget is collected
    texts = [c.text() for c in line.findChildren(type(d.total_lbl))]
    assert any("Queued" in x for x in texts)
    assert not any("%" in x for x in texts)


def test_changing_the_limit_updates_the_queue_and_the_display(qm):
    _app()
    d = _dlg(qm)
    d._set_limit("Main", 7)
    assert qm.queues["Main"].max_concurrent == 7
    texts = [l.text() for l in d.findChildren(type(d.total_lbl))]
    assert any("of 7 running" in t for t in texts)


def test_the_footer_counts_queues(qm):
    _app()
    d = _dlg(qm)
    assert "2 queues" in d.total_lbl.text()


def test_a_long_list_scrolls_so_the_controls_stay_reachable(qm):
    """Thirty downloads must not push Add and Close off the bottom."""
    _app()
    for i in range(30):
        _add(qm, f"file-{i}.iso", "Main", T.QUEUED)
    d = _dlg(qm)
    d.resize(560, 520)
    assert d._scroll.widgetResizable()
    assert d._scroll.widget().sizeHint().height() > d._scroll.height()
