"""aria2 disk failures must say what to do about them.

Reported from a card reading, in full:

    torrent failed: Write disk cache flush failure index=608

The cause was that the payload had been moved from G: to another drive, so the
folder aria2 was writing to no longer existed. Nothing in that message points
at a folder, a drive, or a next step.
"""
import os

import pytest

import torrent


REAL = "Write disk cache flush failure index=608"


def test_a_missing_folder_is_named(tmp_path):
    gone = tmp_path / "moved-away" / "Show.mkv"
    out = torrent.explain_failure(REAL, str(gone))
    assert str(gone.parent) in out, f"never named the folder: {out}"
    assert "index=608" not in out, "still leading with aria2's piece index"
    assert "Resume" in out, "no next step offered"


def test_a_full_drive_says_so(tmp_path, monkeypatch):
    payload = tmp_path / "Show.mkv"
    payload.write_bytes(b"x")

    class _Usage:
        total = free = used = 0
    usage = _Usage()
    usage.free = 10 * 1024 * 1024          # 10 MB left
    monkeypatch.setattr(torrent.shutil, "disk_usage", lambda p: usage)

    out = torrent.explain_failure(REAL, str(payload))
    assert "full" in out.lower(), out
    assert "Resume" in out


def test_a_writable_folder_still_gets_a_useful_message(tmp_path):
    payload = tmp_path / "Show.mkv"
    payload.write_bytes(b"x")
    out = torrent.explain_failure(REAL, str(payload))
    assert str(tmp_path) in out
    assert "writable" in out.lower() or "exists" in out.lower()


@pytest.mark.parametrize("msg", [
    "Write disk cache flush failure index=608",
    "File write failure",
    "No space left on device",
])
def test_the_disk_failures_are_all_recognised(tmp_path, msg):
    gone = tmp_path / "nope" / "f.mkv"
    assert torrent.explain_failure(msg, str(gone)), f"not recognised: {msg}"


@pytest.mark.parametrize("msg", [
    "InfoHash abc is already registered.",
    "Timeout while reading from peer",
    "",
])
def test_unrelated_failures_are_left_alone(tmp_path, msg):
    """Only disk errors get rewritten; anything else keeps aria2's own words."""
    assert torrent.explain_failure(msg, str(tmp_path / "f.mkv")) == ""


def test_no_save_path_falls_back_rather_than_guessing(tmp_path):
    assert torrent.explain_failure(REAL, "") == ""
