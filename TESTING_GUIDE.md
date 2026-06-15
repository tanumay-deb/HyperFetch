# TESTING GUIDE FOR IDM CLONE

This document explains how to run the comprehensive test suite for the HyperFetch.

## Quick Start

### Run All Original Tests (59 tests)
```powershell
cd c:\Users\Deb_Laptop\OneDrive\Documents\agent\IDMClone
python.exe test_e2e.py
```

**Expected Output:**
```
FINAL RESULTS: 59 passed, 0 failed out of 59 total
🎉 ALL TESTS PASSED!
```

### Run Comprehensive End-to-End Tests (10 parts)
```powershell
cd c:\Users\Deb_Laptop\OneDrive\Documents\agent\IDMClone
python.exe test_comprehensive.py
```

**Note:** This downloads real files (1MB, 10MB) so it will take several minutes to complete.

---

## TEST BREAKDOWN

### TEST SUITE 1: test_e2e.py (Original - Fast)
**Duration:** 30-60 seconds  
**Real Downloads:** 2 small files  
**Dependencies:** Network access

#### What it tests:
1. **TEST GROUP 1: Task Model** (18 tests)
   - Task creation
   - State transitions
   - Serialization/deserialization
   - Speed limiting
   - Pause/cancel semantics

2. **TEST GROUP 2: Utils** (16 tests)
   - File categorization (Video, Music, Documents, Programs, Compressed)
   - Filename extraction from URLs
   - Filename sanitization
   - Rate limiting and throttling
   - Speed calculations

3. **TEST GROUP 3: API Server** (10 tests)
   - /ping endpoint health check
   - /download endpoint
   - Request validation
   - Category routing
   - Error handling (400 Bad Request)

4. **TEST GROUP 4: Downloader Engine** (8 tests)
   - Actual HTTP file downloads
   - Multi-segment downloading
   - Speed throttling enforcement
   - Cancellation handling
   - Temporary .sdm file cleanup

5. **TEST GROUP 5: Queue Manager** (7 tests)
   - Task enqueueing
   - Pause/cancel on different states
   - Task removal after completion
   - Concurrent download handling

---

### TEST SUITE 2: test_comprehensive.py (Extended - Thorough)
**Duration:** 3-5 minutes  
**Real Downloads:** 1MB + 10MB files  
**Dependencies:** Network access, disk space

#### What it tests:

**PART 1: API Server Initialization** (4 tests)
- Flask app creation
- Queue manager setup
- Concurrency limits
- Health check endpoint

**PART 2: Task Model & State Management** (12 tests)
- Full state machine verification
- Task ID uniqueness
- Pause/cancel flag propagation
- Serialization round-trip
- Speed limit persistence

**PART 3: API Endpoints & Load Testing** (10 tests)
- Multiple concurrent requests
- Auto filename extraction
- Invalid input rejection
- File categorization
- Category routing (Video/Music/Documents/Programs)

**PART 4: Queue Manager - Concurrency** (6 tests)
- Task queueing with multiple items
- Pause request handling
- Cancellation on QUEUED state
- Cancellation on DOWNLOADING state
- Priority handling

**PART 5: Downloader Engine** (5 tests)
- Real 1MB file download
- Speed throttling (100 KB/s)
- Cancellation mid-download
- Multi-segment (4 segments)
- .sdm cleanup

**PART 6: HLS Stream Detection** (7 tests)
- Direct m3u8 URL detection
- m3u8 with query parameters
- DASH MPD file detection
- MIME type recognition
- Filename-based detection

**PART 7: Utility Functions** (11 tests)
- Category detection (5 types)
- URL to filename extraction
- Special character sanitization
- Rate limiter with limit
- Rate limiter without limit

**PART 8: Error Handling** (5 tests)
- Invalid URL handling
- Absolute path support
- Relative path support
- Concurrent stress test (10 tasks)
- Empty filename fallback

**PART 9: Extension Protocol** (5 tests)
- DOWNLOAD_URL message format
- SNIFFED_MEDIA message format
- API endpoint response
- Cookie passing simulation
- 5 simultaneous connections

**PART 10: Full Integration** (5 tests)
- End-to-end workflow validation
- Task ID generation
- API responsiveness during downloads
- All category routes working

---

## RUNNING INDIVIDUAL TEST GROUPS

### For test_e2e.py
The file is organized into 5 sections. To run all:
```powershell
python.exe test_e2e.py
```

Each group is self-contained, so you can modify the file to comment out groups for faster testing.

### For test_comprehensive.py
Each PART is clearly marked. To run a specific part:
1. Open test_comprehensive.py in VS Code
2. Navigate to the part you want to test
3. Comment out other parts temporarily
4. Run: `python.exe test_comprehensive.py`

---

## UNDERSTANDING TEST OUTPUT

### Success Example
```
  [PASS] [1] Ping endpoint responds
  [PASS] [2] Ping response format
  [PASS] [3] QueueManager created
```
Shows: Test number, pass/fail status, test name

### Failure Example
```
  [FAIL] [15] Download completed -- DownloadError: Connection failed
```
Shows: Test number, fail status, test name, error detail

### Summary Line
```
[OK] Part 1 Complete: 4/4 tests passed
```
Shows: Part name and pass count

---

## TROUBLESHOOTING

### "ModuleNotFoundError: No module named 'flask'"
**Solution:** Install dependencies
```powershell
pip install flask flask-cors requests urllib3
```

### "Connection timeout" during downloads
**Cause:** Network or internet issue  
**Solution:** Check internet connection, try again later

### "PermissionError: File exists"
**Cause:** Test temp directory already exists  
**Solution:** Delete `C:\Users\Deb_Laptop\AppData\Local\Temp\sdm_test_*` directories

### Tests hang or timeout
**Cause:** Download taking too long  
**Solution:** Ctrl+C to stop, check internet speed, run faster test suite

---

## CONTINUOUS INTEGRATION SETUP

### GitHub Actions Example
```yaml
name: E2E Tests
on: [push]

jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      - run: pip install flask flask-cors requests urllib3
      - run: cd IDMClone && python test_e2e.py
```

### Pre-commit Hook
Add to `.git/hooks/pre-commit`:
```bash
#!/bin/bash
cd IDMClone
python test_e2e.py || exit 1
```

---

## TEST DATA SOURCES

Tests use real URLs:
- `http://proof.ovh.net/files/1Mb.dat` - 1MB test file
- `http://proof.ovh.net/files/10Mb.dat` - 10MB test file (comprehensive suite)

These are public test files hosted for bandwidth testing.

---

## PERFORMANCE EXPECTATIONS

| Test | Expected Time | Notes |
|------|---|---|
| API health check | <1s | Local only |
| Task creation | <0.1s | Per task |
| 1MB download | 0.5-1.0s | Depends on network |
| 10MB download | 3-5s | Depends on network |
| Full test_e2e.py | 30-60s | Includes real downloads |
| Full test_comprehensive.py | 3-5min | Many real downloads |

---

## NEXT STEPS

After tests pass:
1. ✅ Review TEST_REPORT.md for detailed results
2. ✅ Run `python main.py` to start the desktop app
3. ✅ Load chrome_ext/ and edge_ext/ in browsers (chrome://extensions)
4. ✅ Test real downloads on actual websites
5. ✅ Monitor logs for any warnings

---

## FILES REFERENCE

| File | Purpose | Run Command |
|------|---------|------------|
| test_e2e.py | Original 59 tests | `python test_e2e.py` |
| test_comprehensive.py | Extended 10-part tests | `python test_comprehensive.py` |
| TEST_REPORT.md | Detailed test results | Open in editor |
| main.py | Desktop app entry point | `python main.py` |
| api_server.py | Flask API server | Part of main.py |

---

## CONTACT & SUPPORT

For test failures:
1. Check error message carefully
2. Review TEST_REPORT.md for expected behavior
3. Check network connectivity
4. Verify Python 3.10+ and dependencies installed
5. Try running test_e2e.py (simpler) first

---

*Last Updated: June 13, 2026*  
*Python: 3.10+*  
*Status: All tests passing*
