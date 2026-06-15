# 🎬 REAL WEBSITE VIDEO GRABBER TESTING GUIDE

## Quick Start: Load Extension & Test in 5 Minutes

### Step 1: Ensure API Server is Running
```powershell
cd IDMClone
python.exe api_server.py
```
✅ Should show: `Running on http://127.0.0.1:5000`

### Step 2: Load Chrome Extension
1. Open **Chrome** → Go to `chrome://extensions`
2. Enable **Developer Mode** (top right toggle)
3. Click **"Load unpacked"**
4. Browse to: `C:\Users\Deb_Laptop\OneDrive\Documents\agent\IDMClone\chrome_ext`
5. Click **Select Folder**
6. ✅ Extension should appear in list with ID and status

### Step 3: Verify Extension Loaded
- Purple icon should appear in Chrome toolbar
- Click it → Should show "IDM Clone Download Manager" popup
- Should see "No active downloads" message

---

## 🌐 REAL WEBSITE TESTING

### TEST #1: Simple Video Page (Recommended Starting Point)

**Website:** https://www.html5rocks.com/en/tutorials/video/basics/

**Expected Result:**
- Purple "HyperFetch" button appears on video player
- Button positioned in top-right corner
- Video plays normally

**How to Test:**
1. Navigate to URL
2. Look for `<video>` element on page
3. Hover over video → Purple button should appear
4. Click button → "Download initiated" toast appears
5. Check desktop app → Task should appear in queue

**Expected Download Details:**
```
Video URL: [detected from page]
Filename: auto-generated
Status: QUEUED
```

---

### TEST #2: HLS Stream (Advanced - Requires HLS Detection)

**Websites with HLS Streams:**
- https://test-streams.mux.dev/x36xhzz/x3zzjt.m3u8 (Direct m3u8)
- https://devstreaming-cdn.apple.com/videos/streaming/examples/bipbop_16x9_variant.m3u8 (Apple test)
- https://commondatastorage.googleapis.com/gtv-videos-library/sample/sintel.m3u8 (Google test)

**Expected Result:**
- HLS stream detected automatically
- m3u8 URL captured in API
- Extension sends to app with stream format

**How to Test:**
1. Open browser console (F12)
2. Navigate to HLS stream URL
3. Console should show: `window.hls.url = "...m3u8"`
4. Open popup → Should detect media
5. Click "Download HLS Stream"
6. Check app → Should show HLS download initiated

**Verification:**
```
- Filename: ends with .m3u8 or .mp4
- Category: Video
- Format: HLS Stream detected
```

---

### TEST #3: YouTube (Advanced - May Require Additional Authentication)

**Website:** https://www.youtube.com

**Note:** YouTube has strong download protection. Extension may detect video element but downloading may fail due to:
- DRM protection (Widevine)
- CORS restrictions
- Authentication requirements

**What Should Happen:**
1. Video plays normally
2. Overlay button may or may not appear (depends on video source)
3. If button appears: Shows appropriate error when clicked

**Expected Error (Normal):**
```
Error: Cannot access YouTube video source
Reason: DRM protected or CORS restricted
Suggestion: Use youtube-dl or similar tool
```

---

### TEST #4: Streaming Site with Multiple Videos

**Website:** https://sample-videos.com

**Expected Result:**
- Multiple video players detected
- Each gets purple HyperFetch button
- All buttons functional
- Each can be downloaded independently

**How to Test:**
1. Navigate to site with 3+ videos
2. Scroll through all videos
3. Verify each has purple button
4. Click each button
5. App should show 3+ tasks in queue

**Verification:**
```
Video 1: [URL] - Button appeared ✓
Video 2: [URL] - Button appeared ✓
Video 3: [URL] - Button appeared ✓
Tasks in Queue: 3
```

---

### TEST #5: Video with Authentication/Cookies

**Website:** Your own streaming service with login

**Expected Result:**
- Video loads with authentication
- Extension detects video
- Download includes auth cookies
- Server-side validation passes

**How to Test:**
1. Log into streaming service
2. Navigate to protected video
3. Verify purple button appears
4. Click to download
5. Monitor API server logs: Should see Cookie header

**Verification in API:**
```python
# Check api_server.py logs
# Should show: Cookie: session=xxx, auth_token=yyy
```

---

### TEST #6: Error Cases - Network Issues

**How to Simulate:**
1. Temporarily disconnect WiFi
2. Navigate to video page with extension
3. Try to download
4. Reconnect internet

**Expected Result:**
- Extension handles gracefully
- Shows error message (not crash)
- App logs error
- Can retry when reconnected

**Error Messages Expected:**
```
- "Network error: Connection timeout"
- "Failed to reach server"
- "API unavailable (127.0.0.1:5000)"
```

---

## 🧪 COMPREHENSIVE TEST MATRIX

### Video Player Types

| Player Type | Website Example | Detection | Button | Download |
|---|---|---|---|---|
| HTML5 `<video>` | html5rocks.com | ✅ | ✅ | ✅ |
| HLS Stream | mux.dev | ✅ | ⚠️ | ✅ |
| DASH Stream | dash-ref.media.mit.edu | ✅ | ⚠️ | ✅ |
| YouTube Player | youtube.com | ✅ | ✅ | ❌ (DRM) |
| Vimeo Player | vimeo.com | ⚠️ | ⚠️ | ⚠️ |
| Custom Player | Other sites | ⚠️ | ⚠️ | ⚠️ |

**Legend:**
- ✅ = Working/Expected
- ⚠️ = May work depending on site
- ❌ = Won't work (expected limitation)

---

## 📊 POPUP FUNCTIONALITY TEST

### Popup Window Features

**What to Test:**
1. Open popup (click extension icon)
2. Should show: "IDM Clone Download Manager"
3. Should show: Number of active downloads
4. Should show: "No active downloads" when idle

**Click Behavior:**
- Clicking on active download → Shows progress
- Can pause/cancel from popup ✅
- Popup updates in real-time

**Expected Popup Display:**
```
╔════════════════════════════╗
║  IDM Clone Download Mgr    ║
╠════════════════════════════╣
║  [Refresh]  [Settings]     ║
║                            ║
║  Active Downloads: 1       ║
║  ┌──────────────────────┐  ║
║  │ video.mp4            │  ║
║  │ ████████░░░░░░░░ 50% │  ║
║  │ 2.5 MB / 5.0 MB      │  ║
║  │ [Pause] [Cancel]     │  ║
║  └──────────────────────┘  ║
╚════════════════════════════╝
```

---

## 🔍 CONSOLE LOG VERIFICATION

### Enable Console Logging
1. Press **F12** (Developer Tools)
2. Go to **Console** tab
3. Navigate to video page

### Expected Console Messages

**On Page Load:**
```javascript
[Extension] Scanning DOM for video elements...
[Extension] Found 1 video element
[Extension] Video source: https://example.com/video.mp4
[Extension] Created download button
```

**On Download Click:**
```javascript
[Extension] Download button clicked
[Extension] Sending to API: {"url": "...", "suggestedName": "video.mp4"}
[Extension] Response: {"status": "ok", "task_id": "task_123"}
[Extension] Download initiated successfully
```

**On Error:**
```javascript
[Extension] ERROR: Cannot reach API at 127.0.0.1:5000
[Extension] Is the API server running?
[Extension] CORS error: Check extension permissions
```

---

## 🛠️ TROUBLESHOOTING

### Issue: Purple Button Not Appearing

**Possible Causes:**

1. **Extension Not Loaded**
   - Check `chrome://extensions` → Status should be "Enabled"
   - Try reloading: Click refresh icon next to extension

2. **Video Player Issue**
   - Open Console (F12)
   - Check for errors
   - Verify `<video>` tag exists: `document.querySelector('video')`

3. **DOM Scanning Not Running**
   - Console should show: "Scanning DOM..." every 500ms
   - If not, reload extension: `chrome://extensions` → Reload button

4. **Position Blocking**
   - Button might be behind other elements
   - Try scrolling or resizing video
   - Check browser zoom: Should be 100%

**Fix:**
```javascript
// In Console, manually trigger scan:
scanDom();
```

---

### Issue: "Cannot reach API" Error

**Possible Causes:**

1. **API Server Not Running**
   ```powershell
   # Terminal: Check if server is running
   Get-Process python | Where-Object {$_.CommandLine -like "*api_server*"}
   
   # If not, start it:
   python.exe api_server.py
   ```

2. **Wrong Port**
   - Extension expects: `127.0.0.1:5000`
   - Check api_server.py output: Should show `Running on ...`

3. **CORS Issue**
   - Check API has CORS headers: 
   - Browser should allow `chrome-extension://` origin

**Fix:**
```python
# In api_server.py, verify CORS:
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response
```

---

### Issue: Download Queued But Not Starting

**Possible Causes:**

1. **API Server Not Receiving Message**
   - Check API logs for POST request
   - Should see: `POST /download 200 OK`

2. **Download Manager Not Running**
   - Start main.py if not running:
   ```powershell
   python.exe main.py
   ```

3. **Queue Manager Issue**
   - Check app logs for errors
   - Verify max_concurrent setting

**Fix:**
```powershell
# Restart everything:
# 1. Stop api_server.py (Ctrl+C in terminal)
# 2. Stop main.py if running
# 3. Reload extension: chrome://extensions → Reload
# 4. Restart api_server.py
# 5. Restart main.py
```

---

### Issue: Overlay Button Blocks Video Controls

**Solution:**
- Button positioned absolutely at `top: 8px; right: 8px`
- Should not interfere with video controls
- If blocking: Check CSS in content.js

**CSS Position (content.js):**
```javascript
button.style.cssText = `
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 10000;
  background: purple;
  ...
`;
```

---

## ✅ TESTING CHECKLIST

### Pre-Testing
- [ ] API server running (`python.exe api_server.py`)
- [ ] Extension loaded (`chrome://extensions`)
- [ ] Extension enabled (toggle is ON)
- [ ] Console open (F12)
- [ ] Internet connection active

### Basic Testing (5 min)
- [ ] Visit html5rocks.com video page
- [ ] Purple button appears on video
- [ ] Button in correct position (top-right)
- [ ] Clicking button triggers download
- [ ] Check app → Task appears in queue
- [ ] No JavaScript errors in console

### Advanced Testing (15 min)
- [ ] Test HLS stream (mux.dev)
- [ ] Test multiple videos on one page
- [ ] Test with authentication
- [ ] Test pause/cancel from popup
- [ ] Monitor console for all messages
- [ ] Check API logs for requests

### Error Testing (10 min)
- [ ] Disconnect internet → Try download → Error shown
- [ ] Stop API server → Try download → Error shown
- [ ] Invalid video URL → Appropriate error
- [ ] Video with no source → Handled gracefully

---

## 📋 TEST RESULTS LOG

**Date:** [Fill in]  
**Tester:** [Fill in]  
**Browser Version:** [Check: chrome://version]  
**Extension Version:** [Check: chrome://extensions]  

### Test Results

| Test | Expected | Actual | Result | Notes |
|---|---|---|---|---|
| Basic video detection | Button appears | __ | ✅/❌ | __ |
| HLS stream | Stream detected | __ | ✅/❌ | __ |
| Multiple videos | All detected | __ | ✅/❌ | __ |
| Authentication | Cookies passed | __ | ✅/❌ | __ |
| Error handling | Graceful error | __ | ✅/❌ | __ |
| Performance | <500ms | __ | ✅/❌ | __ |

---

## 🚀 NEXT STEPS AFTER TESTING

### If All Tests Pass ✅
1. Clear test data
2. Document passing tests
3. Ready for production release
4. Submit to Chrome Web Store (optional)

### If Tests Fail ⚠️
1. Note exact error message
2. Check console logs
3. Review code for issue
4. Fix and re-test
5. Document fix

### Edge Cases to Document
- Specific sites that don't work
- Specific video formats unsupported
- Known limitations
- Workarounds if any

---

## 📞 SUPPORT REFERENCE

### Extension Files
- `chrome_ext/manifest.json` - Extension configuration
- `chrome_ext/content.js` - Video detection logic
- `chrome_ext/background.js` - Message relay
- `chrome_ext/popup.js` - Popup interface

### API Reference
- **Endpoint:** POST `http://127.0.0.1:5000/download`
- **Body:** `{"url": "...", "suggestedName": "..."}`
- **Response:** `{"status": "ok", "task_id": "..."}`

### Debug Mode
```javascript
// Add to content.js for detailed logging:
const DEBUG = true;
if (DEBUG) console.log('[DEBUG]', message);
```

---

**Status: Ready for Real-World Testing**
**Test Independently on Your Chrome Browser**
**Report Results and Issues**
