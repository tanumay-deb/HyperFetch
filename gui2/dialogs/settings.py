"""Settings (v2) — left nav + stacked sections: General / Downloads / Browser /
Appearance / Advanced / About (mockups #6–#11). values() returns a flat dict;
the app applies the actionable keys and persists the rest.
"""
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QSlider, QSpinBox, QListWidget, QListWidgetItem, QStackedWidget, QWidget,
    QFrame, QFileDialog, QTimeEdit, QScrollArea, QApplication
)
from PySide6.QtCore import Qt, QTime

import crash_reporter
from gui.theme import APP_VERSION
try:
    from gui.dialogs import AnimatedToggle
except Exception:                       # fallback if unavailable
    from PySide6.QtWidgets import QCheckBox as AnimatedToggle
from gui2.palette import COLORS, ACCENTS, set_accent

_SECTIONS = ["General", "Downloads", "Network", "Browser", "Appearance", "Advanced", "About"]
_SLIDER_QSS = (
    "QSlider::groove:horizontal{height:5px;background:%(b)s;border-radius:3px;}"
    "QSlider::sub-page:horizontal{background:%(a)s;border-radius:3px;}"
    "QSlider::handle:horizontal{background:white;width:16px;margin:-6px 0;border-radius:8px;}"
)


class SettingsDialogV2(QDialog):
    def __init__(self, parent, *, save_dir, max_concurrent, segments, verify_tls,
                 pair_token, theme, accent, sched_en, sched_start, sched_stop, extras=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(760, 540)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self._token = pair_token
        self._accent = accent
        ex = extras or {}

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)

        # left nav
        self.nav = QListWidget(); self.nav.setFixedWidth(190)
        self.nav.setStyleSheet(
            f"QListWidget{{background:{COLORS['surface']};border:none;border-right:1px solid {COLORS['border']};outline:none;padding:14px 8px;}}"
            f"QListWidget::item{{padding:10px 12px;border-radius:8px;color:{COLORS['muted']};font-weight:600;margin-bottom:3px;}}"
            f"QListWidget::item:selected{{background:{COLORS['accent']};color:white;}}")
        for s in _SECTIONS:
            QListWidgetItem(s, self.nav)
        body.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._p_general(save_dir, ex))
        self.stack.addWidget(self._p_downloads(max_concurrent, segments, ex))
        self.stack.addWidget(self._p_network(ex))
        self.stack.addWidget(self._p_browser(ex))
        self.stack.addWidget(self._p_appearance(theme, accent, ex))
        self.stack.addWidget(self._p_advanced(verify_tls, sched_en, sched_start, sched_stop, ex))
        self.stack.addWidget(self._p_about())
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        # footer
        sep = QFrame(); sep.setFixedHeight(1); sep.setStyleSheet(f"background:{COLORS['border']};")
        outer.addWidget(sep)
        foot = QHBoxLayout(); foot.setContentsMargins(20, 12, 20, 12); foot.addStretch()
        reset = QPushButton("Reset"); reset.clicked.connect(self.reject)
        save = QPushButton("Save Changes"); save.setObjectName("primary"); save.clicked.connect(self.accept)
        foot.addWidget(reset); foot.addWidget(save)
        outer.addLayout(foot)

    # ---- small builders ----
    def _page(self, title, subtitle):
        sa = QScrollArea(); sa.setWidgetResizable(True)
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(28, 24, 32, 24); v.setSpacing(8)
        t = QLabel(title); t.setObjectName("dlgTitle")
        s = QLabel(subtitle); s.setStyleSheet(f"color:{COLORS['muted']};background:transparent;")
        v.addWidget(t); v.addWidget(s); v.addSpacing(10)
        sa.setWidget(w); sa._v = v
        return sa, v

    def _card(self):
        f = QFrame(); f.setObjectName("panel")
        g = QVBoxLayout(f); g.setContentsMargins(16, 12, 16, 12); g.setSpacing(14)
        return f, g

    def _row(self, layout, label, desc, widget):
        r = QHBoxLayout()
        col = QVBoxLayout(); col.setSpacing(1)
        l = QLabel(label); l.setStyleSheet(f"font-weight:700;background:transparent;color:{COLORS['text']};")
        col.addWidget(l)
        if desc:
            d = QLabel(desc); d.setStyleSheet(f"color:{COLORS['muted']};font-size:11px;background:transparent;")
            col.addWidget(d)
        r.addLayout(col); r.addStretch(); r.addWidget(widget)
        layout.addLayout(r)

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
        self.lang = self._combo(["System Default", "English"], ex.get("language"))
        self._row(g2, "Language", "Preferred language", self.lang)
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
        self._row(g, "Connections per Download", "Maximum connections for each download", self.conns)
        self.def_queue = self._combo(["Main"], ex.get("default_queue"))
        self._row(g, "Default Queue", "Where new downloads land", self.def_queue)
        self.auto_start = self._toggle(ex.get("auto_start", True))
        self._row(g, "Auto start downloads", "Start downloads immediately after adding", self.auto_start)
        self.speed_limit = self._combo(["Unlimited", "1 Mb/s", "5 Mb/s", "10 Mb/s"], ex.get("speed_limit"))
        self._row(g, "Download Speed Limit", "Global download speed limit", self.speed_limit)
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
        v.addWidget(f); v.addStretch()
        return sa

    def _p_browser(self, ex):
        sa, v = self._page("Browser Integration", "Integrate with your web browser")
        f, g = self._card()
        get = QPushButton("Get Extension ↗")
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
        v.addWidget(f3); v.addStretch()
        return sa

    def _p_appearance(self, theme, accent, ex):
        sa, v = self._page("Appearance", "Customize the look and feel")
        f, g = self._card()
        self.theme = self._combo(["Dark", "Light", "System"], (theme or "dark").capitalize())
        self._row(g, "Theme", "Preferred theme", self.theme)
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
        self.density = self._combo(["Compact", "Comfortable"], ex.get("ui_density"))
        self._row(g, "UI Density", "Spacing of interface elements", self.density)
        self.font_size = self._combo(["Small", "Medium", "Large"], ex.get("font_size"))
        self._row(g, "Font Size", "Application font size", self.font_size)
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
        v.addWidget(f); v.addStretch()
        return sa

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

    def values(self):
        return {
            "save_dir": self.dir_lbl.text(),
            "max_concurrent": self.conc._slider.value(),
            "segments": self.conns._slider.value(),
            "verify_tls": self.verify.isChecked(),
            "theme": self.theme.currentText().lower(),
            "accent": self._accent,
            "sched_en": self.sched.isChecked(),
            "sched_start": self.t_start.time().toString("HH:mm"),
            "sched_stop": self.t_stop.time().toString("HH:mm"),
            # persisted UI prefs (some cosmetic until backend-wired)
            "launch": self.launch.currentText(),
            "minimize_tray": self.min_tray.isChecked(),
            "close_behavior": self.close_beh.currentText(),
            "language": self.lang.currentText(),
            "default_queue": self.def_queue.currentText(),
            "auto_start": self.auto_start.isChecked(),
            "speed_limit": self.speed_limit.currentText(),
            "open_on_complete": self.open_complete.isChecked(),
            "clipboard_monitor": self.clip_mon.isChecked(),
            "when_complete": self.when_complete.currentText(),
            "connection_type": self.conn_type.currentText(),
            "max_connections": self.max_conn._slider.value(),
            "listen_port": self.listen_port.value(),
            "upnp": self.upnp.isChecked(),
            "dns_https": self.dns_https.isChecked(),
            "preallocate": self.preallocate.isChecked(),
            "proxy": self._proxy_url,
            "ui_density": self.density.currentText(),
            "font_size": self.font_size.currentText(),
            "disk_cache": self.disk_cache.isChecked(),
            "hash_check": self.hash_check.isChecked(),
            "debug_log": self.debug_log.isChecked(),
            "browsers": {b: t.isChecked() for b, t in self.browsers.items()},
        }
