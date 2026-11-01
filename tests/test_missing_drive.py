"""A download folder on a drive that is not there (unplugged, a share that is
down): creating its category folder raised, and the download was lost - one
from the browser too, cancelled there already."""
import os

import utils


def test_a_new_download_is_placed_even_when_its_drive_is_gone(tmp_path, monkeypatch):
    gone = str(tmp_path / "nodrive" / "Downloads")
    real = os.makedirs

    def no_drive(path, *a, **k):
        if str(path).startswith(str(tmp_path / "nodrive")):
            raise FileNotFoundError(3, "The system cannot find the path specified")
        return real(path, *a, **k)

    monkeypatch.setattr(os, "makedirs", no_drive)
    folder, base = utils.place_download(gone, "https://a.example/x.zip", "x.zip")
    assert folder == gone and base == gone
    assert utils.get_category_dir(gone, "clip.mp4") == gone
