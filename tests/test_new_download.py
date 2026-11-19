"""New Download dialog after the trim.

Every row on this screen sits between the user and the thing they asked for, so
these assert that the removed ones really are gone and that the remaining ones
only appear where they change the outcome.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from gui2.dialogs.new_download import NewDownloadDialog, URL_TAB, MAGNET_TAB, TORRENT_TAB

MAGNET = "magnet:?xt=urn:btih:08ada5a7a6183aae1e09d831df6748d566095a10&dn=Sintel"


def _app():
    return QApplication.instance() or QApplication([])


def _dlg(url="", suggested="", **kw):
    _app()
    host = QWidget()
    d = NewDownloadDialog(host, "E:/HyperFetch", ["Main", "Night"], 8,
                          url=url, suggested=suggested, **kw)
    d._host_ref = host
    return d


def test_there_is_no_duplicate_title_in_the_body():
    """The title bar already says New Download; a second copy was the tallest
    thing on the dialog."""
    from PySide6.QtWidgets import QLabel
    d = _dlg()
    texts = [l.text() for l in d.findChildren(QLabel)]
    assert texts.count("New Download") == 0
    assert d.windowTitle() == "New Download"


def test_priority_is_gone_but_tasks_still_get_one():
    d = _dlg(url="https://x/a.zip")
    assert not hasattr(d, "prio")
    assert d.values()["priority"] == 0        # the queue still needs the field


def test_the_dialog_is_narrower_than_it_was():
    d = _dlg()
    assert d.minimumWidth() <= 460            # was 560


def test_a_magnet_hides_the_fields_that_decide_nothing():
    """A torrent names itself and is not categorised, so offering those fields
    invites a value that is then ignored."""
    d = _dlg(url=MAGNET, suggested="magnet:")
    assert d.tabs.currentIndex() == MAGNET_TAB
    assert d.name_edit.isHidden()
    assert d.cat.isHidden()


def test_the_url_tab_keeps_them():
    d = _dlg(url="https://x/a.zip")
    assert d.tabs.currentIndex() == URL_TAB
    assert not d.name_edit.isHidden()
    assert not d.cat.isHidden()


def test_a_useless_magnet_filename_is_never_carried_over():
    """Callers derive a name from the URL and hand us literally "magnet:".
    Left in the box, switching to the URL tab would submit that as the name."""
    d = _dlg(url=MAGNET, suggested="magnet:")
    assert d.name_edit.text() == ""
    d.tabs.setCurrentIndex(URL_TAB)
    d.url_edit.setText("https://x/real.zip")
    assert d.values()["filename"] == ""


def test_a_torrent_tab_never_forces_a_filename():
    d = _dlg(url=MAGNET)
    d.name_edit.setText("something the user typed earlier")
    assert d.values()["filename"] == ""


def test_the_destination_hint_only_appears_when_it_says_something_new():
    """Repeating the folder shown directly above it is a line of noise."""
    d = _dlg(url="https://x/notes.xyz", categorize=False)
    assert d.dest_hint.isHidden(), "with sorting off the folder above is the whole answer"
    d = _dlg(url="https://x/notes.txt")
    d.cat.setCurrentText("Video")                 # explicit subfolder
    assert not d.dest_hint.isHidden()
    assert "Video" in d.dest_hint.text()


def test_the_hint_names_other_for_a_type_nobody_knows():
    """Such a file is saved in Other now, not in the folder shown above."""
    d = _dlg(url="https://x/notes.xyz")
    d.cat.setCurrentText("Auto")
    assert not d.dest_hint.isHidden()
    assert d.dest_hint.text().endswith("Other")


def test_the_hint_says_where_a_torrent_will_be_filed(tmp_path):
    """A .torrent says what it holds; a magnet does not until its metadata is in."""
    from test_sorting import make_torrent
    src = make_torrent(tmp_path / "s.torrent", "Show.S01", files=[("ep1.mkv", 500)])
    d = _dlg(url=src)
    assert d.tabs.currentIndex() == TORRENT_TAB
    assert not d.dest_hint.isHidden() and d.dest_hint.text().endswith("Video")
    m = _dlg(url=MAGNET)
    assert m.tabs.currentIndex() == MAGNET_TAB
    assert not m.dest_hint.isHidden()
    assert "once its contents are known" in m.dest_hint.text()
    off = _dlg(url=MAGNET, categorize=False)
    assert off.dest_hint.isHidden()


def test_dont_show_again_is_reported_and_defaults_off():
    d = _dlg(url="https://x/a.zip")
    assert d.values()["skip_dialog"] is False
    d.skip_next.setChecked(True)
    assert d.values()["skip_dialog"] is True


# --------------------------------------------------------- skipping the dialog
def _stub_app(extras=None):
    import types
    from gui2.app import DownloadAppV2
    stub = types.SimpleNamespace(
        save_dir="E:/HyperFetch",
        segments=8,
        _extras=dict(extras or {}),
        queue=types.SimpleNamespace(queues={"Main": None, "Night": None}),
    )
    return stub, DownloadAppV2


def test_skipping_the_dialog_matches_what_the_dialog_would_have_returned():
    """Turning the prompt off must change HOW a download is added, never where
    it lands — otherwise the setting quietly relocates people's files."""
    stub, cls = _stub_app({"default_queue": "Night", "auto_start": True})
    quick = cls._quick_values(stub, "https://x/a.zip", "a.zip", {})

    d = _dlg(url="https://x/a.zip", suggested="a.zip")
    d.q.setCurrentText("Night")
    shown = d.values()

    for key in ("url", "save_dir", "filename", "category", "queue",
                "priority", "connections", "start_now", "yt_format"):
        assert quick[key] == shown[key], f"{key} differs when the dialog is skipped"


def test_the_quick_path_does_not_invent_a_torrent_filename():
    stub, cls = _stub_app()
    v = cls._quick_values(stub, MAGNET, "magnet:", {})
    assert v["filename"] == ""


def test_the_quick_path_falls_back_to_a_real_queue():
    """A default_queue naming a deleted queue must not produce a task in a
    queue that does not exist."""
    stub, cls = _stub_app({"default_queue": "Deleted"})
    assert cls._quick_values(stub, "https://x/a.zip", "", {})["queue"] == "Main"


# ------------------------------------------------ server filename probe (B7)
def _head_server(disposition="", gate=None):
    import http.server
    import threading
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_HEAD(self):
            seen.append(("HEAD", self.path, dict(self.headers)))
            if gate is not None:
                gate.wait(5)
            self.send_response(200)
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", "1024")
            self.end_headers()

        def do_GET(self):
            seen.append(("GET", self.path, dict(self.headers)))
            self.send_response(405)
            self.end_headers()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, seen


def test_new_download_fills_in_the_servers_name_as_suggested(tmp_path):
    """B7: a link like /download.php asks the server with HEAD on a thread,
    fills in the name and category folder, and counts it as suggested (not
    typed) so task.name_chosen stays empty."""
    import time
    import types
    import utils
    from gui2.app import DownloadAppV2

    httpd, seen = _head_server('attachment; filename="report.pdf"')
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/download.php?id=7"
        app = _app()
        host = QWidget()
        d = NewDownloadDialog(host, str(tmp_path), ["Main"], 8,
                              url=url, suggested="", headers={"Cookie": "sid=1"},
                              categorize=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and d.name_edit.text() != "report.pdf":
            app.processEvents()
            time.sleep(0.02)

        assert d.name_edit.text() == "report.pdf"
        assert not d.dest_hint.isHidden()
        assert utils.category_for("report.pdf") in d.dest_hint.text()
        assert [m for m, _, _ in seen] == ["HEAD"], f"expected only HEAD, got {seen}"
        assert seen[0][2].get("Cookie") == "sid=1"

        v = d.values()
        added = []
        queue = types.SimpleNamespace(queues={"Main": None}, tasks=[], segments=8,
                                      add_task=lambda t, start=True: added.append(t))
        win = types.SimpleNamespace(
            save_dir=str(tmp_path), segments=8, queue=queue,
            _extras={"categorize": True},
            _toasts=types.SimpleNamespace(show=lambda *a, **k: None),
            _save_state=lambda: None, refresh=lambda: None)
        DownloadAppV2._continue_add_download(win, v, url, "", ask=True)
        assert len(added) == 1
        assert added[0].filename == "report.pdf"
        assert added[0].name_chosen == ""
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_new_download_does_not_overwrite_a_name_typed_before_the_probe_answers(tmp_path):
    """B7: if the user types a filename before the HEAD answer arrives, the
    server's name must not overwrite it."""
    import threading
    import time

    gate = threading.Event()
    httpd, seen = _head_server('attachment; filename="report.pdf"', gate=gate)
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/download.php?id=7"
        app = _app()
        host = QWidget()
        d = NewDownloadDialog(host, str(tmp_path), ["Main"], 8,
                              url=url, suggested="", categorize=True)
        d.name_edit.setText("My Notes.pdf")
        gate.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not seen:
            app.processEvents()
            time.sleep(0.02)
        for _ in range(10):
            app.processEvents()
            time.sleep(0.02)

        assert d.name_edit.text() == "My Notes.pdf"
        assert d.values().get("suggested", "") != "My Notes.pdf"
    finally:
        gate.set()
        httpd.shutdown()
        httpd.server_close()
