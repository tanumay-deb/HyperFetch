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
import utils
from gui.theme import APP_VERSION
try:
    from gui.dialogs import AnimatedToggle
except Exception:                       # fallback if unavailable
    from PySide6.QtWidgets import QCheckBox as AnimatedToggle
from gui2.palette import COLORS, ACCENTS, set_accent
from gui.icons import themed_icon
from gui2.brand import BrandLogo
from gui2.dialogs.settings_pages import PageBuilderMixin

_SECTIONS = ["General", "Downloads", "Network", "Browser", "Appearance", "Advanced", "About"]


class SettingsDialogV2(PageBuilderMixin, QDialog):
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
        nv_lay = QVBoxLayout(nav_container); nv_lay.setContentsMargins(12, 14, 12, 12); nv_lay.setSpacing(8)
        
        self.nav = QListWidget()
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.nav.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;outline:none;}}"
            f"QListWidget::item{{padding:7px 12px;border-radius:8px;color:{COLORS['muted']};font-weight:700;margin-bottom:2px;}}"
            f"QListWidget::item:selected{{background:{COLORS['accent']};color:white;}}")
            
        icons = ["settings", "download", "link", "open", "program", "menu", "info"]
        for s, ic in zip(_SECTIONS, icons):
            item = QListWidgetItem(themed_icon(ic, "muted"), s)
            self.nav.addItem(item)
        nv_lay.addWidget(self.nav, 1)

        # stats card — identical to the main-window sidebar (graph + readout)
        from gui2.speed_gauge import SpeedGraph
        self.stats = QFrame()
        self.stats.setObjectName("statsCard")
        sv = QVBoxLayout(self.stats)
        sv.setContentsMargins(12, 10, 12, 10)
        sv.setSpacing(8)
        self.graph = SpeedGraph()
        self.graph.setFixedHeight(54)
        sv.addWidget(self.graph)
        gtext = QWidget(); gtext.setStyleSheet("background: transparent;")
        gt = QHBoxLayout(gtext); gt.setContentsMargins(0, 0, 0, 0)
        scol = QVBoxLayout(); scol.setSpacing(0)
        self.lbl_speed = QLabel("0 B/s")
        self.lbl_speed.setStyleSheet(f"font-size: 16px; font-weight: 800; background: transparent; color: {COLORS['text']};")
        cap1 = QLabel("Current Speed"); cap1.setStyleSheet(f"font-size: 11px; color: {COLORS['muted']}; background: transparent;")
        scol.addWidget(self.lbl_speed); scol.addWidget(cap1)
        ccol = QVBoxLayout(); ccol.setSpacing(0)
        self.lbl_conns = QLabel("0"); self.lbl_conns.setAlignment(Qt.AlignRight)
        self.lbl_conns.setStyleSheet(f"font-size: 16px; font-weight: 800; background: transparent; color: {COLORS['text']};")
        cap2 = QLabel("Connections"); cap2.setAlignment(Qt.AlignRight)
        cap2.setStyleSheet(f"font-size: 11px; color: {COLORS['muted']}; background: transparent;")
        ccol.addWidget(self.lbl_conns); ccol.addWidget(cap2)
        gt.addLayout(scol); gt.addStretch(); gt.addLayout(ccol)
        sv.addWidget(gtext)
        nv_lay.addWidget(self.stats)

        body.addWidget(nav_container)

        # Right side container
        right_container = QWidget()
        right_lay = QVBoxLayout(right_container); right_lay.setContentsMargins(0, 0, 0, 0); right_lay.setSpacing(0)
        
        # Header Search
        head = QFrame(); head.setFixedHeight(72)
        head.setStyleSheet(f"background: {COLORS['bg']}; border-bottom: 1px solid {COLORS['border']};")
        hl = QHBoxLayout(head); hl.setContentsMargins(20, 0, 20, 0); hl.setSpacing(10)
        hl.addWidget(BrandLogo(26))
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
    def update_live(self, bps, conns):
        from gui.theme import human_speed
        self.lbl_speed.setText(human_speed(bps) or "0 b/s")
        self.lbl_conns.setText(str(conns))
        self.graph.push(bps)

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
            "categorize": self.categorize.isChecked(),
            "speed_limit": self.speed_limit.currentText(),
            "throttle_enabled": self.throttle_en.isChecked(),
            "throttle_start": self.thr_start.time().toString("HH:mm"),
            "throttle_stop": self.thr_stop.time().toString("HH:mm"),
            "throttle_limit": self.thr_limit.currentText(),
            "open_on_complete": self.open_complete.isChecked(),
            "clipboard_monitor": self.clip_mon.isChecked(),
            "when_complete": self.when_complete.currentText(),
            "connection_type": self.conn_type.currentText(),
            "max_connections": self.max_conn._slider.value(),
            "listen_port": self.listen_port.value(),
            "upnp": self.upnp.isChecked(),
            "dns_https": self.dns_https.isChecked(),
            "host_rules": self._host_rules,
            "preallocate": self.preallocate.isChecked(),
            "proxy": self._proxy_url,
            "font_size": self.font_size.currentText(),
            "speed_units": "bytes" if self.speed_units.currentText().startswith("Bytes") else "bits",
            "disk_cache": self.disk_cache.isChecked(),
            "hash_check": self.hash_check.isChecked(),
            "debug_log": self.debug_log.isChecked(),
            "browsers": {b: t.isChecked() for b, t in self.browsers.items()},
            "capture_exts": self._parse_exts(self.capture_exts.text()),
        }
