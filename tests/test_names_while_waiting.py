"""A video that is only waiting gets its name while it waits.

A video page is added as "watch.bin": the link says nothing else. yt-dlp knows
the title once it has read the page, and a download does that as it starts - so
everything still waiting its turn was a row of "watch.bin", and nobody could
tell what was already in the list (asked for on 2026-10-06: "user will know
whats already there in the download list").

yt_dl.NameScout reads the page for those, one at a time, with the options the
download itself will use, and gives each the name and folder its download
would. Nothing is downloaded. A download that starts meanwhile keeps what its
own run says: the name is written through QueueManager.while_waiting, which
refuses a task that has a worker.

The stand-in for yt-dlp is the one tests/test_ytdlp_parallel.py runs downloads
against, so the same page is read here and downloaded there; the last test
does both with the real yt-dlp. No network.
"""
import os
import sys
import threading
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import task as T
import utils
import yt_dl
from downloader import Downloader
from queue_manager import QueueManager
from test_ytdlp_parallel import SIZE, TITLE, real_ytdlp, site, video_page  # noqa: F401

PAGE = "watch/8xGJx1"
NAMED = TITLE + ".mp4"


@pytest.fixture
def queue():
    q = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    yield q
    q.shutdown()


def _waiting(media_server, tmp_path, queue, status=T.QUEUED, name="watch.bin"):
    """A video page added the way the app adds one, and not started."""
    base = str(tmp_path / "dl")
    os.makedirs(base, exist_ok=True)
    page = media_server.url(PAGE)
    folder, sort_base = utils.place_download(base, page, name)
    t = T.DownloadTask(page, utils.unique_path(folder, name), filename=name, status=status)
    t.sort_base, t.use_ytdlp = sort_base, True
    queue.add_task(t, start=False)          # listed; nothing starts it
    return t, base


def _scout(queue, **kw):
    return yt_dl.NameScout(lambda: queue.tasks, queue.while_waiting, **kw)


# ---- the case ---------------------------------------------------------------
def test_a_waiting_video_is_given_its_title(site, media_server, tmp_path, queue):
    t, _ = _waiting(media_server, tmp_path, queue)
    assert t.filename == "watch.bin"

    assert _scout(queue).run_once() is t

    assert t.filename == NAMED
    assert t.status == T.QUEUED
    assert [c["download"] for c in site.calls] == [False], "it was told to download"
    assert not site.own, "yt-dlp fetched something"


def test_a_paused_one_too(site, media_server, tmp_path, queue):
    t, _ = _waiting(media_server, tmp_path, queue, status=T.PAUSED)
    _scout(queue).run_once()
    assert t.filename == NAMED and t.status == T.PAUSED


def test_it_is_filed_under_video_while_it_waits(site, media_server, tmp_path, queue):
    t, base = _waiting(media_server, tmp_path, queue)
    assert os.path.dirname(t.save_path) == os.path.join(base, "Other")

    _scout(queue).run_once()

    assert t.save_path == os.path.join(base, "Video", NAMED)
    assert utils.category_of(t) == "Video"
    assert not os.path.exists(os.path.join(base, "Other")), (
        "the folder it only waited in was left behind, empty")
    assert os.listdir(os.path.join(base, "Video")) == [], "something was written"


def test_its_size_is_known_when_the_site_says(site, media_server, tmp_path, queue):
    site.fields = {"filesize": 123456}
    t, _ = _waiting(media_server, tmp_path, queue)
    _scout(queue).run_once()
    assert t.total_size == 123456


def test_its_download_gives_it_the_same_name_and_folder(site, media_server, tmp_path, queue):
    t, base = _waiting(media_server, tmp_path, queue)
    _scout(queue).run_once()
    named = t.save_path

    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert t.save_path == named == os.path.join(base, "Video", NAMED)
    assert open(t.save_path, "rb").read() == site.data


def test_the_page_is_read_with_the_options_its_download_will_use(site, media_server,
                                                                 tmp_path, queue):
    """Else the name could be another format's: Title.webm while it waits,
    Title.mp4 once it runs."""
    t, _ = _waiting(media_server, tmp_path, queue)
    t.yt_format = "bv*[height<=480]+ba/b[height<=480]"
    t.headers = {"User-Agent": "Browser/2.0", "Referer": "https://ref.test/watch"}

    _scout(queue).run_once()
    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    look, run = site.calls[0]["opts"], site.calls[1]["opts"]
    assert "height<=480" in look["format"]
    for key in ("format", "noplaylist", "http_headers", "ffmpeg_location", "proxy",
                "nocheckcertificate"):
        assert look.get(key) == run.get(key), key
    assert (os.path.basename(look["outtmpl"]) == os.path.basename(run["outtmpl"])
            == "%(title)s.%(ext)s")


def test_an_audio_only_one_waits_in_music(site, media_server, tmp_path, queue):
    site.fields = {"ext": "m4a", "vcodec": "none"}
    t, base = _waiting(media_server, tmp_path, queue)
    t.yt_format = "ba"
    _scout(queue).run_once()
    assert t.save_path == os.path.join(base, "Music", TITLE + ".m4a")


def test_a_folder_the_user_chose_is_kept(site, media_server, tmp_path, queue):
    chosen = tmp_path / "my clips"
    chosen.mkdir()
    t = T.DownloadTask(media_server.url(PAGE), str(chosen / "watch.bin"), filename="watch.bin")
    t.sort_base, t.use_ytdlp = "", True          # "": the user picked the place
    queue.add_task(t, start=False)

    _scout(queue).run_once()

    assert t.save_path == str(chosen / NAMED)


def test_a_download_already_begun_stays_where_it_is(site, media_server, tmp_path, queue):
    """Paused part-way under the name it was added with (a list saved before
    names were kept): yt-dlp's partial is in that folder, and moved now the
    download would start again from nothing beside it."""
    t, base = _waiting(media_server, tmp_path, queue, status=T.PAUSED)
    other = os.path.join(base, "Other")
    with open(os.path.join(other, TITLE + ".f720p.mp4.part"), "wb") as f:
        f.write(b"x" * 100)

    _scout(queue).run_once()

    assert t.save_path == os.path.join(other, NAMED)


# ---- which downloads -----------------------------------------------------------
@pytest.mark.parametrize("status", [T.DOWNLOADING, T.COMPLETED, T.ERROR, T.CANCELLED])
def test_only_a_download_that_waits_is_asked_about(site, media_server, tmp_path, queue,
                                                   status):
    t, _ = _waiting(media_server, tmp_path, queue, status=status)
    assert _scout(queue).run_once() is None
    assert site.calls == [] and t.filename == "watch.bin"


def test_what_is_not_yt_dlps_is_not_asked_about(site, media_server, tmp_path, queue):
    plain = T.DownloadTask(media_server.url("files/setup.exe"), str(tmp_path / "setup.exe"),
                           filename="setup.exe")
    magnet = T.DownloadTask("magnet:?xt=urn:btih:" + "a" * 40, str(tmp_path / "magnet_.bin"),
                            filename="magnet_.bin")
    for t in (plain, magnet):
        queue.add_task(t, start=False)

    assert _scout(queue).run_once() is None
    assert site.calls == []


def test_which_downloads_are_yt_dlps(monkeypatch):
    """One answer, for the engine that routes a download and for the scout."""
    def task(url, name, **kw):
        t = T.DownloadTask(url, "C:/dl/" + name, filename=name)
        for k, v in kw.items():
            setattr(t, k, v)
        return t

    assert yt_dl.is_ytdlp_task(task("https://www.youtube.com/watch?v=abc", "watch.bin"))
    assert yt_dl.is_ytdlp_task(task("https://cdn.example.org/a/manifest.mpd", "manifest.mpd"))
    assert yt_dl.is_ytdlp_task(task("https://example.org/v/9", "9.bin", use_ytdlp=True))
    page = task("https://clips.example.test/v/9", "9.bin")
    assert not yt_dl.is_ytdlp_task(page)
    assert yt_dl.is_ytdlp_task(page, "application/dash+xml"), "what the server called it"
    monkeypatch.setattr(utils, "HOST_RULES", {"example.test": {"ytdlp": True}})
    assert yt_dl.is_ytdlp_task(page), "Settings sends this host to yt-dlp"
    # a torrent is the torrent engine's, whatever else is true of it
    assert not yt_dl.is_ytdlp_task(task("https://clips.example.test/a.torrent", "a.torrent"))
    assert not yt_dl.is_ytdlp_task(task("magnet:?xt=urn:btih:" + "a" * 40, "magnet_.bin",
                                        use_ytdlp=True))


def test_the_engine_routes_by_the_same_answer():
    import inspect
    assert "yt_dl.is_ytdlp_task(self.t, self._probe_ctype)" in inspect.getsource(Downloader._run)


def test_one_video_a_pass_and_the_queued_before_the_paused(site, media_server, tmp_path,
                                                           queue):
    paused, _ = _waiting(media_server, tmp_path, queue, status=T.PAUSED)
    queued, _ = _waiting(media_server, tmp_path, queue)
    s = _scout(queue)

    assert s.run_once() is queued, "what downloads next was not named first"
    assert paused.filename == "watch.bin"
    assert s.run_once() is paused
    assert s.run_once() is None, "asked again about a video it has named"
    assert len(site.calls) == 2


def test_a_playlist_has_no_one_name_and_is_asked_about_once(media_server, tmp_path, queue):
    t, _ = _waiting(media_server, tmp_path, queue, name="playlist.bin")
    asked = []
    s = _scout(queue, look_fn=lambda task, give_up: asked.append(task) or ("", 0))

    assert s.run_once() is t
    assert s.run_once() is None
    assert t.filename == "playlist.bin" and len(asked) == 1


# ---- a download that starts, or is changed, while its page is read ---------------
def test_a_download_that_started_meanwhile_keeps_what_its_own_run_says(media_server,
                                                                      tmp_path, queue):
    """Reading the page takes seconds. A slot that frees in that time starts
    the download, which notes the folder it is in as it starts and names
    itself a moment later; a name written now would point at another folder
    than the one its file is going to."""
    t, base = _waiting(media_server, tmp_path, queue)
    before = t.save_path

    def look(task, give_up):
        with queue.cond:                    # as the scheduler does when it starts one
            task._worker_alive = True
        return os.path.join(os.path.dirname(task.save_path), NAMED), 0

    assert _scout(queue, look_fn=look).run_once() is t

    assert (t.save_path, t.filename) == (before, "watch.bin")
    assert not t.yt_named
    assert os.path.isdir(os.path.join(base, "Other")), "the folder it is in was removed"


def test_a_download_moved_by_hand_meanwhile_stays_where_it_was_put(media_server, tmp_path,
                                                                  queue):
    t, base = _waiting(media_server, tmp_path, queue)
    mine = tmp_path / "mine"
    mine.mkdir()
    looks = []

    def look(task, give_up):
        found = os.path.join(os.path.dirname(task.save_path), NAMED), 0
        if not looks:                       # "Move to…", while the page was being read
            task.save_path, task.sort_base = str(mine / "watch.bin"), ""
        looks.append(found)
        return found

    s = _scout(queue, look_fn=look)
    s.run_once()
    assert t.save_path == str(mine / "watch.bin"), "the move was undone"
    s.run_once()
    assert t.save_path == str(mine / NAMED), "it never got its name"


def test_the_queue_lets_a_waiting_task_be_changed_and_no_other(queue):
    t = T.DownloadTask("https://example.test/a", "a.bin", filename="a.bin")
    queue.add_task(t, start=False)
    did = []

    assert queue.while_waiting(t, lambda: did.append("waiting")) is True
    t._worker_alive = True
    assert queue.while_waiting(t, lambda: did.append("has a worker")) is False
    t._worker_alive = False
    for status in (T.DOWNLOADING, T.COMPLETED, T.ERROR, T.CANCELLED):
        t.status = status
        assert queue.while_waiting(t, lambda: did.append(str(status))) is False
    t.status = T.PAUSED
    assert queue.while_waiting(t, lambda: did.append("paused")) is True
    queue.remove_task(t)
    t.status = T.PAUSED
    assert queue.while_waiting(t, lambda: did.append("removed")) is False
    assert did == ["waiting", "paused"]


def test_the_change_is_made_with_the_queues_lock_held(queue):
    """...which is what keeps the scheduler from starting the task half-way
    through it."""
    t = T.DownloadTask("https://example.test/a", "a.bin", filename="a.bin")
    queue.add_task(t, start=False)
    free = []

    def change():
        def other():
            got = queue.cond.acquire(timeout=0.2)
            free.append(got)
            if got:
                queue.cond.release()
        th = threading.Thread(target=other)
        th.start()
        th.join()

    queue.while_waiting(t, change)
    assert free == [False]


# ---- a page that will not read -----------------------------------------------------
def test_a_page_that_cannot_be_read_is_left_alone_longer_each_time(media_server, tmp_path,
                                                                 queue):
    """Soon the first time: the app starts with Windows, often before the
    network is up. Then ever more seldom: a video that is gone stays gone."""
    now = [1000.0]

    def look(task, give_up):
        assert task.meta_fetching, "the card cannot say it is reading the page"
        raise RuntimeError("\x1b[0;31mERROR:\x1b[0m [site] 8xGJx1: Private video")

    t, _ = _waiting(media_server, tmp_path, queue)
    s = _scout(queue, look_fn=look, clock=lambda: now[0])

    assert s.run_once() is t
    assert t.meta_failed and not t.meta_fetching and t.filename == "watch.bin"
    said = [e["message"] for e in t.events if "Private video" in e["message"]]
    assert len(said) == 1 and "\x1b" not in said[0] and "ERROR:" not in said[0]
    assert s.run_once() is None, "asked again at once"

    assert yt_dl.NAME_RETRY_FIRST < yt_dl.NAME_RETRY
    now[0] += yt_dl.NAME_RETRY_FIRST + 1
    assert s.run_once() is t
    now[0] += yt_dl.NAME_RETRY_FIRST + 1
    assert s.run_once() is None, "the second wait was no longer than the first"
    now[0] += yt_dl.NAME_RETRY
    assert s.run_once() is t
    now[0] += yt_dl.NAME_RETRY + 1
    assert s.run_once() is None, "the third wait was no longer than the second"
    assert len([e for e in t.events if "Private video" in e["message"]]) == 1, (
        "said so again on every try")


def test_the_wait_stops_growing(media_server, tmp_path, queue):
    now = [1000.0]

    def look(task, give_up):
        raise RuntimeError("no")

    t, _ = _waiting(media_server, tmp_path, queue)
    s = _scout(queue, look_fn=look, clock=lambda: now[0])
    for _ in range(12):
        now[0] = max(now[0], t.meta_retry_after) + 1
        assert s.run_once() is t
    assert t.meta_retry_after - now[0] <= yt_dl.NAME_RETRY_MAX


def test_a_page_that_reads_later_takes_the_note_away(site, media_server, tmp_path, queue):
    now = [1000.0]
    fail = [True]

    def look(task, give_up):
        if fail[0]:
            raise RuntimeError("temporary failure in name resolution")
        return yt_dl.look_up(task, give_up)

    t, _ = _waiting(media_server, tmp_path, queue)
    s = _scout(queue, look_fn=look, clock=lambda: now[0])
    s.run_once()
    assert t.meta_failed
    fail[0] = False
    now[0] += yt_dl.NAME_RETRY + 1

    s.run_once()

    assert t.filename == NAMED and not t.meta_failed


def test_switched_off_nothing_is_asked(site, media_server, tmp_path, queue):
    t, _ = _waiting(media_server, tmp_path, queue)
    on = [False]
    s = _scout(queue, enabled_fn=lambda: on[0])
    assert s.run_once() is None
    assert site.calls == [] and t.filename == "watch.bin"
    on[0] = True                            # read on every round: no restart needed
    assert s.run_once() is t and t.filename == NAMED


def test_without_yt_dlp_nothing_is_asked(media_server, tmp_path, queue, monkeypatch):
    monkeypatch.setattr(yt_dl, "available", lambda: False)
    t, _ = _waiting(media_server, tmp_path, queue)
    asked = []
    s = _scout(queue, look_fn=lambda task, give_up: asked.append(task))
    assert s.run_once() is None
    assert asked == [] and not t.meta_failed


# ---- reading the page -----------------------------------------------------------------
class _Page:
    """A yt_dlp whose extraction is whatever a test says."""

    def __init__(self, monkeypatch, extract, name="video.mp4"):
        self.opts = []
        page = self

        class YDL:
            def __init__(self, opts):
                self.opts = opts
                page.opts.append(opts)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=True):
                assert download is False
                return extract(self.opts)

            def prepare_filename(self, info):
                return os.path.join(os.path.dirname(self.opts["outtmpl"]), name)

        mod = types.ModuleType("yt_dlp")
        mod.YoutubeDL = YDL
        monkeypatch.setitem(sys.modules, "yt_dlp", mod)


def _video(tmp_path, **kw):
    t = T.DownloadTask("https://www.youtube.com/watch?v=abc", str(tmp_path / "watch.bin"),
                       filename="watch.bin", **kw)
    return t


def test_reading_a_page_says_what_the_file_will_be_called_and_how_big(monkeypatch, tmp_path):
    _Page(monkeypatch, lambda o: {"title": "video", "ext": "mp4", "filesize_approx": 4096.0})
    assert yt_dl.look_up(_video(tmp_path)) == (str(tmp_path / "video.mp4"), 4096)


def test_a_playlist_is_read_no_further_than_its_first_entry(monkeypatch, tmp_path):
    page = _Page(monkeypatch, lambda o: {"_type": "playlist", "title": "Mix",
                                         "entries": [{"url": "x"}]})
    assert yt_dl.look_up(_video(tmp_path)) == ("", 0)
    assert page.opts[0]["extract_flat"] == "in_playlist"
    assert page.opts[0]["playlist_items"] == "1"


def test_the_look_is_dropped_when_its_download_starts(monkeypatch, tmp_path):
    gate = threading.Event()
    _Page(monkeypatch, lambda o: gate.wait(10) and {"title": "video", "ext": "mp4"})
    began = time.monotonic()
    try:
        found = yt_dl.look_up(_video(tmp_path), lambda: time.monotonic() - began > 0.3)
    finally:
        gate.set()
    assert found is None
    assert time.monotonic() - began < 3, "waited for the page it had given up on"


def test_a_page_that_takes_too_long_counts_as_unread(monkeypatch, tmp_path):
    gate = threading.Event()
    _Page(monkeypatch, lambda o: gate.wait(10) and {"title": "video", "ext": "mp4"})
    monkeypatch.setattr(yt_dl, "EXTRACT_TIMEOUT", 0.3)
    try:
        with pytest.raises(TimeoutError):
            yt_dl.look_up(_video(tmp_path))
    finally:
        gate.set()


def test_cookies_that_hide_the_formats_are_left_out_of_the_look_too(monkeypatch, tmp_path):
    """As for the download (tests/test_ytdlp_cookie_retry.py): YouTube answers
    a request with cookies with formats this build cannot use."""
    def extract(opts):
        if any(k.lower() == "cookie" for k in opts.get("http_headers") or {}):
            raise RuntimeError("ERROR: [youtube] abc: Requested format is not available.")
        return {"title": "video", "ext": "mp4"}

    page = _Page(monkeypatch, extract)
    t = _video(tmp_path, headers={"Cookie": "SID=x", "User-Agent": "UA"})

    assert yt_dl.look_up(t)[0] == str(tmp_path / "video.mp4")
    assert [sorted(o["http_headers"]) for o in page.opts] == [["Cookie", "User-Agent"],
                                                              ["User-Agent"]]


# ---- kept --------------------------------------------------------------------------------
def test_a_named_video_is_not_asked_about_after_a_restart(site, media_server, tmp_path,
                                                         queue):
    t, _ = _waiting(media_server, tmp_path, queue)
    _scout(queue).run_once()

    back = T.DownloadTask.from_dict(t.to_dict())
    again = QueueManager(queues=[{"name": "Main", "max_concurrent": 1}])
    try:
        again.add_task(back, start=False)
        assert back.filename == NAMED and back.yt_named
        assert _scout(again).run_once() is None
    finally:
        again.shutdown()
    assert len(site.calls) == 1


def test_a_list_saved_before_this_reads_as_not_named():
    t = T.DownloadTask("https://www.youtube.com/watch?v=abc", "C:/dl/watch.bin")
    d = t.to_dict()
    d.pop("yt_named")
    assert T.DownloadTask.from_dict(d).yt_named is False


def test_a_download_that_named_itself_is_not_asked_about_again(site, media_server, tmp_path,
                                                              queue):
    t, _ = _waiting(media_server, tmp_path, queue)
    Downloader(t, segments=4).run()
    assert t.status == T.COMPLETED and t.yt_named
    t.status = T.PAUSED                           # as one paused part-way reads
    assert _scout(queue).run_once() is None
    assert len(site.calls) == 1


# ---- the window ----------------------------------------------------------------------------
def _card_text(t):
    from PySide6.QtWidgets import QApplication
    from gui2.download_card import DownloadCardWidget
    QApplication.instance() or QApplication([])
    card = DownloadCardWidget(t, 1)
    card.update_task(t, 0.0)
    return card.sub.toolTip()               # the whole line; text() is elided to the width


def test_the_card_says_the_page_is_being_read():
    t = T.DownloadTask("https://www.youtube.com/watch?v=abc", "C:/dl/watch.bin",
                       filename="watch.bin")
    assert "Fetching details" not in _card_text(t)
    t.meta_fetching = True
    assert _card_text(t).startswith("Fetching details…") and "Queued" in _card_text(t)


def test_the_card_says_the_page_would_not_read():
    t = T.DownloadTask("https://www.youtube.com/watch?v=abc", "C:/dl/watch.bin",
                       filename="watch.bin", status=T.PAUSED)
    t.meta_failed = True
    assert _card_text(t).startswith("Couldn't fetch details") and "Paused" in _card_text(t)
    t.status = T.DOWNLOADING                # its own run speaks for it now
    assert "Couldn't fetch details" not in _card_text(t)


def test_the_web_list_does_not_call_a_video_a_torrent():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = open(os.path.join(root, "web", "app.js"), encoding="utf-8").read()
    assert 'if (d.fetchingMeta) return "Reading torrent details…";' not in js
    assert "Reading video details…" in js


def test_the_window_runs_the_scout_and_stops_it():
    import inspect
    from gui2 import app
    made = " ".join(inspect.getsource(app.DownloadAppV2.__init__).split())
    assert ("yt_dl.NameScout(lambda: self.queue.tasks, self.queue.while_waiting, "
            "enabled_fn=self._naming_enabled)") in made
    assert "self._names.start()" in made
    assert "self._names.stop()" in inspect.getsource(app.DownloadAppV2._shutdown)


def test_settings_can_switch_it_off():
    import inspect
    from gui2 import app
    from gui2.dialogs import settings, settings_pages
    page = inspect.getsource(settings_pages.PageBuilderMixin._p_downloads)
    assert "name_waiting_videos" in page and "Name videos while they wait" in page
    assert '"name_waiting_videos"' in inspect.getsource(settings.SettingsDialogV2.values)
    win = types.SimpleNamespace(_extras={})
    assert app.DownloadAppV2._naming_enabled(win) is True, "off unless switched on"
    win._extras["name_waiting_videos"] = False
    assert app.DownloadAppV2._naming_enabled(win) is False


@pytest.mark.parametrize("page", ["PRIVACY.md", "docs/privacy.html"])
def test_the_privacy_policy_says_a_waiting_videos_page_is_read(page):
    """A paused download used to mean no contact with its site at all."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = " ".join(open(os.path.join(root, *page.split("/")), encoding="utf-8").read().split())
    assert "While a video waits in the queue, the application reads its page once" in text
    assert "Name videos while they wait" in text


def test_a_name_learned_while_nothing_runs_is_saved():
    """The list is saved on a timer only while something is queued or
    downloading. A list of paused videos would lose its names, and every
    start would read every page again."""
    from gui2.app import DownloadAppV2 as A
    paused = T.DownloadTask("https://www.youtube.com/watch?v=abc", "C:/dl/watch.bin",
                            status=T.PAUSED)
    saves = []
    win = types.SimpleNamespace(queue=types.SimpleNamespace(tasks=[paused]),
                                _names=types.SimpleNamespace(named=0), _names_saved=0,
                                _save_state=lambda: saves.append(1))
    A._autosave_tick(win)
    assert saves == []
    win._names.named = 1
    A._autosave_tick(win)
    A._autosave_tick(win)
    assert saves == [1]


def test_a_download_moved_by_hand_is_no_longer_the_apps_to_file(tmp_path, monkeypatch):
    """"Move to…" is the user choosing the folder. The task still said the app
    sorts it, so a video moved while it waited went back under Video when it
    was named, and a magnet when its metadata came."""
    from PySide6.QtWidgets import QApplication, QFileDialog
    from gui2.app_actions import ActionsMixin
    QApplication.instance() or QApplication([])
    base, mine = tmp_path / "dl", tmp_path / "mine"
    (base / "Other").mkdir(parents=True)
    mine.mkdir()
    t = T.DownloadTask("https://www.youtube.com/watch?v=abc", str(base / "Other" / "watch.bin"),
                       filename="watch.bin")
    t.sort_base = str(base)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(mine)))
    win = types.SimpleNamespace(save_dir=str(base), _save_state=lambda: None,
                                refresh=lambda: None,
                                _toasts=types.SimpleNamespace(show=lambda *a, **k: None))

    ActionsMixin._move_task_files(win, t)

    assert t.save_path == str(mine / "watch.bin")
    assert t.sort_base == ""


# ---- the real yt-dlp ---------------------------------------------------------------------------
def test_with_the_real_ytdlp_the_name_given_while_waiting_is_the_downloads(
        real_ytdlp, video_page, media_server, make_payload, tmp_path, queue):
    data = media_server.put("media/clip.mp4", make_payload(SIZE), etag='"v1"')
    page = video_page("/watch/3", media_server.url("media/clip.mp4"))
    base = str(tmp_path / "dl")
    os.makedirs(base)
    folder, sort_base = utils.place_download(base, page, "3.bin")
    t = T.DownloadTask(page, utils.unique_path(folder, "3.bin"), filename="3.bin")
    t.sort_base, t.use_ytdlp = sort_base, True
    queue.add_task(t, start=False)

    assert _scout(queue).run_once() is t

    named = t.save_path
    assert os.path.dirname(named) == os.path.join(base, "Video"), named
    assert os.path.basename(named).startswith(TITLE) and named.endswith(".mp4"), named
    assert os.listdir(os.path.join(base, "Video")) == [], "something was written"
    assert not [e for e in media_server.log if e["method"] == "GET"], (
        "the video was fetched to learn its name")

    Downloader(t, segments=4).run()

    assert t.status == T.COMPLETED, t.error
    assert t.save_path == named, "its download called it something else"
    assert open(t.save_path, "rb").read() == data
