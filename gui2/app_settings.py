"""Settings persistence + application for the main window.

`SettingsMixin` is mixed into `DownloadAppV2`; its methods run on the live window
instance (all state is via `self`). Split out of `app.py` to keep that file
focused on the view/lifecycle.
"""
import os
import threading

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

import utils
from gui.theme import apply_theme
from gui2 import palette

MAX_CONCURRENT = 3
SEGMENTS = 8


def _parse_rate(text):
    """"500 KB/s" -> bytes per second. 0 for "Unlimited" or anything odd."""
    if not text or "Unlimited" in text:
        return 0
    try:
        n = float(str(text).split()[0])
    except (ValueError, IndexError):
        return 0
    unit = str(text).upper()
    if "MB" in unit:
        return int(n * 1024 * 1024)
    if "KB" in unit:
        return int(n * 1024)
    if "MB/S" in unit or "M" in unit.split("/")[0]:
        return int(n * 1024 * 1024)
    return int(n)


class SettingsMixin:
    # ------------------------------------------------------------- load / save
    def _load_settings(self):
        s = utils.load_json(self._settings_path, {})
        self._extras = dict(s)               # preserves UI-only prefs across saves
        self.save_dir = s.get("save_dir") or utils.default_download_dir()
        if not os.path.isdir(self.save_dir):
            self.save_dir = utils.default_download_dir()
        self.max_concurrent = int(s.get("max_concurrent", MAX_CONCURRENT))
        self.segments = int(s.get("segments", SEGMENTS))
        self.global_speed_limit = int(s.get("global_speed_limit", 0))
        utils.global_limiter.set_limit(self.global_speed_limit)
        self.verify_tls = bool(s.get("verify_tls", True))
        utils.VERIFY_TLS = self.verify_tls
        self.theme = s.get("theme", "dark")
        apply_theme(self.theme)                       # for the shared PropertiesDialog
        palette.set_theme(self.theme)                 # v2 palette (dark / light / system)
        palette.set_accent(s.get("accent", "purple"))  # v2 widgets
        self.pair_token = utils.get_or_create_token()
        self.queues_config = s.get("queues", [{"name": "Main", "max_concurrent": self.max_concurrent}])
        self.scheduler_enabled = bool(s.get("scheduler_enabled", False))
        self.scheduler_start = s.get("scheduler_start", "02:00")
        self.scheduler_stop = s.get("scheduler_stop", "08:00")
        self._apply_network_settings()

    def _save_settings(self):
        data = dict(getattr(self, "_extras", {}))
        data.update({
            "save_dir": self.save_dir,
            "max_concurrent": self.max_concurrent,
            "segments": self.segments,
            "global_speed_limit": getattr(self, "global_speed_limit", 0),
            "verify_tls": getattr(self, "verify_tls", True),
            "theme": getattr(self, "theme", "dark"),
            "accent": next((k for k, v in palette.ACCENTS.items() if v == palette.COLORS["accent"]), "purple"),
            "queues": [{"name": q.name, "max_concurrent": q.max_concurrent} for q in self.queue.queues.values()],
            "scheduler_enabled": getattr(self, "scheduler_enabled", False),
            "scheduler_start": getattr(self, "scheduler_start", "02:00"),
            "scheduler_stop": getattr(self, "scheduler_stop", "08:00"),
        })
        utils.save_json(self._settings_path, data)

    # ------------------------------------------------------------- apply
    def _apply_settings(self, v):
        if os.path.isdir(v["save_dir"]):
            self.save_dir = v["save_dir"]
        self.max_concurrent = v["max_concurrent"]
        self.segments = v["segments"]
        self.queue.set_max_concurrent("Main", v["max_concurrent"])
        # keep the aria2 daemon's own queue wider than the app's, live — else
        # raising the limit appears to do nothing while aria2 holds the extras
        utils.MAX_CONCURRENT_DOWNLOADS = v["max_concurrent"]
        try:
            import aria2d
            aria2d.DAEMON.apply_concurrency()
        except Exception:
            pass
        self.queue.segments = v["segments"]
        self.verify_tls = v["verify_tls"]
        utils.VERIFY_TLS = v["verify_tls"]
        # global speed limit (combo "Unlimited" / "N Mb/s")
        bps = 0
        if "Mb/s" in v.get("speed_limit", ""):
            try:
                bps = int(v["speed_limit"].split()[0]) * 1000 * 1000 // 8
            except ValueError:
                bps = 0
        self.global_speed_limit = bps
        theme_changed = (v["theme"] != self.theme)
        self.theme = v["theme"]
        import downloader
        if hasattr(downloader._GLOBAL_CONNS, "set_limit"):
            downloader._GLOBAL_CONNS.set_limit(self.max_concurrent * self.segments)
        apply_theme(self.theme)
        # A light/dark switch can't be applied live (widgets bake colours at build
        # time), so persist it (below) and auto-restart for a clean re-skin.
        if theme_changed:
            if hasattr(self, "_toasts"):
                self._toasts.show("info", "Applying theme", "Restarting HyperFetch…")
            QTimer.singleShot(700, self._restart_app)
        if palette.ACCENTS.get(v["accent"]) != palette.COLORS["accent"]:
            palette.set_accent(v["accent"])
            self.setStyleSheet(palette.qss())        # live accent re-skin (QSS widgets)
            self.sidebar.set_active(self._filter if self._filter in self.sidebar._rows else "All")
        self.scheduler_enabled = v["sched_en"]
        self.scheduler_start = v["sched_start"]
        self.scheduler_stop = v["sched_stop"]
        self._apply_web_settings(v)      # must run BEFORE the line below
        self._extras.update(v)
        self._apply_network_settings()
        self._apply_throttle()           # throttle window may override the global limit
        self._apply_appearance()
        self._save_settings()
        self.refresh()

    def _apply_web_settings(self, v):
        """Web Client page -> web_auth.json.

        Runs before `self._extras.update(v)` and REMOVES the password from `v`,
        because everything left in that dict is written to settings.json in
        plain text. The password belongs only in web_auth.json, hashed.

        The credentials live in web_auth.json rather than settings.json for the
        same reason: settings.json is read and rewritten constantly and has no
        business holding a secret.
        """
        import web_auth
        pw = v.pop("web_password", "") or ""
        if "web_enabled" not in v:
            return                       # dialog without the page (older caller)
        user = (v.get("web_username") or "").strip() or web_auth.DEFAULT_USERNAME
        v["web_username"] = user
        want_on = bool(v.get("web_enabled"))

        try:
            if pw:
                web_auth.set_password(pw, user=user)
            elif user != web_auth.username():
                web_auth.set_username(user)
        except ValueError as e:
            # Too short. Say so and leave the old password alone rather than
            # switching the client on with credentials that were never stored.
            self._web_toast("error", "Password not changed", str(e))
            want_on = want_on and web_auth.has_password()

        if want_on and not web_auth.has_password():
            self._web_toast("error", "Web client not enabled",
                            "Set a password for it first.")
            want_on = False

        web_auth.set_enabled(want_on)
        v["web_enabled"] = want_on

    def _web_toast(self, kind, title, msg):
        try:
            self._toasts.show(kind, title, msg)
        except Exception:
            import logging
            logging.getLogger("hyperfetch.gui").warning("%s: %s", title, msg)

    def _apply_appearance(self):
        """Apply the Appearance font-size setting.

        Two halves: QApplication.setFont covers widgets with no explicit size,
        and palette.set_font_scale drives every size the stylesheet declares
        (a stylesheet font-size always beats the application font, so setting
        the app font alone had almost no visible effect). Inline widget styles
        read the scale when they are constructed, so a change applies fully
        after the restart the settings dialog offers.
        """
        from gui2 import palette
        name = self._extras.get("font_size", "Medium")
        palette.set_font_scale(name)
        pt = {"Small": 9, "Medium": 10, "Large": 12}.get(name, 10)
        app = QApplication.instance()
        if app:
            f = app.font(); f.setPointSize(pt); app.setFont(f)
        # re-generate the stylesheet so the app chrome resizes immediately
        self.setStyleSheet(palette.qss())

    def _apply_network_settings(self):
        """Push persisted Network/Advanced prefs into the backend globals the
        downloader + torrent engine read each request/launch."""
        ex = self._extras
        mc = ex.get("max_connections")
        utils.MAX_CONNECTIONS = int(mc) if mc else 0
        utils.LISTEN_PORT = int(ex.get("listen_port", 0) or 0)
        utils.DISK_CACHE = bool(ex.get("disk_cache", True))
        utils.PREALLOCATE = bool(ex.get("preallocate", False))
        utils.TORRENT_PREVIEW = bool(ex.get("torrent_preview", False))
        utils.SEED_ENABLED = bool(ex.get("seed_enabled", False))
        utils.SEED_RATIO = float(ex.get("seed_ratio", 1.0) or 0)
        utils.SEED_MINUTES = int(ex.get("seed_minutes", 0) or 0)
        utils.MAX_UPLOAD_BPS = _parse_rate(ex.get("upload_limit"))
        utils.HASH_CHECK = bool(ex.get("hash_check", False))
        # Auto-capture allowlist (Settings -> Browser). The Flask /download endpoint
        # reads utils.CAPTURE_EXTS to filter the extension's auto-captures.
        ce = ex.get("capture_exts")
        utils.CAPTURE_EXTS = list(ce) if isinstance(ce, list) else list(utils.DEFAULT_CAPTURE_EXTS)
        utils.SPEED_IN_BYTES = (ex.get("speed_units") == "bytes")
        utils.BADGE_CORNER = ex.get("badge_corner", "top-right")
        hr = ex.get("host_rules")
        utils.HOST_RULES = dict(hr) if isinstance(hr, dict) else {}
        utils.setup_logging(bool(ex.get("debug_log", False)))
        # DNS-over-HTTPS: override the resolver for all in-process HTTP downloads
        import doh
        doh.enable(bool(ex.get("dns_https", False)))
        # UPnP: open the torrent listen port on the router (best-effort, threaded).
        # Uses the effective port — the user's setting OR the engine default —
        # because gating on a non-zero LISTEN_PORT meant the mapping never ran
        # for anyone who had not manually picked a port, so no inbound peers.
        if bool(ex.get("upnp", True)):
            import upnp, torrent as _tor
            threading.Thread(target=upnp.map_port, args=(_tor.listen_port(),),
                             daemon=True).start()
        ctype = ex.get("connection_type", "Default (Auto)")
        purl = (ex.get("proxy") or "").strip()
        if ctype == "Direct":
            utils.PROXIES = {}                       # force direct, ignore env proxies
        elif purl:
            utils.PROXIES = {"http": purl, "https": purl}
        else:
            utils.PROXIES = None                     # auto / system / env
