"""What went wrong with a download, in words.

The engines record a failure as they meet it, and that is often an
exception's own text: "HLS parse failed: HTTPSConnectionPool(host='…',
port=443): Max retries exceeded with url: … (Caused by
NameResolutionError(…))", "segment 13/292 failed: ('Connection aborted.',
RemoteDisconnected(…))", "Torrent engine unavailable (aria2 daemon busy:
<urlopen error [WinError 10061] …>)". Right for the Logs tab, where the task
keeps it; no use on the card. plain() says it in words for the card, the
failure notice and the web list. The shapes are the ones a real log held
(2026-08-25 .. 10-16, tests/test_plain_errors.py).

Anything it does not know is returned as it was: a vaguer sentence in its
place would hide the only clue there is.
"""
import re

# What failed, in front of why it failed ("<what>: <why>"). An empty label:
# the why says it all.
_WHAT = [
    (re.compile(r"^HLS parse failed: ", re.I), "Could not read the video's playlist"),
    (re.compile(r"^HLS key fetch failed: ", re.I), "Could not get the video's key"),
    (re.compile(r"^HLS init segment failed: ", re.I), "Could not get the start of the video"),
    (re.compile(r"^segment (?P<n>\d+)/(?P<of>\d+) failed: ", re.I), "Part {n} of {of} failed"),
    (re.compile(r"^finalize failed \([^)]*\): ", re.I), "Could not save the finished file"),
    (re.compile(r"^cannot create folder: ", re.I), "Could not create the folder"),
    (re.compile(r"^yt-dlp: (?:ERROR: )?", re.I), "yt-dlp"),
    (re.compile(r"^(?:disk error|torrent failed): ", re.I), ""),
]


def _http(code):
    if code == 403:
        return "The site refused it (403) — the link may have expired"
    if code == 404:
        return "It is not found on the server (404)"
    if code in (401, 407):
        return "The site wants a login (%d)" % code
    if code == 429:
        return "The site is limiting requests (429) — try again later"
    if 500 <= code < 600:
        return "The server had a problem (%d) — try again later" % code
    return "The site answered %d" % code


def _why(text):
    """Why it failed, in words - or None when the text is not a shape known
    here."""
    low = text.lower()
    m = re.search(r"host='([^']+)'", text)
    host = m.group(1) if m else ""
    if "disk cache flush" in low or "file write failure" in low:
        return "Could not write the torrent to the drive — is it full or disconnected?"
    if "winerror 112" in low or "not enough space on the disk" in low or "no space left" in low:
        return "The drive is full"
    if "winerror 32]" in low or "being used by another process" in low:
        return "The file is open in another program — close it, then Resume"
    if "winerror 5]" in low or "access is denied" in low or "permission denied" in low:
        return "HyperFetch is not allowed to write there — pick another folder, then Resume"
    if "winerror 21]" in low or "device is not ready" in low:
        return "The drive is not ready — is it connected?"
    if "winerror 3]" in low or "cannot find the path specified" in low:
        return "The folder is not there any more — is its drive connected?"
    if "winerror 2]" in low or "cannot find the file specified" in low:
        if ".hfdownload" in low:
            return "The download's temporary file is gone — Resume starts it again"
        return "A file the download needs is gone"
    if "certificate_verify_failed" in low or "certificate verify failed" in low:
        return "The site's security certificate could not be checked"
    if ("nameresolutionerror" in low or "getaddrinfo failed" in low
            or "name or service not known" in low):
        return ("Could not find %s — check the connection, or switch on "
                "\"Get past provider blocks\"" % (host or "the site"))
    if "timed out" in low or "timeouterror" in low:
        return "%s did not answer in time" % (host or "The site")
    if ("newconnectionerror" in low or "10061" in low or "connection refused" in low
            or "failed to establish a new connection" in low):
        return "Could not connect to %s" % (host or "the site")
    if ("connection aborted" in low or "remotedisconnected" in low
            or "connection reset" in low or "10054" in low):
        return "The server closed the connection"
    m = re.search(r"\b(\d{3}) (?:Client|Server) Error|HTTP Error (\d{3})", text)
    if m:
        return _http(int(m.group(1) or m.group(2)))
    return None


def plain(raw):
    """`raw` - a task's error - in words; as it was when it is not a shape
    known here, and "" for none."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("torrent engine unavailable ("):
        return ("Torrent engine is not answering — it may be restarting. "
                "Resume in a minute.")
    for pattern, label in _WHAT:
        m = pattern.match(raw)
        if m:
            why = _why(raw[m.end():])
            if why is None:
                return raw
            label = label.format(**m.groupdict())
            return "%s: %s" % (label, why[0].lower() + why[1:]) if label else why
    return _why(raw) or raw
