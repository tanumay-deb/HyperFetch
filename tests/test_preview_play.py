"""Playing a part-downloaded torrent.

Head/tail piece priority makes a partial video watchable; without somewhere to
click, that setting only produced a differently-ordered download.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import types

import pytest

import task as T
import utils
from gui2.app_actions import ActionsMixin


def _task(tmp_path, files=None, name="Show"):
    t = T.DownloadTask("magnet:?xt=urn:btih:abc&dn=Show",
                       str(tmp_path / name / "payload"), filename=name)
    t.status = T.DOWNLOADING
    t.file_progress = files or []
    return t


def _mk(tmp_path, rel, size):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0" * size)
    return str(p)


class _Fake(ActionsMixin):
    """A real subclass, so _VIDEO_EXTS and method lookup work as they do in the
    app — a SimpleNamespace stub silently lacked both."""

    def __init__(self):
        self.shown = []
        self._toasts = types.SimpleNamespace(
            show=lambda *a: self.shown.append(a))


def _actions():
    f = _Fake()
    return f, f


def test_the_biggest_video_wins_not_the_sample(tmp_path):
    """A release's sample file is not what anyone wants to watch."""
    feature = _mk(tmp_path, "Show/Show.S01E01.mkv", 500)
    sample = _mk(tmp_path, "Show/Sample/sample.mkv", 50)
    t = _task(tmp_path, [
        {"path": sample, "length": 50, "completed": 50, "selected": True},
        {"path": feature, "length": 5_000_000, "completed": 100, "selected": True},
    ])
    stub, cls = _actions()
    assert cls._playable_file(t) == feature


def test_a_file_with_no_data_is_not_offered(tmp_path):
    """aria2 preallocates, so an empty file exists at full size — opening it
    would just hand the player zeroes."""
    empty = _mk(tmp_path, "Show/Show.mkv", 500)
    t = _task(tmp_path, [
        {"path": empty, "length": 5_000_000, "completed": 0, "selected": True},
    ])
    stub, cls = _actions()
    assert cls._playable_file(t) == ""


def test_non_video_files_are_ignored(tmp_path):
    nfo = _mk(tmp_path, "Show/readme.nfo", 100)
    t = _task(tmp_path, [
        {"path": nfo, "length": 100, "completed": 100, "selected": True},
    ])
    stub, cls = _actions()
    assert cls._playable_file(t) == ""


def test_the_legacy_engine_falls_back_to_the_disk(tmp_path):
    """It reports no per-file progress at all, so scan the folder instead."""
    _mk(tmp_path, "Show/Show.S01E01.mkv", 4000)
    _mk(tmp_path, "Show/Show.S01E02.mkv", 9000)
    t = _task(tmp_path, [])                      # no file_progress
    stub, cls = _actions()
    got = cls._playable_file(t)
    assert got.endswith("Show.S01E02.mkv")       # the larger one


def test_a_missing_folder_is_not_an_error(tmp_path):
    t = _task(tmp_path, [], name="Gone")
    stub, cls = _actions()
    assert cls._playable_file(t) == ""


def test_playing_warns_when_preview_priority_is_off(tmp_path, monkeypatch):
    """Without head/tail priority the opening pieces may not be there, so the
    player would fail for a reason the user cannot see."""
    monkeypatch.setattr(utils, "TORRENT_PREVIEW", False, raising=False)
    video = _mk(tmp_path, "Show/Show.mkv", 500)
    t = _task(tmp_path, [
        {"path": video, "length": 500, "completed": 100, "selected": True},
    ])
    opened = []
    f = _Fake()
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    f._play_partial(t)
    toasts = f.shown
    assert opened == [video]                     # still plays
    assert any("Preview" in a[1] for a in toasts)


def test_no_warning_when_preview_priority_is_on(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "TORRENT_PREVIEW", True, raising=False)
    video = _mk(tmp_path, "Show/Show.mkv", 500)
    t = _task(tmp_path, [
        {"path": video, "length": 500, "completed": 100, "selected": True},
    ])
    opened = []
    f = _Fake()
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    f._play_partial(t)
    toasts = f.shown
    assert opened == [video]
    assert toasts == []


def test_nothing_to_play_says_so(tmp_path, monkeypatch):
    t = _task(tmp_path, [])
    opened = []
    f = _Fake()
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    f._play_partial(t)
    toasts = f.shown
    assert opened == []
    assert toasts and "preview" in toasts[0][1].lower()
