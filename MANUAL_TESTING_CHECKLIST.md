# MANUAL TESTING CHECKLIST

Use this checklist to verify the Smart Download Manager works correctly on real websites.

## Pre-Testing Setup

- [ ] Python 3.10+ installed
- [ ] All dependencies installed: `pip install flask flask-cors requests urllib3 pyside6`
- [ ] Desktop app started: `python main.py`
- [ ] Chrome/Edge browser with extension loaded
- [ ] Network connection active
- [ ] Download folder is accessible
- [ ] At least 2GB free disk space

---

## BASIC FUNCTIONALITY TESTS

### 1. Direct File Download
**Test URL:** Any website with direct download link (e.g., PDF, ZIP)

- [ ] Click file link in browser
- [ ] "Sent to Download Manager" toast appears
- [ ] File appears in desktop app
- [ ] Download completes successfully
- [ ] File opens/extracts correctly
- [ ] No .sdm temp files remain

### 2. Speed Limiting
**Test URL:** http://proof.ovh.net/files/10Mb.dat

- [ ] Right-click → "Copy link"
- [ ] Paste in app URL field
- [ ] Set speed limit to 100 KB/s
- [ ] Start download
- [ ] Monitor progress - should take ~100 seconds
- [ ] Speed shown accurately
- [ ] Can pause and resume
- [ ] Can cancel mid-download

### 3. Pause & Resume
**Test URL:** Any large file (>10MB)

- [ ] Start download
- [ ] Click Pause button while downloading
- [ ] Progress stops updating
- [ ] Click Resume button
- [ ] Download continues from where it left off
- [ ] Final file is complete and correct

### 4. Cancellation
**Test URL:** Any file

- [ ] Start download
- [ ] Click Cancel button
- [ ] File transfer stops immediately
- [ ] Task shows "CANCELLED" status
- [ ] Temp .sdm file is deleted
- [ ] Can start new download afterward

---

## BROWSER EXTENSION TESTS

### 5. Chrome Extension
**Setup:** `chrome://extensions` → Load unpacked → Select `chrome_ext/`

- [ ] Extension icon appears in toolbar
- [ ] Extension popup shows "connected" status (green dot)
- [ ] Test button works (small 10MB download)
- [ ] Real website links captured
- [ ] Toast "Sent to Download Manager" appears
- [ ] Files appear in app

### 6. Edge Extension
**Setup:** `edge://extensions` → Toggle Developer mode → Load unpacked → Select `edge_ext/`

- [ ] Extension appears in toolbar
- [ ] Status shows "connected"
- [ ] Works identically to Chrome version
- [ ] Download captures work
- [ ] Integration with app verified

### 7. Extension Popup
**Action:** Click extension icon in toolbar

- [ ] Popup shows app connection status
- [ ] Toggle "Capture downloads" ON/OFF
- [ ] Test download button works
- [ ] Status updates in real-time
- [ ] Connection status correct (green/red dot)

---

## VIDEO STREAMING TESTS

### 8. HLS Stream Detection
**Test URL:** https://missav.ws/en/gana-3191 (or similar HLS site)

- [ ] Page loads with video player
- [ ] "SDM" button appears in top-right corner of video
- [ ] Button has gradient background (purple)
- [ ] Click button → Download starts
- [ ] Media panel shows detected HLS stream
- [ ] Download completes with .m3u8 or .mp4

### 9. Runtime HLS Detection
**Test URL:** Any site using hls.js player

- [ ] Video loads dynamically via JavaScript
- [ ] window.hls object contains playlist URL
- [ ] Extension detects the runtime URL
- [ ] Download button appears and works
- [ ] Multiple quality levels detected if available

### 10. Media Sniffer Panel
**Test URL:** Any streaming site

- [ ] Purple panel appears top-right with "Download Media (N)"
- [ ] Shows detected HLS/DASH streams
- [ ] Shows file size and MIME type
- [ ] "Download with SDM" button on each item
- [ ] Panel can be hidden/shown by clicking toggle

---

## CATEGORIZATION & ORGANIZATION TESTS

### 11. File Categories
**Test Actions:** Download files with different extensions

- [ ] MP4 file → Saved to Videos folder
- [ ] MP3 file → Saved to Music folder
- [ ] PDF file → Saved to Documents folder
- [ ] EXE file → Saved to Programs folder
- [ ] ZIP file → Saved to Compressed folder
- [ ] Unknown extension → Saved to main Downloads

### 12. Download History
**Action:** Check app after several downloads

- [ ] All files appear in list
- [ ] Sizes shown correctly
- [ ] Status shows COMPLETED
- [ ] Timestamps are accurate
- [ ] Can clear history
- [ ] Can open file directly from app

---

## ERROR HANDLING TESTS

### 13. Invalid URL
**Action:** Paste invalid URL in app

- [ ] Error appears: "Invalid URL" or similar
- [ ] Download doesn't start
- [ ] App remains responsive
- [ ] Can try another URL

### 14. Network Timeout
**Action:** Disconnect network mid-download

- [ ] Download pauses
- [ ] Error message appears: "Connection lost"
- [ ] Can retry when network restored
- [ ] Progress saved

### 15. Disk Full
**Setup:** Create a test scenario with limited disk space

- [ ] App shows error: "Insufficient disk space"
- [ ] Download stops gracefully
- [ ] Temp file cleaned up
- [ ] No corruption

### 16. Protected/Authenticated Content
**Test URL:** Google Drive, OneDrive, or protected content

- [ ] App passes cookies correctly
- [ ] Authentication cookies sent
- [ ] Protected files downloadable
- [ ] Session maintained through download

---

## PERFORMANCE TESTS

### 17. Multiple Concurrent Downloads
**Action:** Start 5 downloads simultaneously

- [ ] All 5 downloads start
- [ ] App remains responsive
- [ ] UI updates smoothly
- [ ] Maximum concurrent limit enforced (default 3)
- [ ] Queue visible in app
- [ ] Each completes correctly

### 18. Large File Download (100MB+)
**Action:** Download large file

- [ ] Progress updates every second
- [ ] Speed calculation accurate
- [ ] ETA shown and updates
- [ ] Can pause/resume without corruption
- [ ] Final file integrity verified
- [ ] Completes in reasonable time

### 19. Multi-Segment Download
**Action:** Download 10MB file with 4 segments

- [ ] File divided into 4 parts
- [ ] Segments download in parallel
- [ ] Final file is complete (10MB)
- [ ] Speed limit applies to total, not per-segment
- [ ] Pause/resume works correctly

---

## COOKIE & AUTHENTICATION TESTS

### 20. Authenticated Site
**Test URL:** Any login-required site (e.g., patreon, dropbox)

- [ ] Log in to site in browser
- [ ] Extension captures download cookies
- [ ] Background script sends cookies in POST
- [ ] Authenticated file downloads successfully
- [ ] No "403 Forbidden" errors

### 21. Session Persistence
**Action:** Download multiple files from same authenticated site

- [ ] Session maintained across downloads
- [ ] No re-authentication needed
- [ ] All files download successfully
- [ ] Cookies refreshed if needed

---

## UI/UX TESTS

### 22. Desktop App UI
**Action:** Open desktop app window

- [ ] Window shows SDM logo/title
- [ ] Download list visible
- [ ] Speed/progress indicators work
- [ ] Buttons responsive (Pause, Cancel, Open)
- [ ] Smooth scrolling through downloads
- [ ] Resize window works properly

### 23. Toast Notifications
**Action:** Perform various actions

- [ ] "Sent to Download Manager" appears (3s timeout)
- [ ] Position: bottom-right corner
- [ ] Style: Purple gradient background
- [ ] Text readable
- [ ] Auto-disappears after timeout

### 24. Status Indicators
**Action:** Monitor various states

- [ ] QUEUED = Blue/waiting
- [ ] DOWNLOADING = Green/active
- [ ] COMPLETED = Checkmark
- [ ] CANCELLED = Red/crossed
- [ ] PAUSED = Orange/paused
- [ ] ERROR = Red/error icon

---

## API ENDPOINT TESTS

### 25. /ping Endpoint
**Action:** Send GET request to http://127.0.0.1:5000/ping

- [ ] Response: HTTP 200
- [ ] Body: `{"status": "ok"}`
- [ ] Used by popup for connection check

### 26. /download Endpoint (Valid)
**Action:** Send POST with valid JSON

```json
{
  "url": "http://example.com/file.zip",
  "filename": "archive.zip"
}
```

- [ ] Response: HTTP 200
- [ ] Contains task ID
- [ ] Task appears in queue

### 27. /download Endpoint (Invalid)
**Action:** Send POST missing URL

- [ ] Response: HTTP 400
- [ ] Error message clear
- [ ] App doesn't crash

---

## CLEANUP & FINAL CHECKS

### 28. Temp File Cleanup
**Action:** Monitor temp directory during downloads

- [ ] .sdm files created in Downloads folder
- [ ] After completion, .sdm deleted automatically
- [ ] After cancellation, .sdm deleted automatically
- [ ] No orphaned temp files

### 29. Log Cleanliness
**Action:** Run app and check console output

- [ ] No Python exceptions
- [ ] No stack traces
- [ ] Only expected messages
- [ ] Performance metrics available

### 30. System Integration
**Action:** Test system-level integration

- [ ] Extension works with Chrome Dev Tools open
- [ ] Extension works with multiple tabs
- [ ] App works alongside other download tools
- [ ] No interference with system downloads
- [ ] No port conflicts (127.0.0.1:5000)

---

## KNOWN ISSUES / WORKAROUNDS

| Issue | Status | Workaround |
|-------|--------|-----------|
| Overlay button not appearing initially | Known | Reload page |
| HLS with dynamic URLs | Supported | Script detection enabled |
| Protected/signed URLs expiring | Known | Downloads before expiry |
| Very large files (>5GB) | Not tested | May require manual chunks |

---

## SIGN-OFF CHECKLIST

- [ ] All 30 manual tests completed
- [ ] No critical bugs found
- [ ] Performance acceptable
- [ ] UI/UX satisfactory
- [ ] Documentation accurate
- [ ] Ready for users

---

## TEST ENVIRONMENT

**Tester:** _________________  
**Date:** _________________  
**System:** Windows 10/11  
**Browser:** Chrome / Edge  
**Python:** 3.10+  
**Network:** Broadband (>10Mbps)  

**Overall Result:**  
- [ ] PASS - All tests successful
- [ ] PASS with notes - See comments below
- [ ] FAIL - Critical issues found

**Comments:**
```
_________________________________________________________________
_________________________________________________________________
_________________________________________________________________
```

---

*Last Updated: June 13, 2026*  
*Estimated Testing Time: 1-2 hours for full checklist*  
*Designed for: QA testers and developers*
