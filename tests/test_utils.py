"""utils: filename derivation, unique paths, categories, rate limiter."""
import os
import time
import threading

import pytest

import utils


def test_filename_from_url_basic():
    assert utils.filename_from_url("https://x.com/path/file.zip") == "file.zip"


def test_filename_from_url_query_fragment():
    assert utils.filename_from_url("https://x.com/a/movie.mp4?token=1#t") == "movie.mp4"


def test_filename_from_url_suggested_wins():
    assert utils.filename_from_url("https://x.com/a", "custom.bin") == "custom.bin"


def test_filename_from_url_encoded():
    assert utils.filename_from_url("https://x.com/My%20File.pdf") == "My File.pdf"


def test_filename_from_url_extensionless_gets_bin():
    n = utils.filename_from_url("https://x.com/download")
    assert n.endswith(".bin")


def test_filename_illegal_chars_sanitized():
    n = utils.filename_from_url("https://x.com/", 'a<b>c:d"e|f?g*h.txt')
    for ch in '<>:"|?*':
        assert ch not in n


def test_safe_filename_windows_reserved():
    # reserved device names (even with an extension) get a prefix so the OS accepts them
    assert utils.safe_filename("nul.zip") == "_nul.zip"
    assert utils.safe_filename("CON") == "_CON"
    assert utils.safe_filename("com1.txt") == "_com1.txt"
    # a name that merely contains a reserved word is fine
    assert utils.safe_filename("console.log") == "console.log"


def test_safe_filename_keeps_hash_and_extension():
    # a media title with '#' must NOT be truncated at the '#', and the extension
    # must survive — yt-dlp titles reach here via the category move (regression)
    out = utils.safe_filename("Crush GMAT | 3-hour NonStop #GMAT Crash-Course.mp4")
    assert out.endswith(".mp4")
    assert "#GMAT Crash-Course" in out
    assert "|" not in out                # illegal char replaced, not split


def test_safe_filename_question_mark_not_split():
    out = utils.safe_filename("what is this? v2.mkv")
    assert out.endswith(".mkv") and "?" not in out


def test_safe_filename_length_capped():
    long = "a" * 400 + ".zip"
    out = utils.safe_filename(long)
    assert len(out) <= 200 and out.endswith(".zip")


def test_safe_filename_traversal():
    assert "/" not in utils.safe_filename("../../etc/passwd")
    assert "\\" not in utils.safe_filename("..\\..\\windows\\system32")
    assert utils.safe_filename("..") == "download"


def test_unique_path_collisions(tmp_path):
    d = str(tmp_path)
    p1 = utils.unique_path(d, "a.zip")
    open(p1, "w").close()
    p2 = utils.unique_path(d, "a.zip")
    open(p2, "w").close()
    p3 = utils.unique_path(d, "a.zip")
    assert os.path.basename(p2) == "a (1).zip"
    assert os.path.basename(p3) == "a (2).zip"


@pytest.mark.parametrize("name,cat", [
    ("clip.mp4", "Video"), ("clip.ts", "Video"), ("list.m3u8", "Video"),
    ("song.mp3", "Music"), ("a.zip", "Compressed"), ("setup.exe", "Programs"),
    ("doc.pdf", "Documents"),
])
def test_category_dir(tmp_path, name, cat):
    out = utils.get_category_dir(str(tmp_path), name)
    assert os.path.basename(out) == cat
    assert os.path.isdir(out)


def test_category_unknown_stays_base(tmp_path):
    assert utils.get_category_dir(str(tmp_path), "weird.xyz") == str(tmp_path)
    assert utils.get_category_dir(str(tmp_path), "") == str(tmp_path)


def test_json_roundtrip_and_corruption(tmp_path):
    p = str(tmp_path / "x.json")
    utils.save_json(p, {"a": 1})
    assert utils.load_json(p, None) == {"a": 1}
    assert not os.path.exists(p + ".tmp")  # atomic temp cleaned
    with open(p, "w") as f:
        f.write("not json{{")
    assert utils.load_json(p, "DEF") == "DEF"
    assert utils.load_json(str(tmp_path / "missing.json"), 42) == 42


# ---------------------------------------------------------------- rate limiter
def test_limiter_amount_exceeds_capacity_no_hang():
    rl = utils.RateLimiter()
    rl.set_limit(32 * 1024)          # 32 KB/s
    t0 = time.monotonic()
    rl.wait(65536)                   # 64 KB chunk > capacity
    assert time.monotonic() - t0 < 5


def test_limiter_rate_accuracy():
    rl = utils.RateLimiter()
    rl.set_limit(64 * 1024)
    t0 = time.monotonic()
    for _ in range(2):
        rl.wait(65536)               # 128 KB @ 64 KB/s ~ 2s
    el = time.monotonic() - t0
    assert 0.5 < el < 4


def test_limiter_zero_is_noop():
    rl = utils.RateLimiter()
    rl.set_limit(0)
    t0 = time.monotonic()
    rl.wait(10_000_000)
    assert time.monotonic() - t0 < 0.05


def test_limiter_thread_safe():
    rl = utils.RateLimiter()
    rl.set_limit(64 * 1024)
    done = []
    def worker():
        rl.wait(16384)
        done.append(1)
    ts = [threading.Thread(target=worker) for _ in range(4)]
    t0 = time.monotonic()
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=6)
    assert len(done) == 4
    assert time.monotonic() - t0 < 6


def test_limiter_live_change_unblocks():
    rl = utils.RateLimiter()
    rl.set_limit(16 * 1024)
    done = threading.Event()
    threading.Thread(target=lambda: (rl.wait(256 * 1024), done.set()),
                     daemon=True).start()
    time.sleep(1.0)
    rl.set_limit(4 * 1024 * 1024)    # raise the limit mid-wait
    assert done.wait(4), "raising the limit should let the waiter finish"
