"""A name the user typed for a video is the video's name.

Found on 2026-10-06: a download that goes to yt-dlp ignored the name it was
given. yt-dlp named the file by the video's title whatever the task said, and
the task was then renamed to match - while it waited (yt_dl.NameScout) and as
it ran (YtDlpDownloader._settle). A name typed in New Download, a Rename from
the card's menu and one from the web client all went back to the title.

The name the user typed is kept on the task now (DownloadTask.name_chosen),
and yt-dlp names the file by it - with the extension yt-dlp chooses, which is
known only once it has read the page: "Lecture 1.mp4" typed and a webm chosen
is "Lecture 1.webm". The name New Download fills in is a guess from the link,
not the user's, and the title still wins over that.

The stand-in for yt-dlp is tests/test_ytdlp_parallel.py's, as in
tests/test_names_while_waiting.py; the tests at the end run the real yt-dlp
against pages served here. No network.
"""
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import task as T
import utils
import yt_dl
from downloader import Downloader
from queue_manager import QueueManager
from test_ytdlp_parallel import (SIZE, TITLE, _pause_midway, _ranged, _requested,  # noqa: F401
                                 _start, _wait_for, real_ytdlp, site, video_page)

PAGE = "watch/8xGJx1"
YOUTUBE = "https://www.youtube.com/watch?v=8xGJx1"
PW = "correct horse battery"
LAN = "192.168.1.50"


@pytest.fixture
def queue():
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    yield q
    q.shutdown()


def _scout(queue):
    return yt_dl.NameScout(lambda: queue.tasks, queue.while_waiting)


def _waiting(media_server, tmp_path, queue, typed="Lecture 1.mp4", status=T.QUEUED):
    """A video page added as New Download adds one with `typed` typed for its
    name (test_a_name_typed_in_new_download_is_the_users), or with none, and
    not started."""
    base = str(tmp_path / "dl")
    os.makedirs(base, exist_ok=True)
    page = media_server.url(PAGE)
    name = utils.filename_from_url(page, typed) if typed else "watch.bin"
    folder, sort_base = utils.place_download(base, page, name)
    t = T.DownloadTask(page, utils.unique_path(folder, name), filename=name, status=status)
    t.sort_base, t.use_ytdlp = sort_base, True
    if typed:
        t.name_chosen = yt_dl.stem(os.path.basename(t.save_path))
    queue.add_task(t, start=False)              # listed; nothing starts it
    return t, base


# ---- New Download ---------------------------------------------------------------
def _window(base, extras=None):
    """Just enough of the main window to add a download (tests/test_sorting.py)."""
    from gui2.app import DownloadAppV2
    added = []
    queue = types.SimpleNamespace(queues={"Main": None}, tasks=[], segments=8,
                                  add_task=lambda t, start=True: added.append(t))
    win = types.SimpleNamespace(
        save_dir=str(base), segments=8, queue=queue, _extras=dict(extras or {}),
        _toasts=types.SimpleNamespace(show=lambda *a, **k: None),
        _save_state=lambda: None, refresh=lambda: None)
    win._quick_values = lambda *a: DownloadAppV2._quick_values(win, *a)
    return win, added, DownloadAppV2


def _answered(monkeypatch, typed=None):
    """New Download, answered: Download pressed with the name as the dialog
    filled it in, or with `typed` in its place - and "Use yt-dlp" ticked, as
    it is for a site the app knows."""
    from PySide6.QtWidgets import QApplication, QDialog
    from gui2 import app
    QApplication.instance() or QApplication([])

    class Answered(app.NewDownloadDialog):
        def __init__(self, parent, *a, **kw):
            super().__init__(None, *a, **kw)        # the stand-in window is no widget
            if typed is not None:
                self.name_edit.setText(typed)
            self.use_ytdlp.setChecked(True)

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(app, "NewDownloadDialog", Answered)


def test_a_name_typed_in_new_download_is_the_users(tmp_path, monkeypatch):
    win, added, App = _window(tmp_path)
    _answered(monkeypatch, typed="Lecture 1.mp4")

    App._add_download(win, YOUTUBE, "", None)

    t, = added
    assert t.filename == "Lecture 1.mp4"
    assert t.name_chosen == "Lecture 1", "the extension is yt-dlp's to choose"


@pytest.mark.parametrize("dialog", [True, False], ids=["filled-in", "no-dialog"])
def test_the_name_new_download_filled_in_is_not(site, media_server, tmp_path, monkeypatch,
                                                queue, dialog):
    """A guess from the link - here the browser's - says less than the title."""
    win, added, App = _window(tmp_path, {"skip_new_download_dialog": not dialog})
    _answered(monkeypatch)                      # the name left as it was filled in

    App._add_download(win, media_server.url(PAGE), "Some page.mp4", None)

    t, = added
    assert t.filename == "Some page.mp4" and t.name_chosen == ""
    t.use_ytdlp = True                          # added without the dialog: the box is not there
    queue.add_task(t, start=False)
    _scout(queue).run_once()
    assert t.filename == TITLE + ".mp4"


def test_the_typed_name_holds_while_it_waits_and_for_the_finished_file(
        site, media_server, tmp_path, monkeypatch, queue):
    win, added, App = _window(tmp_path)
    _answered(monkeypatch, typed="Lecture 1")
    App._add_download(win, media_server.url(PAGE), "", None)
    t, = added
    assert t.filename == "Lecture 1.bin", "no extension typed: .bin, as ever"
    queue.add_task(t, start=False)

    assert _scout(queue).run_once() is t

    named = t.save_path
    assert named == str(tmp_path / "Video" / "Lecture 1.mp4"), named
    assert not os.path.exists(tmp_path / "Other"), "the folder it only waited in was left"

    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert t.save_path == named, "its download called it something else"
    assert open(named, "rb").read() == site.data
    assert os.listdir(tmp_path / "Video") == ["Lecture 1.mp4"]


def test_the_extension_is_the_one_yt_dlp_chooses(site, media_server, tmp_path, queue):
    site.fields = {"ext": "webm"}
    t, base = _waiting(media_server, tmp_path, queue, typed="Lecture 1.mp4")

    _scout(queue).run_once()
    assert t.filename == "Lecture 1.webm"
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert t.save_path == os.path.join(base, "Video", "Lecture 1.webm")


def test_the_name_holds_as_it_runs(site, media_server, tmp_path, queue):
    """Not asked about while it waited (Settings can switch that off): its
    download names it, as it starts."""
    site.merge = True                           # yt-dlp fetches these itself
    t, base = _waiting(media_server, tmp_path, queue)
    fetched = []
    site.on_dl = lambda name: fetched.append((t.filename, os.path.basename(name)))

    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert len(fetched) == 2
    assert all(shown == "Lecture 1.mp4" and part.startswith("Lecture 1.")
               for shown, part in fetched), fetched
    assert t.save_path == os.path.join(base, "Video", "Lecture 1.mp4")


@pytest.mark.parametrize("typed,kept", [
    ("Lecture 1.mp4", "Lecture 1"), ("Lecture 1.bin", "Lecture 1"),
    ("LECTURE 1.MKV", "LECTURE 1"), ("Song.opus", "Song"), ("Lecture 1", "Lecture 1"),
    ("Part 1.5", "Part 1.5"), ("Mr. Smith goes", "Mr. Smith goes"), ("notes.txt", "notes.txt"),
])
def test_what_of_a_typed_name_the_file_keeps(typed, kept):
    """All of it but an extension that is yt-dlp's to choose."""
    assert yt_dl.stem(typed) == kept


# ---- Rename ------------------------------------------------------------------------
def _rename(monkeypatch, t, queue, typed):
    """Rename, from the card's menu, answered with `typed`. What the window
    said, if anything."""
    from PySide6.QtWidgets import QApplication, QInputDialog
    from gui2.app_actions import ActionsMixin
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (typed, True)))
    said = []
    win = types.SimpleNamespace(queue=queue, _save_state=lambda: None, refresh=lambda: None,
                                _toasts=types.SimpleNamespace(show=lambda *a: said.append(a)))
    ActionsMixin._rename_task(win, t)
    return said


def test_rename_a_video_that_waits(site, media_server, tmp_path, queue, monkeypatch):
    t, base = _waiting(media_server, tmp_path, queue, typed=None)

    assert _rename(monkeypatch, t, queue, "Lecture 1") == []
    assert t.filename == "Lecture 1" and t.name_chosen == "Lecture 1"

    _scout(queue).run_once()
    assert t.save_path == os.path.join(base, "Video", "Lecture 1.mp4")
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.save_path == os.path.join(base, "Video", "Lecture 1.mp4")


def test_a_video_yt_dlp_has_named_keeps_the_extension_it_gave(site, media_server, tmp_path,
                                                              queue, monkeypatch):
    t, base = _waiting(media_server, tmp_path, queue, typed=None)
    _scout(queue).run_once()
    assert t.filename == TITLE + ".mp4"

    _rename(monkeypatch, t, queue, "Lecture 1.mkv")

    assert t.save_path == os.path.join(base, "Video", "Lecture 1.mp4"), "not the file it will be"
    assert _scout(queue).run_once() is None, "asked about again"
    Downloader(t, segments=4).run()
    assert t.save_path == os.path.join(base, "Video", "Lecture 1.mp4")
    assert len(site.calls) == 2


def test_rename_a_video_paused_midway(site, media_server, tmp_path, queue, monkeypatch):
    """The engine's bytes are in the id-keyed temp: they stay where they are,
    and the download goes on from them."""
    t, base = _waiting(media_server, tmp_path, queue, typed=None)
    paused_at = _pause_midway(t, media_server)
    assert t.filename == TITLE + ".mp4"
    since = len(media_server.log)

    _rename(monkeypatch, t, queue, "Lecture 1")

    assert t.filename == "Lecture 1.mp4"
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.save_path == os.path.join(base, "Video", "Lecture 1.mp4")
    assert open(t.save_path, "rb").read() == site.data
    assert _requested(_ranged(media_server, since)) <= SIZE - paused_at + 1, "started over"
    assert os.listdir(os.path.join(base, "Video")) == ["Lecture 1.mp4"]


def test_what_yt_dlp_left_on_disk_is_renamed_with_it_and_nothing_else(tmp_path, queue):
    """Its .part, .ytdl and fragments, a merge's parts and theirs, a merge it
    was making - named after the video. Moved without them, the download
    would start again beside them."""
    video = tmp_path / "Video"
    video.mkdir()
    t = T.DownloadTask(YOUTUBE, str(video / "A video.mp4"), filename="A video.mp4",
                       status=T.PAUSED)
    t.yt_named = True
    queue.add_task(t, start=False)
    left = ["A video.mp4.part", "A video.mp4.ytdl", "A video.mp4.part-Frag3",
            "A video.mp4.part-Frag4.part", "A video.f137.mp4", "A video.f140.m4a.part",
            "A video.f140.m4a.ytdl", "A video.temp.mp4"]
    others = ["A video.mp4", "A video.pdf", "A video.txt.part", "A video 2.mp4.part",
              "Another.mp4.part"]
    for name in left + others:
        (video / name).write_bytes(b"x")

    assert yt_dl.rename(queue, t, "Lecture 1.mp4")

    assert t.save_path == str(video / "Lecture 1.mp4")
    assert sorted(os.listdir(video)) == sorted(
        [n.replace("A video", "Lecture 1", 1) for n in left] + others)


def test_a_name_another_downloads_partial_has_is_taken(tmp_path, queue):
    """yt-dlp would carry on from that partial, into this video."""
    t = T.DownloadTask(YOUTUBE, str(tmp_path / "A video.mp4"), filename="A video.mp4",
                       status=T.PAUSED)
    t.yt_named = True
    queue.add_task(t, start=False)
    (tmp_path / "A video.mp4.part").write_bytes(b"mine")
    (tmp_path / "Lecture 1.mp4.part").write_bytes(b"another's")

    assert yt_dl.rename(queue, t, "Lecture 1.mp4")

    assert t.filename == "Lecture 1 (1).mp4" and t.name_chosen == "Lecture 1 (1)"
    assert (tmp_path / "Lecture 1 (1).mp4.part").read_bytes() == b"mine"
    assert (tmp_path / "Lecture 1.mp4.part").read_bytes() == b"another's"


@pytest.mark.parametrize("status", [T.QUEUED, T.DOWNLOADING], ids=["starting", "running"])
def test_a_video_is_not_renamed_while_it_runs(media_server, tmp_path, queue, monkeypatch,
                                              status):
    """yt-dlp named the file as it started, and the run ends under that name:
    the row would go back to it, and a pause would leave its partial behind."""
    t, _ = _waiting(media_server, tmp_path, queue, typed=None)
    with queue.cond:                            # as the scheduler does when it starts one
        t._worker_alive = True
    t.status = status
    before = (t.save_path, t.filename, t.name_chosen)

    said = _rename(monkeypatch, t, queue, "Lecture 1")

    assert (t.save_path, t.filename, t.name_chosen) == before
    assert [s[1] for s in said] == ["Pause first"]


def test_renaming_a_finished_video_keeps_its_extension(tmp_path, queue, monkeypatch):
    t = T.DownloadTask(YOUTUBE, str(tmp_path / "A video.webm"), filename="A video.webm",
                       status=T.COMPLETED)
    t.yt_named = True
    (tmp_path / "A video.webm").write_bytes(b"video")

    _rename(monkeypatch, t, queue, "Lecture 1")

    assert t.save_path == str(tmp_path / "Lecture 1.webm")
    assert (tmp_path / "Lecture 1.webm").read_bytes() == b"video"


@pytest.fixture
def web(queue, tmp_path):
    """The web client's rename, signed in from another machine on the LAN."""
    import web_auth
    from api_server import create_app
    app = create_app(queue, str(tmp_path), pending=None, token="tok")
    app.config["TESTING"] = True
    c = app.test_client()
    web_auth.set_password(PW, user="admin")
    web_auth.set_enabled(True)
    assert c.post("/api/login", json={"username": "admin", "password": PW},
                  environ_overrides={"REMOTE_ADDR": LAN}).status_code == 200
    return lambda t, name: c.post("/api/downloads/%s/rename" % t.id, json={"name": name},
                                  environ_overrides={"REMOTE_ADDR": LAN})


def test_the_web_clients_rename_holds_for_a_video(site, media_server, tmp_path, queue, web):
    t, base = _waiting(media_server, tmp_path, queue, typed=None)

    r = web(t, "Lecture 1")

    assert r.status_code == 200 and r.get_json()["name"] == "Lecture 1"
    assert t.name_chosen == "Lecture 1"
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.save_path == os.path.join(base, "Video", "Lecture 1.mp4")


def test_the_web_client_is_asked_to_pause_a_running_video_first(media_server, tmp_path, queue,
                                                                web):
    t, _ = _waiting(media_server, tmp_path, queue, typed=None)
    with queue.cond:
        t._worker_alive = True
    t.status = T.DOWNLOADING

    r = web(t, "Lecture 1")

    assert r.status_code == 409
    assert r.get_json()["message"].startswith("Pause this video")
    assert t.filename == "watch.bin" and t.name_chosen == ""


# ---- kept ----------------------------------------------------------------------------
def test_the_typed_name_is_kept_across_a_restart():
    t = T.DownloadTask(YOUTUBE, "C:/dl/Lecture 1.bin", filename="Lecture 1.bin")
    t.name_chosen = "Lecture 1"
    assert T.DownloadTask.from_dict(t.to_dict()).name_chosen == "Lecture 1"


def test_a_list_saved_before_this_has_no_typed_names():
    d = T.DownloadTask(YOUTUBE, "C:/dl/watch.bin").to_dict()
    d.pop("name_chosen")
    assert T.DownloadTask.from_dict(d).name_chosen == ""


# ---- the real yt-dlp --------------------------------------------------------------------
def _page_task(page, out, queue, name="watch.bin", typed=""):
    """A video page added to a folder the user chose, `out`."""
    out.mkdir(exist_ok=True)
    t = T.DownloadTask(page, str(out / name), filename=name)
    t.use_ytdlp, t.sort_base, t.name_chosen = True, "", typed
    queue.add_task(t, start=False)
    return t


def test_with_the_real_ytdlp_a_typed_name_is_taken_as_it_is(
        real_ytdlp, video_page, media_server, make_payload, tmp_path, queue, monkeypatch):
    """As the name, not as template text: yt-dlp would expand an environment
    variable in that, and read a "%" as the start of a field."""
    monkeypatch.setenv("HF_PROBE", "expanded")
    out = tmp_path / "dl"
    data = media_server.put("media/clip.mp4", make_payload(SIZE), etag='"v1"')
    page = video_page("/watch/6", media_server.url("media/clip.mp4"))
    t = _page_task(page, out, queue, typed="100% $HF_PROBE")

    assert _scout(queue).run_once() is t
    assert t.filename == "100% $HF_PROBE.mp4"
    assert not [e for e in media_server.log if e["method"] == "GET"], "fetched to name it"

    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert t.save_path == str(out / "100% $HF_PROBE.mp4")
    assert open(t.save_path, "rb").read() == data


def test_with_the_real_ytdlp_its_own_partial_is_renamed_with_it(
        real_ytdlp, video_page, media_server, make_payload, tmp_path, queue, monkeypatch):
    """What yt-dlp fetches itself - a merge, HLS, YouTube's chunked formats;
    here a host rule says so - it writes to Title.mp4.part beside the file.
    Renamed without it, the download started again from nothing."""
    monkeypatch.setattr(utils, "HOST_RULES", {"127.0.0.1": {"ytdlp": True}})
    out = tmp_path / "dl"
    data = media_server.put("media/clip.mp4", make_payload(SIZE), etag='"v1"')
    t = _page_task(video_page("/watch/7", media_server.url("media/clip.mp4")), out, queue)
    media_server.rate = 1024 * 1024
    th = _start(t)
    assert _wait_for(lambda: t.downloaded >= SIZE // 4), "never got going"
    t.request_pause()
    th.join(10)
    assert t.status == T.PAUSED, t.error
    media_server.rate = 0
    t.clear_pause()
    title = os.path.basename(t.save_path)
    assert title.startswith(TITLE) and os.listdir(out) == [title + ".part"], os.listdir(out)
    since = len(media_server.log)

    _rename(monkeypatch, t, queue, "Lecture 1")

    assert os.listdir(out) == ["Lecture 1.mp4.part"]
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED, t.error
    assert t.save_path == str(out / "Lecture 1.mp4")
    assert open(t.save_path, "rb").read() == data
    gets = [e for e in media_server.log[since:] if e["method"] == "GET"]
    assert gets and all((e["range"] or "bytes=0-")[6:].split("-")[0] != "0" for e in gets), (
        "started over: %s" % [e["range"] for e in gets])
    assert os.listdir(out) == ["Lecture 1.mp4"]


def test_with_the_real_ytdlp_a_name_typed_for_a_playlist_does_not_break_its_run(
        real_ytdlp, video_page, media_server, make_payload, tmp_path, queue):
    """Every entry would have been given the one name, and all but the first
    taken for downloaded already. They keep their titles."""
    out = tmp_path / "dl"
    one = media_server.put("media/1.mp4", make_payload(SIZE // 4), etag='"1"')
    two = media_server.put("media/2.mp4", make_payload(SIZE // 2)[::-1], etag='"2"')
    page = video_page("/list/8", media_server.url("media/1.mp4"), media_server.url("media/2.mp4"))
    t = _page_task(page, out, queue, name="My list.bin", typed="My list")

    assert _scout(queue).run_once() is t
    assert t.filename == "My list.bin", "a playlist has no one name to give"

    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    got = sorted(os.listdir(out))
    assert len(got) == 2 and all(n.startswith(TITLE) and n.endswith(".mp4") for n in got), got
    assert sorted(open(out / n, "rb").read() for n in got) == sorted([one, two])
