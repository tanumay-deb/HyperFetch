"""A failed download says what went wrong in words, not in an exception's text.

The card is where a failure is read, and it carried the engines' raw text:
"Torrent engine unavailable (aria2 daemon busy: <urlopen error [WinError
10061] No connection could be made because the target machine actively
refused it>) — Resume to retry", "HLS parse failed:
HTTPSConnectionPool(host='…', port=443): Max retries exceeded with url: …",
"segment 4/120 failed: ('Connection aborted.', RemoteDisconnected(…))". The
messages below are the shapes found in a real log (2026-08-25 .. 10-16), with
other hosts. plain_errors.plain() turns them into words; the raw text stays
on the task, in its Logs tab and under the card's tooltip.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import plain_errors
import task as T

POOL = ("HTTPSConnectionPool(host='cdn.example.net', port=443): Max retries exceeded "
        "with url: /v/clip.m3u8 (Caused by %s)")


@pytest.mark.parametrize("raw,says", [
    # the torrent engine
    ("Torrent engine unavailable (aria2 daemon busy: <urlopen error [WinError 10061] No "
     "connection could be made because the target machine actively refused it>) — Resume "
     "to retry", "Torrent engine is not answering"),
    ("torrent failed: Write disk cache flush failure index=608",
     "Could not write the torrent to the drive"),
    # the network, by what failed
    ("HLS parse failed: " + POOL % "NameResolutionError(\"Failed to resolve 'cdn.example.net'"
     " ([Errno 11001] getaddrinfo failed)\")", "Could not find cdn.example.net"),
    ("HLS parse failed: " + POOL % "ConnectTimeoutError(<urllib3.connection.HTTPSConnection "
     "object at 0x0>, 'Connection to cdn.example.net timed out. (connect timeout=15)')",
     "cdn.example.net did not answer"),
    ("segment 7/1862 failed: " + POOL % "NewConnectionError('<urllib3.connection."
     "HTTPSConnection object at 0x0>: Failed to establish a new connection: [WinError "
     "10061] No connection could be made')", "Could not connect to cdn.example.net"),
    ("segment 13/292 failed: ('Connection aborted.', RemoteDisconnected('Remote end closed "
     "connection without response'))", "the server closed the connection"),
    ("HLS parse failed: 403 Client Error: Forbidden for url: https://cdn.example.net/v/x.m3u8",
     "refused"),
    ("HLS parse failed: 404 Client Error: Not Found for url: https://cdn.example.net/v/x.m3u8",
     "not found"),
    ("yt-dlp: ERROR: unable to download video data: HTTP Error 403: Forbidden", "refused"),
    ("HLS parse failed: 503 Server Error: Service Unavailable for url: https://x.example/a",
     "server had a problem"),
    ("HLS parse failed: " + POOL % "SSLError(SSLCertVerificationError(1, '[SSL: "
     "CERTIFICATE_VERIFY_FAILED] certificate verify failed'))", "certificate"),
    # the disk
    ("disk error: [WinError 112] There is not enough space on the disk", "drive is full"),
    ("disk error: [WinError 2] The system cannot find the file specified: "
     "'C:\\\\Users\\\\x\\\\AppData\\\\Local\\\\Temp\\\\ab.hfdownload'", "temporary file"),
    ("finalize failed (pick another folder, then Resume): [WinError 5] Access is denied: "
     "'G:\\\\HF\\\\Video\\\\clip.mp4.hfmove'", "not allowed to write"),
    ("disk error: [WinError 32] The process cannot access the file because it is being used "
     "by another process: 'G:\\\\x.mp4'", "open in another program"),
    ("disk error: [WinError 3] The system cannot find the path specified: 'G:\\\\HF\\\\Video'",
     "folder is not there"),
    ("disk error: [WinError 21] The device is not ready", "drive is not ready"),
])
def test_what_went_wrong_is_said_in_words(raw, says):
    plain = plain_errors.plain(raw)
    assert says.lower() in plain.lower(), plain
    for jargon in ("WinError", "HTTPSConnectionPool", "urlopen", "Errno", "Caused by",
                   "object at", "RemoteDisconnected", "Client Error"):
        assert jargon not in plain, (jargon, plain)


def test_what_the_failure_was_about_is_kept():
    """The part, the playlist, the save: what failed, then why."""
    seg = plain_errors.plain("segment 13/292 failed: ('Connection aborted.', "
                             "RemoteDisconnected('Remote end closed connection'))")
    assert "13" in seg and "292" in seg
    assert plain_errors.plain("HLS parse failed: 403 Client Error: Forbidden for url: "
                              "https://a.example/x.m3u8").lower().startswith(
        "could not read the video's playlist")
    assert "pick another folder" in plain_errors.plain(
        "finalize failed (pick another folder, then Resume): [WinError 5] Access is denied")


@pytest.mark.parametrize("raw", [
    "Connection lost — Resume to retry",
    "No peers found yet — the swarm may be offline or still waking up. Resume to try again.",
    "Not found (HTTP 404) — the file may have moved",
    "SHA-256 mismatch — the file may be corrupt",
])
def test_what_is_already_in_words_is_left_alone(raw):
    assert plain_errors.plain(raw) == raw


def test_what_it_does_not_know_is_left_as_it_was():
    """An unknown message is the only clue there is: never swapped for a vaguer one."""
    raw = "yt-dlp: [generic] abc: Unsupported URL: https://a.example/x"
    assert plain_errors.plain(raw) == raw
    assert plain_errors.plain("") == "" and plain_errors.plain(None) == ""


# ---- where it is read ------------------------------------------------------------------
RAW = ("segment 13/292 failed: ('Connection aborted.', RemoteDisconnected('Remote end "
       "closed connection without response'))")


def _failed():
    t = T.DownloadTask("https://a.example/v.m3u8", "C:/dl/v.mp4", filename="v.mp4")
    t.status = T.ERROR
    t.error = RAW
    return t


def test_the_card_says_it_in_words_and_keeps_the_details_under_its_tooltip():
    from PySide6.QtWidgets import QApplication
    from gui2.download_card import DownloadCardWidget
    QApplication.instance() or QApplication([])
    t = _failed()
    card = DownloadCardWidget(t, 1)
    card.update_task(t, 0.0)
    assert card.sub.text() and "RemoteDisconnected" not in card.sub._full
    assert card.sub._full == plain_errors.plain(RAW)
    assert RAW in card.sub.toolTip(), "the details are nowhere to be found"


def test_the_web_list_says_it_in_words_and_carries_the_details(tmp_path):
    import web_auth
    from api_server import create_app
    from queue_manager import QueueManager
    q = QueueManager()
    q.shutdown()                       # nothing is to start
    q.add_task(_failed(), start=False)
    app = create_app(q, str(tmp_path), pending=None, token="tok")
    app.config["TESTING"] = True
    c = app.test_client()
    web_auth.set_password("correct horse battery", user="admin")
    web_auth.set_enabled(True)
    lan = {"REMOTE_ADDR": "192.168.1.50"}
    assert c.post("/api/login", json={"username": "admin", "password": "correct horse battery"},
                  environ_overrides=lan).status_code == 200
    row = c.get("/api/downloads", environ_overrides=lan).get_json()["downloads"][0]
    assert row["error"] == plain_errors.plain(RAW)
    assert row["errorDetail"] == RAW


def test_the_failure_notice_says_it_in_words():
    import inspect
    from gui2 import app
    src = inspect.getsource(app.DownloadAppV2._check_completions)
    assert "plain_errors.plain(t.error" in src
