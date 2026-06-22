"""Settings (v2) — left nav + stacked sections: General / Downloads / Browser /
Appearance / Advanced / About (mockups #6–#11). values() returns a flat dict;
the app applies the actionable keys and persists the rest.
"""
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QSlider, QSpinBox, QListWidget, QListWidgetItem, QStackedWidget, QWidget,
    QFrame, QFileDialog, QTimeEdit, QScrollArea, QApplication, QSizePolicy
)
from PySide6.QtCore import Qt, QTime

import crash_reporter
from gui.theme import APP_VERSION
try:
    from gui.dialogs import AnimatedToggle
except Exception:                       # fallback if unavailable
    from PySide6.QtWidgets import QCheckBox as AnimatedToggle
from gui2.palette import COLORS, ACCENTS, set_accent
from gui.icons import themed_icon

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

        # left nav container
        nav_container = QWidget(); nav_container.setFixedWidth(220)
        nav_container.setStyleSheet(f"background:{COLORS['surface']};border-right:1px solid {COLORS['border']};")
        nv_lay = QVBoxLayout(nav_container); nv_lay.setContentsMargins(16, 24, 16, 16); nv_lay.setSpacing(16)
        
        # Brand logo — defined here, shown in the header beside the search.
        class BrandLogo(QLabel):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setFixedSize(28, 28)
            def paintEvent(self, e):
                from PySide6.QtGui import QPainter, QColor
                p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
                p.setBrush(QColor(COLORS["accent"])); p.setPen(Qt.NoPen)
                p.drawEllipse(self.rect())
                ic = themed_icon("bolt", "white").pixmap(16, 16)
                p.drawPixmap((self.width() - 16) // 2, (self.height() - 16) // 2, ic)
                p.end()
        self._BrandLogo = BrandLogo

        self.nav = QListWidget()
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.nav.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;outline:none;}}"
            f"QListWidget::item{{padding:12px 14px;border-radius:8px;color:{COLORS['muted']};font-weight:700;margin-bottom:4px;}}"
            f"QListWidget::item:selected{{background:{COLORS['accent']};color:white;}}")
            
        icons = ["settings", "download", "link", "open", "program", "menu", "info"]
        for s, ic in zip(_SECTIONS, icons):
            item = QListWidgetItem(themed_icon(ic, "muted"), s)
            self.nav.addItem(item)
        nv_lay.addWidget(self.nav, 1)

        # Mini Speed Graph (pinned below the nav; nav fills the rest so its
        # items never scroll)
        self.mini_speed_lbl = QLabel("Total Speed\n0 b/s")
        self.mini_speed_lbl.setStyleSheet(f"color:{COLORS['text']}; font-size: 11px; font-weight: 600; background: transparent; border: none;")
        nv_lay.addWidget(self.mini_speed_lbl)
        
        from gui2.details_drawer import SpeedGraph
        self.mini_graph = SpeedGraph(history=40); self.mini_graph.setFixedHeight(60)
        self.mini_graph.setStyleSheet("background: transparent; border: none;")
        nv_lay.addWidget(self.mini_graph)
        
        self.mini_conns_lbl = QLabel("Active Connections\n0")
        self.mini_conns_lbl.setStyleSheet(f"color:{COLORS['muted']}; font-size: 11px; background: transparent; border: none;")
        nv_lay.addWidget(self.mini_conns_lbl)
        
        body.addWidget(nav_container)

        # Right side container
        right_container = QWidget()
        right_lay = QVBoxLayout(right_container); right_lay.setContentsMargins(0, 0, 0, 0); right_lay.setSpacing(0)
        
        # Header Search
        head = QFrame(); head.setFixedHeight(72)
        head.setStyleSheet(f"background: {COLORS['bg']}; border-bottom: 1px solid {COLORS['border']};")
        hl = QHBoxLayout(head); hl.setContentsMargins(20, 0, 20, 0); hl.setSpacing(10)
        hl.addWidget(self._BrandLogo())
        brand = QLabel("HyperFetch")
        brand.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {COLORS['text']}; background: transparent; border: none;")
        hl.addWidget(brand)
        hl.addStretch()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search settings…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(280)        # flat style from the global QSS — same as the main search
        self.search.textChanged.connect(self._do_search)
        hl.addWidget(self.search)
        right_lay.addWidget(head)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._p_general(save_dir, ex))
        self.stack.addWidget(self._p_downloads(max_concurrent, segments, ex))
        self.stack.addWidget(self._p_network(ex))
        self.stack.addWidget(self._p_browser(ex))
        self.stack.addWidget(self._p_appearance(theme, accent, ex))
        self.stack.addWidget(self._p_advanced(verify_tls, sched_en, sched_start, sched_stop, ex))
        self.stack.addWidget(self._p_about())
        right_lay.addWidget(self.stack, 1)
        body.addWidget(right_container, 1)
        outer.addLayout(body, 1)

        self.nav.currentRowChanged.connect(self._on_tab_changed)
        self.nav.setCurrentRow(0)

        # footer
        sep = QFrame(); sep.setFixedHeight(1); sep.setStyleSheet(f"background:{COLORS['border']};")
        outer.addWidget(sep)
        foot = QHBoxLayout(); foot.setContentsMargins(20, 12, 20, 12)
        reset = QPushButton("Reset"); reset.clicked.connect(self.reject)
        save = QPushButton("Save Changes"); save.setObjectName("primary"); save.clicked.connect(self.accept)
        foot.addWidget(reset); foot.addStretch(); foot.addWidget(save)
        outer.addLayout(foot)

    def _on_tab_changed(self, idx):
        self.stack.setCurrentIndex(idx)
        self._apply_search()

    def _do_search(self, _text=None):
        self._apply_search()

    def _apply_search(self):
        """Filter settings across ALL sections: hide non-matching rows, hide
        panels left empty, narrow the nav to sections with a hit, and jump to
        the first matching section when the current one has none."""
        if getattr(self, "_searching", False):
            return
        self._searching = True
        try:
            q = self.search.text().strip().lower()
            first_match = None
            for i in range(self.stack.count()):
                inner = self.stack.widget(i).widget()
                page_match = False
                for panel in inner.findChildren(QFrame):
                    if panel.objectName() != "panel":
                        continue
                    rows = panel.findChildren(QWidget, "settingRow")
                    if rows:
                        shown = False
                        for rw in rows:
                            m = (not q) or (q in (rw.property("searchText") or ""))
                            rw.setVisible(m)
                            shown = shown or m
                        panel.setVisible(shown)
                        page_match = page_match or shown
                    else:   # panel without tagged rows (e.g. accent swatches)
                        m = (not q) or any(q in l.text().lower() for l in panel.findChildren(QLabel))
                        panel.setVisible(m)
                        page_match = page_match or m
                self.nav.setRowHidden(i, bool(q) and not page_match)
                if page_match and first_match is None:
                    first_match = i
            cur = self.nav.currentRow()
            if q and first_match is not None and (cur < 0 or self.nav.isRowHidden(cur)):
                self.nav.setCurrentRow(first_match)
        finally:
            self._searching = False

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
        v.addWidget(f3); v.addStretch()
        return sa

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

    def update_live(self, bps, conns):
        from gui.theme import human_speed
        self.mini_speed_lbl.setText(f"Total Speed\n{human_speed(bps) or '0 b/s'}")
        self.mini_conns_lbl.setText(f"Active Connections\n{conns}")
        self.mini_graph.push(bps)

    def values(self):
        return {
            "save_dir": self.dir_lbl.text(),
            "max_concurrent": self.conc._slider.value(),
            "segments": self.conns._slider.value(),
            "verify_tls": self.verify.isChecked(),
            "theme": self._sel_theme.lower(),
            "accent": self._accent,
            "sched_en": self.sched.isChecked(),
            "sched_start": self.t_start.time().toString("HH:mm"),
            "sched_stop": self.t_stop.time().toString("HH:mm"),
            # persisted UI prefs (some cosmetic until backend-wired)
            "launch": self.launch.currentText(),
            "minimize_tray": self.min_tray.isChecked(),
            "close_behavior": self.close_beh.currentText(),
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
            "font_size": self.font_size.currentText(),
            "disk_cache": self.disk_cache.isChecked(),
            "hash_check": self.hash_check.isChecked(),
            "debug_log": self.debug_log.isChecked(),
            "browsers": {b: t.isChecked() for b, t in self.browsers.items()},
        }
