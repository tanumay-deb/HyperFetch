"""A video fetched as picture and sound is two files: the bar went back to 0%
as the second began."""
import sys
import types

import task as T
import yt_dl

FORMATS = [{"filesize": 100}, {"filesize_approx": 50}]


def test_progress_goes_on_across_a_videos_parts(tmp_path, monkeypatch):
    t = T.DownloadTask("https://www.youtube.com/watch?v=abc", str(tmp_path / "v.mkv"),
                       filename="v.mkv")
    seen = []

    class _YDL:
        def __init__(self, opts):
            self.hooks = opts["progress_hooks"]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            info = {"id": "abc", "requested_formats": FORMATS}
            for d in ({"status": "downloading", "downloaded_bytes": 60, "total_bytes": 100},
                      {"status": "finished", "downloaded_bytes": 100, "total_bytes": 100},
                      {"status": "downloading", "downloaded_bytes": 20, "total_bytes": 50}):
                for h in self.hooks:
                    h(dict(d, info_dict=info, filename=str(tmp_path / "v.f1.mp4")))
                seen.append((t.downloaded, t.total_size))
            return {"title": "v", "ext": "mkv"}

        def prepare_filename(self, info):
            return ""

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = _YDL
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    yt_dl.YtDlpDownloader(t).run()
    assert seen == [(60, 150), (100, 150), (120, 150)]
