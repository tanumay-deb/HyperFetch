"""Settings page builders + their helpers, split out of settings.py.

`PageBuilderMixin` is mixed into `SettingsDialogV2`; every method runs on the
live dialog instance, so the `self.<widget>` references that `values()` reads
keep working unchanged. This file holds the UI construction; settings.py keeps
the dialog behaviour (nav, search, values).
"""
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QSlider, QSpinBox, QWidget, QFrame, QFileDialog, QTimeEdit,
    QScrollArea, QApplication, QSizePolicy
)
from PySide6.QtCore import Qt, QTime

import crash_reporter
import utils
from gui.theme import APP_VERSION
try:
    from gui.dialogs import AnimatedToggle
except Exception:                       # fallback if unavailable
    from PySide6.QtWidgets import QCheckBox as AnimatedToggle
from gui2.palette import COLORS, ACCENTS, set_accent
from gui.icons import themed_icon

_SLIDER_QSS = (
    "QSlider::groove:horizontal{height:5px;background:%(b)s;border-radius:3px;}"
    "QSlider::sub-page:horizontal{background:%(a)s;border-radius:3px;}"
    "QSlider::handle:horizontal{background:white;width:16px;margin:-6px 0;border-radius:8px;}"
)


class PageBuilderMixin:
    def _page(self, title, subtitle):
        # title/subtitle intentionally omitted — the section name already shows
        # in the sidebar, so the page goes straight to its cards.
        sa = QScrollArea(); sa.setWidgetResizable(True)
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(28, 22, 32, 22); v.setSpacing(10)
        sa.setWidget(w); sa._v = v
        return sa, v

    def _card(self):
        f = QFrame(); f.setObjectName("panel")
        g = QVBoxLayout(f); g.setContentsMargins(16, 12, 16, 12); g.setSpacing(14)
        return f, g

    def _row(self, layout, label, desc, widget):
        # wrap each setting in a widget so search can hide individual rows, and
        # tag it with its searchable text (label + description).
        rw = QWidget(); rw.setObjectName("settingRow")
        rw.setProperty("searchText", f"{label} {desc or ''}".lower())
        r = QHBoxLayout(rw); r.setContentsMargins(0, 0, 0, 0)
        col = QVBoxLayout(); col.setSpacing(1)
        l = QLabel(label); l.setStyleSheet(f"font-weight:700;background:transparent;color:{COLORS['text']};")
        col.addWidget(l)
        if desc:
            d = QLabel(desc); d.setStyleSheet(f"color:{COLORS['muted']};font-size:11px;background:transparent;")
            col.addWidget(d)
        r.addLayout(col); r.addStretch(); r.addWidget(widget)
        layout.addWidget(rw)

    def _toggle(self, on):
        t = AnimatedToggle()
        t.setChecked(bool(on))
        return t

    def _combo(self, items, current=None):
        c = QComboBox(); c.addItems(items)
        if current and current in items:
            c.setCurrentText(current)
        return c

    def _slider(self, lo, hi, val):
        s = QSlider(Qt.Horizontal); s.setRange(lo, hi); s.setValue(int(val)); s.setFixedWidth(220)
        s.setStyleSheet(_SLIDER_QSS % {"a": COLORS["accent"], "b": COLORS["border2"]})
        lbl = QLabel(str(int(val))); lbl.setFixedWidth(28); lbl.setStyleSheet("background:transparent;font-weight:700;")
        s.valueChanged.connect(lambda v: lbl.setText(str(v)))
        box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0); h.addWidget(s); h.addWidget(lbl)
        box._slider = s
        return box

    # ---- pages ----
    def _p_general(self, save_dir, ex):
        sa, v = self._page("General", "Manage basic application settings")
        f, g = self._card()
        self.dir_lbl = QLabel(save_dir); self.dir_lbl.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
        browse = QPushButton("Change…"); browse.clicked.connect(self._browse)
        self._row(g, "Default Download Folder", save_dir, browse)
        v.addWidget(f)
        f2, g2 = self._card()
        self.launch = self._combo(["Show main window", "Start minimized", "Start in tray"], ex.get("launch"))
        self._row(g2, "On application launch", "What happens when HyperFetch starts", self.launch)
        self.min_tray = self._toggle(ex.get("minimize_tray", True))
        self._row(g2, "Minimize to system tray", "Keep HyperFetch running in the background", self.min_tray)
        self.close_beh = self._combo(["Ask every time", "Minimize to tray", "Exit"], ex.get("close_behavior"))
        self._row(g2, "Close button behavior", "When clicking the close button", self.close_beh)
        self.clip_mon = self._toggle(ex.get("clipboard_monitor", False))
        self._row(g2, "Monitor clipboard", "Notify when a download link is copied", self.clip_mon)
        v.addWidget(f2); v.addStretch()
        return sa

    def _p_downloads(self, conc, segs, ex):
        sa, v = self._page("Downloads", "Configure download behavior and defaults")
        f, g = self._card()
        self.conc = self._slider(1, 16, conc)
        self._row(g, "Concurrent Downloads", "Maximum downloads at the same time", self.conc)
        self.conns = self._slider(1, 32, segs)
        self._row(g, "Segments per Download", "Simultaneous HTTP range segments per file", self.conns)
        self.def_queue = self._combo(["Main"], ex.get("default_queue"))
        self._row(g, "Default Queue", "Where new downloads land", self.def_queue)
        self.auto_start = self._toggle(ex.get("auto_start", True))
        self._row(g, "Auto start downloads", "Start downloads immediately after adding", self.auto_start)
        self.categorize = self._toggle(ex.get("categorize", True))
        self._row(g, "Organize into category folders",
                  "Auto-sort into Video / Music / Images / … subfolders by file type", self.categorize)
        self.speed_limit = self._combo(["Unlimited", "1 Mb/s", "5 Mb/s", "10 Mb/s"], ex.get("speed_limit"))
        self._row(g, "Download Speed Limit", "Global download speed limit", self.speed_limit)
        self.throttle_en = self._toggle(ex.get("throttle_enabled", False))
        self._row(g, "Scheduled speed limit", "Throttle to a slower speed during a daily time window", self.throttle_en)
        trow = QHBoxLayout()
        self.thr_start = QTimeEdit(QTime.fromString(ex.get("throttle_start", "09:00"), "HH:mm")); self.thr_start.setDisplayFormat("HH:mm")
        self.thr_stop = QTimeEdit(QTime.fromString(ex.get("throttle_stop", "17:00"), "HH:mm")); self.thr_stop.setDisplayFormat("HH:mm")
        self.thr_limit = self._combo(["1 Mb/s", "2 Mb/s", "5 Mb/s", "10 Mb/s"], ex.get("throttle_limit"))
        trow.addWidget(QLabel("From")); trow.addWidget(self.thr_start)
        trow.addSpacing(8); trow.addWidget(QLabel("to")); trow.addWidget(self.thr_stop)
        trow.addSpacing(12); trow.addWidget(QLabel("limit")); trow.addWidget(self.thr_limit); trow.addStretch()
        g.addLayout(trow)
        self.when_complete = self._combo(["Show notification", "Open file", "Open folder", "Do nothing"],
                                         ex.get("when_complete"))
        self._row(g, "When download is complete", "Action when a download finishes", self.when_complete)
        self.open_complete = self._toggle(ex.get("open_on_complete", False))
        self._row(g, "Open file when complete", "Launch the file as soon as it finishes", self.open_complete)
        v.addWidget(f); v.addStretch()
        return sa

    def _p_network(self, ex):
        sa, v = self._page("Network", "Configure network and connection settings")
        f, g = self._card()
        self.conn_type = self._combo(["Default (Auto)", "Direct", "System Proxy"], ex.get("connection_type"))
        self._row(g, "Connection Type", "Select your internet connection type", self.conn_type)
        self.max_conn = self._slider(1, 1000, int(ex.get("max_connections", 100) or 100))
        self._row(g, "Max Connections", "Maximum total connections", self.max_conn)
        self.listen_port = QSpinBox(); self.listen_port.setRange(1024, 65535)
        self.listen_port.setValue(int(ex.get("listen_port", 56666) or 56666)); self.listen_port.setFixedWidth(130)
        self._row(g, "Listen Port", "Port for incoming (torrent) connections", self.listen_port)
        self.upnp = self._toggle(ex.get("upnp", True))
        self._row(g, "Use UPnP / NAT-PMP", "Allow automatic port mapping", self.upnp)
        self._proxy_url = (ex.get("proxy") or "").strip()
        self.proxy_btn = QPushButton("Configure" if not self._proxy_url else "Edit")
        self.proxy_btn.clicked.connect(self._config_proxy)
        self._row(g, "Proxy Settings", "Configure a proxy for downloads", self.proxy_btn)
        self.dns_https = self._toggle(ex.get("dns_https", False))
        self._row(g, "DNS over HTTPS", "Use secure DNS for resolving addresses", self.dns_https)
        self._host_rules = dict(ex.get("host_rules", {}) or {})
        n = len(self._host_rules)
        self.hostrules_btn = QPushButton("Configure" if not n else f"Edit ({n})")
        self.hostrules_btn.clicked.connect(self._open_host_rules)
        self._row(g, "Per-host rules", "Override segments / force yt-dlp per host", self.hostrules_btn)
        v.addWidget(f); v.addStretch()
        return sa

    def _open_host_rules(self):
        from gui2.dialogs.host_rules import HostRulesDialog
        HostRulesDialog(self, self._host_rules).exec()
        n = len(self._host_rules)
        self.hostrules_btn.setText("Configure" if not n else f"Edit ({n})")

    def _p_browser(self, ex):
        sa, v = self._page("Browser Integration", "Integrate with your web browser")
        f, g = self._card()
        get = QPushButton("  Get Extension"); get.setIcon(themed_icon("open", "text"))
        self._row(g, "Browser Extension", "Install the HyperFetch extension to catch downloads", get)
        v.addWidget(f)
        f2, g2 = self._card()
        self.browsers = {}
        for b in ("Google Chrome", "Microsoft Edge", "Mozilla Firefox", "Brave", "Opera"):
            tog = self._toggle((ex.get("browsers") or {}).get(b, True))
            self.browsers[b] = tog
            self._row(g2, b, "Detected", tog)
        v.addWidget(f2)
        f3, g3 = self._card()
        trow = QHBoxLayout()
        tok = QLineEdit(self._token); tok.setReadOnly(True)
        copy = QPushButton("Copy"); copy.clicked.connect(lambda: QApplication.clipboard().setText(self._token))
        trow.addWidget(tok, 1); trow.addWidget(copy)
        lab = QLabel("Browser Pairing Token"); lab.setStyleSheet("font-weight:700;background:transparent;")
        g3.addWidget(lab); g3.addLayout(trow)
        v.addWidget(f3)

        # ---- Auto Capture Links allowlist (the app is the source of truth; the
        # extension only has the on/off toggle) ----
        f4, g4 = self._card()
        cap_lab = QLabel("Auto Capture Links"); cap_lab.setStyleSheet("font-weight:700;background:transparent;")
        cap_hint = QLabel("When you click a link in your browser it is captured for "
                          "download by the app. Only the file types below are captured "
                          "(leave empty to capture everything).")
        cap_hint.setWordWrap(True)
        cap_hint.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
        exts = ex.get("capture_exts")
        if exts is None:
            exts = utils.DEFAULT_CAPTURE_EXTS
        self.capture_exts = QLineEdit(" ".join(exts))
        self.capture_exts.setPlaceholderText("e.g. zip rar 7z iso exe mp4   —   space separated; empty = capture all")
        g4.addWidget(cap_lab); g4.addWidget(cap_hint); g4.addWidget(self.capture_exts)
        v.addWidget(f4); v.addStretch()
        return sa

    @staticmethod
    def _parse_exts(text):
        """Split the capture-allowlist field into a clean, lowercase, de-dotted,
        de-duplicated extension list."""
        out = []
        for tok in (text or "").replace(",", " ").split():
            e = tok.lstrip(".").lower()
            if e and e not in out:
                out.append(e)
        return out

    def _p_appearance(self, theme, accent, ex):
        sa, v = self._page("Appearance", "Customize the look and feel")
        f, g = self._card()
        
        # Custom visual theme cards
        self._sel_theme = (theme or "dark").capitalize()
        t_row = QHBoxLayout(); t_row.setSpacing(16)
        self.t_btns = {}
        
        def _make_tcard(name):
            btn = QPushButton(name)
            btn.setFixedSize(130, 84)
            btn.setCursor(Qt.PointingHandCursor)
            bg = COLORS['surface2'] if name == "Dark" else ("#f8fafc" if name == "Light" else COLORS['surface'])
            fg = "white" if name == "Dark" else ("black" if name == "Light" else COLORS['text'])
            btn.setStyleSheet(f"QPushButton {{ background: {bg}; color: {fg}; border: 2px solid transparent; border-radius: 8px; font-weight: 700; font-size: 14px; }}"
                              f"QPushButton:hover {{ border: 2px solid {COLORS['accent2']}; }}")
            btn.clicked.connect(lambda _, n=name: self._set_theme(n))
            return btn
            
        for n in ["Dark", "Light", "System"]:
            btn = _make_tcard(n)
            self.t_btns[n] = btn
            t_row.addWidget(btn)
        t_row.addStretch()
        self._set_theme(self._sel_theme) # apply border to active
        
        lbl = QLabel("Theme"); lbl.setStyleSheet(f"font-weight:700;color:{COLORS['text']};background:transparent;")
        g.addWidget(lbl); g.addLayout(t_row); g.addSpacing(8)
        
        # accent swatches
        srow = QHBoxLayout(); srow.setSpacing(8)
        self._accent_btns = {}
        for key, hexv in ACCENTS.items():
            b = QPushButton(); b.setFixedSize(26, 26); b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(self._swatch_style(hexv, key == accent))
            b.clicked.connect(lambda _=False, k=key: self._pick_accent(k))
            self._accent_btns[key] = b; srow.addWidget(b)
        srow.addStretch()
        acc_l = QLabel("Accent Color"); acc_l.setStyleSheet("font-weight:700;background:transparent;")
        g.addWidget(acc_l); g.addLayout(srow)
        self.font_size = self._combo(["Small", "Medium", "Large"], ex.get("font_size"))
        self._row(g, "Font Size", "Application font size", self.font_size)
        su_cur = "Bytes (KB/s)" if ex.get("speed_units") == "bytes" else "Bits (Kb/s)"
        self.speed_units = self._combo(["Bits (Kb/s)", "Bytes (KB/s)"], su_cur)
        self._row(g, "Speed Units", "Show download speed in bits or bytes", self.speed_units)
        v.addWidget(f); v.addStretch()
        return sa

    def _p_advanced(self, verify, sched_en, sstart, sstop, ex):
        sa, v = self._page("Advanced", "Configure advanced settings")
        f, g = self._card()
        self.verify = self._toggle(verify)
        self._row(g, "Verify HTTPS Certificates", "Verify website certificates (recommended)", self.verify)
        self.sched = self._toggle(sched_en)
        self._row(g, "Scheduler", "Enable time-based scheduling", self.sched)
        srow = QHBoxLayout()
        self.t_start = QTimeEdit(QTime.fromString(sstart, "HH:mm")); self.t_start.setDisplayFormat("HH:mm")
        self.t_stop = QTimeEdit(QTime.fromString(sstop, "HH:mm")); self.t_stop.setDisplayFormat("HH:mm")
        srow.addWidget(QLabel("Start")); srow.addWidget(self.t_start)
        srow.addSpacing(12); srow.addWidget(QLabel("Stop")); srow.addWidget(self.t_stop); srow.addStretch()
        g.addLayout(srow)
        self.disk_cache = self._toggle(ex.get("disk_cache", True))
        self._row(g, "Disk cache", "Improve disk writing performance", self.disk_cache)
        self.preallocate = self._toggle(ex.get("preallocate", False))
        self._row(g, "Pre-allocate disk space", "Reserve the full file size before downloading", self.preallocate)
        self.hash_check = self._toggle(ex.get("hash_check", False))
        self._row(g, "Enable hash verification", "Verify file integrity after download", self.hash_check)
        self.debug_log = self._toggle(ex.get("debug_log", False))
        self._row(g, "Debug logging", "Save detailed logs for troubleshooting", self.debug_log)
        self.console_btn = QPushButton("  Open Console")
        self.console_btn.setIcon(themed_icon("info", "text"))
        self.console_btn.clicked.connect(self._open_console)
        self._row(g, "Developer Console", "Live log viewer for troubleshooting", self.console_btn)
        v.addWidget(f); v.addStretch()
        return sa

    def _open_console(self):
        from gui2.dialogs.console import ConsoleDialog
        ConsoleDialog(self).exec()

    def _p_about(self):
        sa, v = self._page("About", "")
        f, g = self._card()
        title = QLabel(f"HyperFetch v{APP_VERSION}"); title.setStyleSheet("font-weight:800;font-size:15px;background:transparent;")
        desc = QLabel("A modern, fast and reliable download manager."); desc.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
        g.addWidget(title); g.addWidget(desc)
        brow = QHBoxLayout()
        upd = QPushButton("Check for Updates"); upd.clicked.connect(self._check_updates)
        crash = QPushButton("Open Crash Folder"); crash.clicked.connect(self._open_crashes)
        brow.addWidget(upd); brow.addWidget(crash); brow.addStretch()
        g.addLayout(brow)
        self.upd_lbl = QLabel(""); self.upd_lbl.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
        g.addWidget(self.upd_lbl)
        links = QHBoxLayout()
        lic = QPushButton("License"); lic.setObjectName("ghost")
        cred = QPushButton("Credits"); cred.setObjectName("ghost")
        links.addWidget(lic); links.addWidget(cred); links.addStretch()
        g.addLayout(links)
        v.addWidget(f); v.addStretch()
        return sa

    # ---- behaviour ----
    def _swatch_style(self, hexv, sel):
        border = "white" if sel else COLORS["border2"]
        return f"QPushButton{{background:{hexv};border:2px solid {border};border-radius:13px;}}"

    def _pick_accent(self, key):
        self._accent = key
        for k, b in self._accent_btns.items():
            b.setStyleSheet(self._swatch_style(ACCENTS[k], k == key))

    def _set_theme(self, name):
        self._sel_theme = name
        for k, b in self.t_btns.items():
            bg = COLORS['surface2'] if k == "Dark" else ("#f8fafc" if k == "Light" else COLORS['surface'])
            fg = "white" if k == "Dark" else ("black" if k == "Light" else COLORS['text'])
            border = COLORS['accent'] if k == name else COLORS['border']
            b.setStyleSheet(f"QPushButton {{ background: {bg}; color: {fg}; border: 2px solid {border}; border-radius: 8px; font-weight: 700; font-size: 14px; }}"
                            f"QPushButton:hover {{ border: 2px solid {COLORS['accent2']}; }}")


    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Default download folder", self.dir_lbl.text())
        if d:
            self.dir_lbl.setText(d)

    def _config_proxy(self):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "Proxy", "Proxy URL (e.g. http://host:port) — leave blank for none:",
            text=self._proxy_url)
        if ok:
            self._proxy_url = text.strip()
            self.proxy_btn.setText("Edit" if self._proxy_url else "Configure")

    def _check_updates(self):
        self.upd_lbl.setText("Checking…"); QApplication.processEvents()
        import urllib.request, json
        try:
            req = urllib.request.Request("https://api.github.com/repos/tanumay-deb/HyperFetch/releases/latest")
            with urllib.request.urlopen(req, timeout=5) as r:
                latest = json.loads(r.read().decode()).get("tag_name", "")
            self.upd_lbl.setText(f"Update available: {latest}" if latest.lstrip("v") != APP_VERSION
                                 else "You are on the latest version.")
        except Exception:
            self.upd_lbl.setText("Failed to check for updates.")

    def _open_crashes(self):
        import os
        d = crash_reporter.crashes_dir() if hasattr(crash_reporter, "crashes_dir") else getattr(crash_reporter, "CRASH_DIR", "")
        if d and os.path.isdir(d):
            try:
                os.startfile(d)
            except OSError:
                pass

