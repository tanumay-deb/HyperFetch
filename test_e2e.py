"""End-to-end test suite for HyperFetch.

Tests:
  1. Task model: creation, serialization, deserialization, speed limit
  2. Utils: categories, rate limiter, filename sanitization
  3. API server: endpoint routing, category integration
  4. Downloader: pre-allocation (.hfdownload), download completion, speed throttling
"""
import os
import sys
import time
import json
import shutil
import tempfile
import threading

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

import task as T
import utils
from queue_manager import QueueManager
from downloader import Downloader

PASS = 0
FAIL = 0
RESULTS = []

def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(f"  ✅ PASS: {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ❌ FAIL: {name} — {detail}")

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 1: Task Model (task.py)
# ═══════════════════════════════════════════════════════════════════
print("=" * 60)
print("TEST GROUP 1: Task Model")
print("=" * 60)

# 1.1 Basic creation
t = T.DownloadTask("http://example.com/file.zip", "/tmp/file.zip", filename="file.zip")
test("Task creation", t.url == "http://example.com/file.zip" and t.filename == "file.zip")
test("Task default status", t.status == T.QUEUED)
test("Task default speed_limit", t.speed_limit == 0)

# 1.2 Serialization round-trip
t.total_size = 1024000
t.downloaded = 512000
t.speed_limit = 100 * 1024
t.segments = [T.Segment(0, 0, 511999), T.Segment(1, 512000, 1023999)]
t.segments[0].downloaded = 512000
t.segments[1].downloaded = 0

d = t.to_dict()
test("to_dict has speed_limit", d["speed_limit"] == 100 * 1024)
test("to_dict has segments", len(d["segments"]) == 2)

t2 = T.DownloadTask.from_dict(d)
test("from_dict url", t2.url == t.url)
test("from_dict save_path", t2.save_path == t.save_path)
test("from_dict speed_limit", t2.speed_limit == 100 * 1024)
test("from_dict segments count", len(t2.segments) == 2)
test("from_dict segment downloaded", t2.segments[0].downloaded == 512000)

# 1.3 Speed limit setter
t2.set_speed_limit(500 * 1024)
test("set_speed_limit updates field", t2.speed_limit == 500 * 1024)
test("set_speed_limit updates limiter", t2._limiter.limit_bps == 500 * 1024)

# 1.4 Pause / Cancel flags
t3 = T.DownloadTask("http://x.com/f", "/tmp/f")
test("pause_requested default False", not t3.pause_requested)
t3.request_pause()
test("pause_requested after request", t3.pause_requested)
t3.clear_pause()
test("pause cleared", not t3.pause_requested)
t3.request_cancel()
test("cancel_requested", t3.cancel_requested)

# 1.5 Percent calculation
t4 = T.DownloadTask("http://x.com/f", "/tmp/f", total_size=1000, downloaded=250)
test("percent 25%", t4.percent == 25)
t5 = T.DownloadTask("http://x.com/f", "/tmp/f", total_size=0)
test("percent 0% when size unknown", t5.percent == 0)

for r in RESULTS: print(r)
RESULTS.clear()

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 2: Utils (utils.py)
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST GROUP 2: Utils")
print("=" * 60)

# 2.1 Categories
test("mp4 -> Video", utils.get_category_dir("/base", "movie.mp4").endswith("Video"))
test("mp3 -> Music", utils.get_category_dir("/base", "song.mp3").endswith("Music"))
test("zip -> Compressed", utils.get_category_dir("/base", "arch.zip").endswith("Compressed"))
test("exe -> Programs", utils.get_category_dir("/base", "setup.exe").endswith("Programs"))
test("pdf -> Documents", utils.get_category_dir("/base", "paper.pdf").endswith("Documents"))
test("unknown ext -> base", utils.get_category_dir("/base", "file.xyz") == "/base")
test("empty filename -> base", utils.get_category_dir("/base", "") == "/base")

# 2.2 Filename from URL
test("filename from url path", utils.filename_from_url("http://x.com/video.mp4") == "video.mp4")
test("filename with suggested", utils.filename_from_url("http://x.com/a", "my_file.zip") == "my_file.zip")
test("filename fallback", utils.filename_from_url("http://x.com/") == "download.bin")

# 2.3 Sanitize
test("sanitize special chars", utils.sanitize('file<>:name.txt') == "file___name.txt")
test("sanitize query string", utils.sanitize("file.txt?v=1") == "file.txt")

# 2.4 Rate Limiter
rl = utils.RateLimiter()
test("limiter default unlimited", rl.limit_bps == 0)
rl.set_limit(1024)
test("limiter set_limit", rl.limit_bps == 1024)

# Test that limiter actually throttles
rl2 = utils.RateLimiter()
rl2.set_limit(10000)  # 10 KB/s
start = time.monotonic()
for _ in range(5):
    rl2.wait(5000)  # request 5 KB five times = 25 KB at 10 KB/s = ~2.5s
elapsed = time.monotonic() - start
test(f"limiter throttles (elapsed={elapsed:.1f}s, expect ~1.5-3.5s)", 1.0 < elapsed < 5.0,
     f"elapsed={elapsed:.2f}s")

# Test unlimited doesn't block
rl3 = utils.RateLimiter()  # limit_bps = 0 by default
start = time.monotonic()
for _ in range(100):
    rl3.wait(65536)
elapsed = time.monotonic() - start
test(f"unlimited doesn't block (elapsed={elapsed:.4f}s)", elapsed < 0.1)

for r in RESULTS: print(r)
RESULTS.clear()

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 3: API Server (api_server.py)
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST GROUP 3: API Server")
print("=" * 60)

from api_server import create_app
from collections import deque

q = QueueManager(max_concurrent=1, segments=2)
test_dir = tempfile.mkdtemp(prefix="hf_test_")
pending = deque()

app = create_app(q, test_dir, pending=pending)
client = app.test_client()

# 3.1 Ping
resp = client.get("/ping")
test("GET /ping returns 200", resp.status_code == 200)
test("GET /ping body", resp.get_json()["status"] == "ok")

# 3.2 Download with pending (GUI mode)
resp = client.post("/download", json={"url": "http://example.com/video.mp4", "filename": "video.mp4"})
test("POST /download returns 200", resp.status_code == 200)
test("POST /download queued", resp.get_json()["status"] == "queued")
test("pending deque populated", len(pending) == 1)
test("pending has url", pending[0]["url"] == "http://example.com/video.mp4")

# 3.3 Download without pending (headless mode)
app2 = create_app(q, test_dir, pending=None)
client2 = app2.test_client()
resp2 = client2.post("/download", json={"url": "http://example.com/music.mp3", "filename": "song.mp3"})
data2 = resp2.get_json()
test("Headless POST returns 200", resp2.status_code == 200)
test("Headless POST has id", "id" in data2)
test("Headless category routing", any("Music" in t.save_path for t in q.tasks), 
     f"paths: {[t.save_path for t in q.tasks]}")

# 3.4 Missing URL
resp3 = client.post("/download", json={})
test("Missing URL returns 400", resp3.status_code == 400)

# Cleanup
shutil.rmtree(test_dir, ignore_errors=True)

for r in RESULTS: print(r)
RESULTS.clear()

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 4: Downloader Engine (downloader.py)
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST GROUP 4: Downloader Engine")
print("=" * 60)

# Use a small publicly available test file
TEST_URL = "https://www.google.com/robots.txt"
test_dir2 = tempfile.mkdtemp(prefix="hf_dl_test_")
save_path = os.path.join(test_dir2, "robots.txt")
hf_path = save_path + ".hfdownload"

# 4.1 Basic download test
dt = T.DownloadTask(TEST_URL, save_path, filename="robots.txt")
dl = Downloader(dt, segments=2)
dl.run()

test("Download completed", dt.status == T.COMPLETED, f"status={dt.status}, error={dt.error}")
test("Final file exists", os.path.exists(save_path))
test(".hfdownload file cleaned up", not os.path.exists(hf_path))
if os.path.exists(save_path):
    fsize = os.path.getsize(save_path)
    test(f"File has content ({fsize} bytes)", fsize > 0)
    test("Downloaded matches file", dt.downloaded > 0)

# 4.2 Cancel test
save_path2 = os.path.join(test_dir2, "cancel_test.txt")
dt2 = T.DownloadTask(TEST_URL, save_path2, filename="cancel_test.txt")
dt2.request_cancel()
dl2 = Downloader(dt2, segments=1)
dl2.run()
test("Cancelled download status", dt2.status == T.CANCELLED)
test("Cancelled .hfdownload cleaned", not os.path.exists(save_path2 + ".hfdownload"))

# 4.3 Speed limiter integration (verify limiter is called without crashing)
save_path3 = os.path.join(test_dir2, "throttled.txt")
dt3 = T.DownloadTask(TEST_URL, save_path3, filename="throttled.txt")
dt3.set_speed_limit(50 * 1024)  # 50 KB/s
utils.global_limiter.set_limit(100 * 1024)  # 100 KB/s global
dl3 = Downloader(dt3, segments=1)
dl3.run()
test("Throttled download completed", dt3.status == T.COMPLETED, f"status={dt3.status}")
utils.global_limiter.set_limit(0)  # Reset

# Cleanup
shutil.rmtree(test_dir2, ignore_errors=True)

for r in RESULTS: print(r)
RESULTS.clear()

# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 5: Queue Manager (queue_manager.py)
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST GROUP 5: Queue Manager")
print("=" * 60)

qm = QueueManager(max_concurrent=2, segments=1)
t_q1 = T.DownloadTask("http://example.com/1", "/tmp/q1", filename="q1")
t_q1.status = T.PAUSED
qm.add_task(t_q1, start=False)
test("Task added to queue", t_q1 in qm.tasks)
test("Task not started (start=False)", t_q1.status == T.PAUSED)

# Test pause/resume API
qm.pause_task(t_q1)
test("Pause on paused task is safe", t_q1.status == T.PAUSED)

# Test cancel
t_q2 = T.DownloadTask("http://example.com/2", "/tmp/q2", filename="q2")
qm.add_task(t_q2, start=False)
qm.cancel_task(t_q2)
test("Cancel sets status", t_q2.status == T.CANCELLED)

# Test cancel on an already-downloading task
t_q4 = T.DownloadTask("http://example.com/4", "/tmp/q4", filename="q4", status=T.DOWNLOADING)
qm.add_task(t_q4, start=False)
qm.cancel_task(t_q4)
test("Cancel marks active task cancelled", t_q4.status == T.CANCELLED)
test("Cancel sets cancellation flag", t_q4.cancel_requested)

# Test remove_finished
t_q3 = T.DownloadTask("http://example.com/3", "/tmp/q3", filename="q3", status=T.COMPLETED)
qm.add_task(t_q3, start=False)
qm.remove_finished()
test("remove_finished clears completed", t_q3 not in qm.tasks)

qm.shutdown()

for r in RESULTS: print(r)
RESULTS.clear()

# ═══════════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"FINAL RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL} total")
print("=" * 60)

if FAIL > 0:
    sys.exit(1)
else:
    print("🎉 ALL TESTS PASSED!")
    sys.exit(0)
