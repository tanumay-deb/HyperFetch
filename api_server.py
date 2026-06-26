"""Flask endpoint that receives URLs from the browser extension.

Security model (localhost-only desktop app):
- Bound to 127.0.0.1 — not reachable off-machine.
- CORS allows only ``chrome-extension://`` / ``moz-extension://`` origins, so a
  website's JS cannot drive downloads (its cross-origin JSON POST fails preflight).
- ``/download`` additionally requires the pairing token (``X-HyperFetch-Token`` header
  or ``token`` body field) so other local processes / extensions can't queue
  downloads. The user copies the token from the app into the extension once.

Two modes:
- GUI mode (``pending`` given): requests land in a deque; the GUI pops them and
  shows the file-info dialog before anything is queued.
- Headless mode (``python api_server.py``): tasks are queued immediately.
"""
import logging

from flask import Flask, request, jsonify
from flask_cors import CORS

import task as T
import utils

PORT = 5000
log = logging.getLogger("hyperfetch")


def create_app(queue, save_dir, pending=None, token=None):
    app = Flask(__name__)
    # Only browser-extension origins may call cross-origin. Websites use http(s)
    # origins and are rejected at preflight.
    CORS(app, origins=[r"chrome-extension://*", r"moz-extension://*"],
         allow_headers=["Content-Type", "X-HyperFetch-Token"])
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    app.config["HYPERFETCH_TOKEN"] = token

    def _authorized(data):
        expected = app.config.get("HYPERFETCH_TOKEN")
        if not expected:
            return True  # token disabled (e.g. headless/tests)
        presented = request.headers.get("X-HyperFetch-Token") or (data or {}).get("token")
        # constant-time compare
        import hmac
        return bool(presented) and hmac.compare_digest(str(presented), str(expected))

    @app.route("/ping", methods=["GET"])
    def ping():
        # open (no token) so the popup can show connection status; reveals nothing
        return jsonify({"status": "ok", "needsToken": bool(app.config.get("HYPERFETCH_TOKEN"))})

    @app.route("/probe", methods=["POST"])
    def probe():
        """Parse an HLS master's quality variants for the extension's picker.
        The app has the original capture's cookies/referer/UA and no CORS, so
        it reads referer/auth-gated manifests the extension's own fetch can't."""
        data = request.get_json(silent=True) or {}
        if not _authorized(data):
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        url = (data.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return jsonify({"status": "error", "message": "invalid url"}), 400
        headers = {}
        if data.get("cookies"):
            headers["Cookie"] = data["cookies"]
        if data.get("userAgent"):
            headers["User-Agent"] = data["userAgent"]
        if data.get("referrer"):
            headers["Referer"] = data["referrer"]
        import hls
        try:
            variants = hls.probe_variants(url, headers)
        except Exception:
            variants = []
        return jsonify({"variants": variants})

    @app.route("/download", methods=["POST"])
    def download():
        data = request.get_json(silent=True) or {}
        if not _authorized(data):
            return jsonify({"status": "error", "message": "unauthorized"}), 401

        url = (data.get("url") or "").strip()
        # real web downloads + magnet links; block file://, chrome://, javascript:
        if not url.lower().startswith(("http://", "https://", "magnet:")):
            return jsonify({"status": "error", "message": "invalid url"}), 400

        suggested = data.get("filename") or ""

        # Auto-capture allowlist: the extension's browser-download capture sends
        # auto=true. Reject (so the browser keeps the file) when its extension is
        # not in the Settings allowlist. Manual menu/badge/magnet captures have no
        # auto flag and are never filtered. magnet: links carry no extension and
        # are always allowed.
        if data.get("auto") and url.lower().startswith(("http://", "https://")) \
                and not utils.capture_allowed(suggested or url):
            return jsonify({"status": "ignored", "reason": "extension not in capture list"})

        log.info("server received download: %s", url)

        # browser context for auth-gated hosts (Google Drive etc.)
        headers = {}
        if data.get("cookies"):
            headers["Cookie"] = data["cookies"]
        if data.get("userAgent"):
            headers["User-Agent"] = data["userAgent"]
        if data.get("referrer"):
            headers["Referer"] = data["referrer"]

        if pending is not None:
            # GUI decides via the file-info dialog
            pending.append({"url": url, "filename": suggested,
                            "headers": headers})
            return jsonify({"status": "queued"})

        filename = utils.filename_from_url(url, suggested)
        cat_dir = utils.get_category_dir(save_dir, filename)
        save_path = utils.unique_path(cat_dir, filename)
        task = T.DownloadTask(url, save_path, filename=filename, headers=headers)
        queue.add_task(task)
        return jsonify({"status": "queued", "id": task.id, "filename": filename})

    return app


def run_server(queue, save_dir, port=PORT, pending=None, token=None):
    app = create_app(queue, save_dir, pending, token=token)
    # threaded so multiple browser hits don't block; reloader off (background thread)
    app.run(host="127.0.0.1", port=port, threaded=True,
            use_reloader=False, debug=False)


if __name__ == "__main__":
    from queue_manager import QueueManager
    q = QueueManager()
    run_server(q, utils.default_download_dir(), token=utils.get_or_create_token())
