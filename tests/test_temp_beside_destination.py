"""A download for another drive keeps its unfinished file on that drive, so
finishing is a rename, not a copy of the whole file (asked 2026-10-30)."""
import os

import task as T
import utils


def _task(dest):
    return T.DownloadTask("https://x.example/a.bin", str(dest))


def _other_drive(monkeypatch):
    monkeypatch.setattr(utils, "same_drive", lambda a, b: False)


def test_another_drive_puts_it_beside_the_destination(tmp_path, monkeypatch):
    _other_drive(monkeypatch)
    t = _task(tmp_path / "dl" / "a.bin")
    p = utils.temp_download_path(t)
    assert os.path.dirname(os.path.dirname(p)) == str(tmp_path / "dl")
    assert os.path.basename(os.path.dirname(p)).startswith("HyperFetch-")
    assert t.temp_path == p
    assert T.DownloadTask.from_dict(t.to_dict()).temp_path == p     # saved


def test_thesame_drive_keeps_the_temp_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "same_drive", lambda a, b: True)
    t = _task(tmp_path / "dl" / "a.bin")
    assert os.path.dirname(utils.temp_download_path(t)) == utils.temp_dir()


def test_a_picked_unfinished_folder_wins(tmp_path, monkeypatch):
    _other_drive(monkeypatch)
    monkeypatch.setattr(utils, "INCOMPLETE_DIR", str(tmp_path / "unf"))
    t = _task(tmp_path / "dl" / "a.bin")
    assert os.path.dirname(utils.temp_download_path(t)) == utils.temp_dir()


def test_a_partial_goes_on_where_it_is_after_a_move(tmp_path, monkeypatch):
    _other_drive(monkeypatch)
    t = _task(tmp_path / "dl" / "a.bin")
    p = utils.temp_download_path(t)
    open(p, "wb").write(b"half")
    t.save_path = str(tmp_path / "elsewhere" / "a.bin")             # Move to… while paused
    assert utils.temp_download_path(t) == p


def test_a_partial_from_before_stays_in_the_temp_folder(tmp_path, monkeypatch):
    t = _task(tmp_path / "dl" / "a.bin")
    old = os.path.join(utils.temp_dir(), t.id + ".hfdownload")
    os.makedirs(utils.temp_dir(), exist_ok=True)
    open(old, "wb").write(b"half")
    _other_drive(monkeypatch)
    assert utils.temp_download_path(t) == old


def test_finishing_and_cancelling_leave_no_hidden_folder(tmp_path, monkeypatch):
    _other_drive(monkeypatch)
    t = _task(tmp_path / "dl" / "a.bin")
    p = utils.temp_download_path(t)
    open(p, "wb").write(b"whole")
    utils.finalize_download(p, t.save_path)
    assert open(t.save_path, "rb").read() == b"whole"
    assert os.listdir(tmp_path / "dl") == ["a.bin"]

    from queue_manager import QueueManager
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    try:
        u = _task(tmp_path / "dl" / "b.bin")
        open(utils.temp_download_path(u), "wb").write(b"half")
        u.status = T.PAUSED
        q.cancel_task(u)
        assert os.listdir(tmp_path / "dl") == ["a.bin"]
    finally:
        q.shutdown()
