"""Search-query token parsing/filtering (gui2/search.py) — pure, no Qt."""
import task as T
from gui2 import search


def _mk(name, size=0, status=T.QUEUED, url=""):
    t = T.DownloadTask(url or f"http://x/{name}", f"C:/dl/{name}",
                       filename=name, total_size=size, status=status)
    return t


TASKS = [
    _mk("ubuntu-24.04.iso", 5_000_000_000, T.DOWNLOADING),
    _mk("song.mp3", 5_000_000, T.COMPLETED),
    _mk("movie.mp4", 1_500_000_000, T.PAUSED),
    _mk("report.pdf", 800_000, T.ERROR),
    _mk("clip.mkv", 300_000_000, T.DOWNLOADING),
]


def names(tasks):
    return sorted(t.filename for t in tasks)


def test_empty_query_returns_all():
    assert len(search.filter_tasks(TASKS, "")) == len(TASKS)
    assert len(search.filter_tasks(TASKS, "   ")) == len(TASKS)


def test_plain_text_matches_name():
    assert names(search.filter_tasks(TASKS, "movie")) == ["movie.mp4"]
    # case-insensitive
    assert names(search.filter_tasks(TASKS, "UBUNTU")) == ["ubuntu-24.04.iso"]


def test_status_token():
    assert names(search.filter_tasks(TASKS, "status:downloading")) == ["clip.mkv", "ubuntu-24.04.iso"]
    assert names(search.filter_tasks(TASKS, "status:completed")) == ["song.mp3"]
    assert names(search.filter_tasks(TASKS, "status:failed")) == ["report.pdf"]


def test_category_token():
    # mp4/mkv -> Video, mp3 -> Music
    assert names(search.filter_tasks(TASKS, "category:video")) == ["clip.mkv", "movie.mp4"]
    assert names(search.filter_tasks(TASKS, "cat:music")) == ["song.mp3"]


def test_size_token():
    assert names(search.filter_tasks(TASKS, "size:>1gb")) == ["movie.mp4", "ubuntu-24.04.iso"]
    assert names(search.filter_tasks(TASKS, "size:<10mb")) == ["report.pdf", "song.mp3"]
    assert names(search.filter_tasks(TASKS, "size:>=300mb")) == ["clip.mkv", "movie.mp4", "ubuntu-24.04.iso"]


def test_combined_tokens_and_text():
    # downloading AND video AND name contains 'clip'
    assert names(search.filter_tasks(TASKS, "status:downloading category:video clip")) == ["clip.mkv"]
    # downloading AND >1gb
    assert names(search.filter_tasks(TASKS, "status:downloading size:>1gb")) == ["ubuntu-24.04.iso"]


def test_unparseable_size_falls_back_to_text():
    # "sizefoo" isn't a valid size token -> treated as plain text (matches nothing)
    assert search.filter_tasks(TASKS, "size:huge") == []


def test_ext_token():
    assert names(search.filter_tasks(TASKS, "ext:iso")) == ["ubuntu-24.04.iso"]
    assert names(search.filter_tasks(TASKS, "ext:mp4")) == ["movie.mp4"]
    assert search.filter_tasks(TASKS, "ext:nope") == []


def _today_ts(ago=3600):
    """An epoch `ago` seconds back, but never earlier than midnight today.

    date:today means "since local midnight", so a plain now-3600 lands on
    YESTERDAY whenever the suite runs in the first hour after midnight — these
    tests failed for an hour every night, and CI only ever ran mid-afternoon
    UTC so it never caught it.
    """
    import datetime
    import time
    midnight = time.mktime(datetime.date.today().timetuple())
    return max(midnight + 1, time.time() - ago)


def test_date_token():
    import time
    now = time.time()
    recent = _mk("fresh.zip", 1000, T.PAUSED); recent.added = _today_ts()          # today
    old = _mk("ancient.zip", 1000, T.PAUSED); old.added = now - 20 * 86400         # 20 days ago
    pool = [recent, old]
    assert names(search.filter_tasks(pool, "date:today")) == ["fresh.zip"]
    assert names(search.filter_tasks(pool, "date:7d")) == ["fresh.zip"]
    assert names(search.filter_tasks(pool, "date:30d")) == ["ancient.zip", "fresh.zip"]
    # bad date -> treated as text (matches nothing here)
    assert search.filter_tasks(pool, "date:whenever") == []


def test_combined_advanced():
    t = _mk("clip.mkv", 2_000_000_000, T.DOWNLOADING); t.added = _today_ts(1800)
    assert names(search.filter_tasks([t], "status:downloading ext:mkv date:today size:>1gb")) == ["clip.mkv"]


# ---- history records (dicts) share the task query syntax ----
def _rec(name, size=1000, cat="Video", url="https://x/f", when=0.0):
    return {"filename": name, "size": size, "category": cat, "url": url,
            "completed_at": when, "path": "C:/d/" + name}


def test_filter_records_plain_word():
    recs = [_rec("Interstellar.mkv"), _rec("report.pdf", cat="Documents")]
    assert [r["filename"] for r in search.filter_records(recs, "inter")] == ["Interstellar.mkv"]
    assert len(search.filter_records(recs, "")) == 2


def test_filter_records_tokens():
    recs = [_rec("a.mkv", size=5_000_000_000, cat="Video"),
            _rec("b.zip", size=1000, cat="Compressed")]
    assert [r["filename"] for r in search.filter_records(recs, "category:compressed")] == ["b.zip"]
    assert [r["filename"] for r in search.filter_records(recs, "ext:mkv")] == ["a.mkv"]
    assert [r["filename"] for r in search.filter_records(recs, "size:>1gb")] == ["a.mkv"]


def test_filter_records_date_uses_completed_at():
    import time
    now = time.time()
    recs = [_rec("new.mkv", when=now), _rec("old.mkv", when=now - 40 * 86400)]
    assert [r["filename"] for r in search.filter_records(recs, "date:7d")] == ["new.mkv"]


def test_filter_records_url_is_searchable():
    recs = [_rec("a.mkv", url="https://cdn.example.com/movie"), _rec("b.mkv", url="https://other/x")]
    assert [r["filename"] for r in search.filter_records(recs, "example.com")] == ["a.mkv"]


def test_filter_records_status_token_is_completed_only():
    """History holds only completed downloads, so status:completed matches all
    and any other status matches nothing (rather than silently ignoring it)."""
    recs = [_rec("a.mkv"), _rec("b.mkv")]
    assert len(search.filter_records(recs, "status:completed")) == 2
    assert search.filter_records(recs, "status:failed") == []


def test_filter_records_missing_fields_dont_crash():
    assert search.filter_records([{}], "anything") == []
    assert len(search.filter_records([{}], "")) == 1
