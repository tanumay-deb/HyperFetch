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


def test_date_token():
    import time
    now = time.time()
    recent = _mk("fresh.zip", 1000, T.PAUSED); recent.added = now - 3600          # 1h ago
    old = _mk("ancient.zip", 1000, T.PAUSED); old.added = now - 20 * 86400         # 20 days ago
    pool = [recent, old]
    assert names(search.filter_tasks(pool, "date:today")) == ["fresh.zip"]
    assert names(search.filter_tasks(pool, "date:7d")) == ["fresh.zip"]
    assert names(search.filter_tasks(pool, "date:30d")) == ["ancient.zip", "fresh.zip"]
    # bad date -> treated as text (matches nothing here)
    assert search.filter_tasks(pool, "date:whenever") == []


def test_combined_advanced():
    import time
    t = _mk("clip.mkv", 2_000_000_000, T.DOWNLOADING); t.added = time.time() - 1800
    assert names(search.filter_tasks([t], "status:downloading ext:mkv date:today size:>1gb")) == ["clip.mkv"]
