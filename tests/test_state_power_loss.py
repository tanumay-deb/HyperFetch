"""A power cut must never cost the download list again.

On 8 September downloads.json and downloads.json.bak both became ~5,900 bytes
of zeros. save_json wrote a temp file and renamed it over the original, which
is atomic for the NAME but not the data: the rename reached the disk at once,
the bytes were still in the write cache, and Windows kept a file of the right
size that had never been filled. The .bak had been copied the same way a minute
earlier. After that the loader refused to save for as long as the file stayed
unreadable — which was every launch, for two weeks.
"""
import json
import os

import pytest

import utils


def _record(monkeypatch):
    """Log fsync and replace in the order they happen."""
    seen = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        seen.append(("fsync", None))
        return real_fsync(fd)

    def replace(src, dst):
        seen.append(("replace", os.path.basename(str(dst))))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    return seen


def _flushed_before_rename(seen, name):
    """True when an fsync happened after the previous rename and before the
    rename onto `name`."""
    last = None
    for i, (op, dst) in enumerate(seen):
        if op == "replace" and dst == name:
            window = seen[(last + 1 if last is not None else 0):i]
            return any(o == "fsync" for o, _ in window)
        if op == "replace":
            last = i
    raise AssertionError(f"never renamed anything onto {name}: {seen}")


def test_a_save_reaches_the_disk_before_it_replaces_the_old_file(tmp_path, monkeypatch):
    p = str(tmp_path / "downloads.json")
    utils.save_json(p, [{"id": "a"}], keep_backup=True)       # something to back up
    seen = _record(monkeypatch)
    utils.save_json(p, [{"id": "a"}, {"id": "b"}], keep_backup=True)
    assert _flushed_before_rename(seen, "downloads.json.bak"), \
        "the backup is renamed into place before its bytes are on disk"
    assert _flushed_before_rename(seen, "downloads.json"), \
        "the list is renamed into place before its bytes are on disk"
    assert json.loads(open(p, encoding="utf-8").read()) == [{"id": "a"}, {"id": "b"}]
    assert json.loads(open(p + ".bak", encoding="utf-8").read()) == [{"id": "a"}]


def test_a_zeroed_file_is_never_rotated_over_a_good_backup(tmp_path):
    """The backup is the one thing that has to survive a bad main file."""
    p = tmp_path / "downloads.json"
    p.write_bytes(b"\0" * 5901)
    good = [{"id": "kept"}]
    (tmp_path / "downloads.json.bak").write_text(json.dumps(good), encoding="utf-8")
    utils.save_json(str(p), [{"id": "new"}], keep_backup=True)
    assert json.loads((tmp_path / "downloads.json.bak").read_text(encoding="utf-8")) == good, \
        "a file of zeros replaced the good backup"


def test_the_web_password_file_is_flushed_too(tmp_path, monkeypatch):
    import web_auth
    seen = _record(monkeypatch)
    assert web_auth._save({"enabled": False})
    assert _flushed_before_rename(seen, os.path.basename(web_auth._path()))


# ------------------------------------------------------------------ loading
pytest.importorskip("PySide6")


def _load(tmp_path, monkeypatch):
    from gui2.app import DownloadAppV2
    from queue_manager import QueueManager
    monkeypatch.setattr(utils, "app_data_dir", lambda: str(tmp_path))
    app = DownloadAppV2.__new__(DownloadAppV2)
    app._state_path = str(tmp_path / "downloads.json")
    app.queue = QueueManager()
    app._load_state()
    return app


def test_a_list_and_backup_of_zeros_are_set_aside_and_saving_resumes(tmp_path, monkeypatch):
    """The exact 8 September state. Refusing to save was right for one launch;
    for every launch after it, it lost everything downloaded since."""
    (tmp_path / "downloads.json").write_bytes(b"\0" * 5901)
    (tmp_path / "downloads.json.bak").write_bytes(b"\0" * 5900)
    app = _load(tmp_path, monkeypatch)
    assert not app._state_load_failed, "saving is still switched off"
    assert list(app.queue.tasks) == []
    kept = sorted(n for n in os.listdir(tmp_path) if ".corrupt-" in n)
    assert any(n.startswith("downloads.json.corrupt-") for n in kept), kept
    assert any(n.startswith("downloads.json.bak.corrupt-") for n in kept), kept
    main = next(n for n in kept if n.startswith("downloads.json.corrupt-"))
    assert (tmp_path / main).read_bytes() == b"\0" * 5901, "did not keep the original"
    assert not (tmp_path / "downloads.json").exists()


def test_a_list_that_cannot_be_opened_is_still_never_saved_over(tmp_path, monkeypatch):
    """Locked or denied is not garbage: nothing is moved, and nothing is written
    over it this session. (A directory stands in for a file we cannot open.)"""
    (tmp_path / "downloads.json").mkdir()
    app = _load(tmp_path, monkeypatch)
    assert app._state_load_failed
    assert (tmp_path / "downloads.json").is_dir()
    assert not [n for n in os.listdir(tmp_path) if ".corrupt-" in n]
