"""The public users site, on its own port.

Separate from ``api_server`` on purpose, and the separation is the security
design rather than a tidiness preference:

- The tunnel runs on this machine, so every visitor arrives as 127.0.0.1. Any
  "is this caller local?" test would wave a stranger through to the control
  page. Two ports means the control routes are simply **not registered here**,
  so there is nothing to spoof past and no header to trust.
- Its own session key, so a cookie minted here can never authenticate against
  the control app.
- No extension routes. ``/download``, ``/pair`` and friends do not exist on
  this app at all.

Bound to 127.0.0.1. A tunnel (``tailscale funnel 5001``) is what makes it
reachable, which is why nothing here opens a port on the network itself.
"""
import logging
import os
import sys

from datetime import timedelta

from flask import (Flask, request, jsonify, session, send_file,
                   send_from_directory)

import site_auth
import site_limits
import task as T
import utils

PORT = 5001
log = logging.getLogger("hyperfetch.site")

# The built front end. Absent during development and in a build that never ran
# the front-end step, so its absence is handled rather than assumed.
SITE_DIRNAME = "site"


def site_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, SITE_DIRNAME)


HOLDING_PAGE = """<!doctype html><meta charset="utf-8">
<title>HyperFetch</title>
<style>
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0e14;
      color:#f1f5f9;font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
 main{max-width:30rem;padding:2rem;text-align:center}
 h1{font-size:1.2rem;margin:0 0 .5rem}
 p{color:#94a3b8;margin:0}
</style>
<main><h1>HyperFetch</h1><p>%s</p></main>
"""


def create_site_app(queue, save_dir):
    app = Flask(__name__)
    app.secret_key = site_auth.secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # This app is only ever reached over HTTPS, because the tunnel
        # terminates TLS. The control app cannot set this — it answers plain
        # HTTP on a LAN — which is exactly why the two configs differ.
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_NAME="hyperfetch_site",
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=64 * 1024,      # nothing here accepts a file body
    )
    # Two throttles: one per address so a single noisy device cannot lock out
    # the household, one per account so guessing at one username from many
    # addresses still meets a wall.
    by_addr = site_auth.Throttle()
    by_user = site_auth.Throttle()

    # ---------------------------------------------------------------- helpers
    def caller():
        return request.remote_addr or "?"

    def current_user():
        """The signed-in account, or None.

        The stamp covers the password hash *and* the status, so resetting a
        password or disabling an account ends its live sessions on the next
        request rather than at the next restart.
        """
        uid = session.get("uid")
        if not uid:
            return None
        if session.get("st") != site_auth.stamp(uid):
            return None
        u = site_auth.get_user(uid)
        if not u or u.get("status") != site_auth.STATUS_ACTIVE:
            return None
        return site_auth.public(u)

    def require_user():
        """(user, None) when the caller may proceed, else (None, response)."""
        if not site_auth.is_enabled():
            # Deliberately not a connection error: the tunnel stays up so this
            # reads as maintenance rather than a broken link.
            return None, (jsonify({"status": "error", "code": "unavailable",
                                   "message": "HyperFetch is not accepting "
                                              "requests right now."}), 503)
        u = current_user()
        if not u:
            return None, (jsonify({"status": "error", "code": "auth",
                                   "message": "Sign in to continue."}), 401)
        return u, None

    def owned(task_id, user):
        """This user's task, or None. The ownership check, in one place.

        Task ids are uuid4, so guessing is not realistic — but ids leak through
        screenshots, browser history and logs, so entropy is not the control.
        This is.
        """
        t = queue.get_task(task_id)
        if not t:
            return None
        if (getattr(t, "owner", "") or "") != user["username"]:
            return None
        return t

    def as_json(t):
        """One download as the site sees it. An explicit allow-list, never the
        task's own dict: tasks carry cookies and auth headers in memory."""
        total = int(getattr(t, "total_size", 0) or 0)
        done = int(getattr(t, "downloaded", 0) or 0)
        return {
            "id": t.id,
            "name": t.filename or "",
            "status": t.status,
            "totalBytes": total,
            "doneBytes": done,
            "percent": round(done * 100.0 / total, 2) if total else 0.0,
            "added": float(getattr(t, "added", 0) or 0),
            "error": getattr(t, "error", "") or "",
            "isTorrent": str(t.url or "").lower().startswith("magnet:")
                         or str(t.filename or "").lower().endswith(".torrent"),
            "peers": int(getattr(t, "tor_conns", 0) or 0),
            "seeds": int(getattr(t, "tor_seeds", 0) or 0),
            "seeding": bool(getattr(t, "seeding", False)),
            "fetchingMeta": bool(getattr(t, "meta_fetching", False)),
            "metaFailed": bool(getattr(t, "meta_failed", False)),
            "expiresInDays": site_limits.days_left(t),
        }

    def mine(user):
        return [t for t in list(queue.tasks)
                if (getattr(t, "owner", "") or "") == user["username"]]

    # ------------------------------------------------------------- the pages
    @app.route("/", methods=["GET"])
    @app.route("/<path:rest>", methods=["GET"])
    def index(rest=""):
        """The front end, or a holding page when it has not been built.

        Unknown paths fall here so the single-page app can own its own routing
        — except anything under /api, which must 404 rather than be handed a
        page. A catch-all that answers 200 with HTML to a mistyped API route
        turns a typo into a mystery.
        """
        if rest.startswith("api/"):
            return jsonify({"status": "error", "message": "no such endpoint"}), 404
        if not site_auth.is_enabled():
            return HOLDING_PAGE % ("This service is temporarily unavailable. "
                                   "Please try again later."), 503
        built = os.path.join(site_dir(), "index.html")
        if os.path.isfile(built):
            return send_from_directory(site_dir(), "index.html")
        return HOLDING_PAGE % "The web client has not been built yet.", 200

    @app.route("/assets/<path:name>", methods=["GET"])
    def asset(name):
        """Built front-end assets. send_from_directory refuses to escape the
        folder, so a crafted name cannot walk up the filesystem."""
        d = os.path.join(site_dir(), "assets")
        if not os.path.isdir(d):
            return ("", 404)
        return send_from_directory(d, name)

    # ------------------------------------------------------------- accounts
    @app.route("/api/session", methods=["GET"])
    def api_session():
        u = current_user()
        return jsonify({
            "enabled": site_auth.is_enabled(),
            "user": {"username": u["username"], "quota": u["quota"]} if u else None,
        })

    @app.route("/api/signup", methods=["POST"])
    def api_signup():
        if not site_auth.is_enabled():
            return jsonify({"status": "error", "code": "unavailable",
                            "message": "HyperFetch is not accepting requests "
                                       "right now."}), 503
        addr = caller()
        if by_addr.locked_for(addr) > 0:
            return jsonify({"status": "error", "code": "locked",
                            "message": "Too many attempts. Try again later."}), 429
        d = request.get_json(silent=True) or {}

        # One reply for every outcome. Usernames are the login here, so saying
        # "that one is taken" hands over half a credential. The cost is that a
        # weaker oracle survives — sign up, then try to sign in — which costs
        # two requests and meets the login throttle. Email confirmation closes
        # it properly when the app can send mail.
        generic = jsonify({"status": "ok",
                           "message": "If that username was free, your account "
                                      "is ready. Sign in to continue."})
        try:
            site_auth.create_user(d.get("username"), d.get("email"),
                                  d.get("password"), d.get("code"))
        except ValueError as e:
            by_addr.record_failure(addr)
            # The invite code and the password rule are about the request
            # itself, not about who else exists, so those are safe to say.
            msg = str(e)
            if "invite" in msg.lower() or "password" in msg.lower():
                return jsonify({"status": "error", "message": msg}), 400
            log.info("signup refused from %s: %s", addr, msg)
            return generic
        return generic

    @app.route("/api/login", methods=["POST"])
    def api_login():
        if not site_auth.is_enabled():
            return jsonify({"status": "error", "code": "unavailable",
                            "message": "HyperFetch is not accepting requests "
                                       "right now."}), 503
        d = request.get_json(silent=True) or {}
        name = (d.get("username") or "").strip()
        addr = caller()
        key = site_auth.normalise_username(name)

        for throttle, k in ((by_addr, addr), (by_user, key)):
            wait = throttle.locked_for(k)
            if wait > 0:
                return jsonify({"status": "error", "code": "locked",
                                "message": "Too many attempts. Try again in "
                                           "%ds." % (int(wait) + 1),
                                "retryAfter": int(wait) + 1}), 429

        u = site_auth.verify(name, d.get("password") or "")
        if not u:
            by_addr.record_failure(addr)
            by_user.record_failure(key)
            log.warning("failed site login from %s", addr)
            return jsonify({"status": "error", "code": "bad-login",
                            "message": "Wrong username or password."}), 401

        by_addr.record_success(addr)
        by_user.record_success(key)
        session.clear()
        session["uid"] = u["id"]
        session["st"] = site_auth.stamp(u["id"])
        session.permanent = True
        log.info("site login: %s", u["username"])
        return jsonify({"status": "ok", "user": {"username": u["username"],
                                                 "quota": u["quota"]}})

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.clear()
        return jsonify({"status": "ok"})

    # ------------------------------------------------------------ downloads
    @app.route("/api/downloads", methods=["GET"])
    def api_list():
        u, deny = require_user()
        if deny:
            return deny
        rows = mine(u)
        used = site_limits.usage_bytes(save_dir, u["username"])
        return jsonify({
            "downloads": [as_json(t) for t in rows],
            "usedBytes": used,
            "quotaBytes": u["quota"],
            "activeCount": site_limits.active_count(rows, u["username"]),
            "activeLimit": site_limits.MAX_ACTIVE_PER_USER,
        })

    @app.route("/api/downloads", methods=["POST"])
    def api_add():
        u, deny = require_user()
        if deny:
            return deny
        url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
        # Same allow-list as everywhere else: no file://, no chrome://, no
        # javascript:. This app is reachable from the internet, so it matters
        # more here, not less.
        if not url.lower().startswith(("http://", "https://", "magnet:")):
            return jsonify({"status": "error",
                            "message": "That does not look like a link or a "
                                       "magnet."}), 400

        why = site_limits.refusal(save_dir, u["username"], quota=u["quota"])
        if why:
            return jsonify({"status": "error", "code": "limit",
                            "message": why}), 409

        fn = utils.filename_from_url(url) or "download"
        try:
            folder = utils.user_download_dir(save_dir, u["username"])
        except (ValueError, OSError) as e:
            log.error("could not make a folder for %s: %s", u["username"], e)
            return jsonify({"status": "error",
                            "message": "Could not prepare your folder."}), 500

        path = utils.unique_path(folder, fn)
        t = T.DownloadTask(url, path, filename=fn)
        t.owner = u["username"]
        # Its own queue, so site traffic can never take every slot from the
        # desktop app. QueueManager creates the queue on first use.
        t.queue_name = site_limits.WEB_QUEUE
        # Over the per-user cap, it waits rather than being refused: the person
        # asked for it, and a queue is the honest answer to "not right now".
        start = site_limits.active_count(mine(u), u["username"]) < \
            site_limits.MAX_ACTIVE_PER_USER
        queue.add_task(t, start=start)
        return jsonify({"status": "queued", "id": t.id, "started": start})

    @app.route("/api/downloads/<task_id>/pause", methods=["POST"])
    def api_pause(task_id):
        u, deny = require_user()
        if deny:
            return deny
        t = owned(task_id, u)
        if not t:
            return jsonify({"status": "error", "message": "no such download"}), 404
        queue.pause_task(t)
        return jsonify({"status": "ok"})

    @app.route("/api/downloads/<task_id>/resume", methods=["POST"])
    def api_resume(task_id):
        u, deny = require_user()
        if deny:
            return deny
        t = owned(task_id, u)
        if not t:
            return jsonify({"status": "error", "message": "no such download"}), 404
        why = site_limits.refusal(save_dir, u["username"], quota=u["quota"])
        if why:
            return jsonify({"status": "error", "code": "limit", "message": why}), 409
        queue.resume_task(t)
        return jsonify({"status": "ok"})

    @app.route("/api/downloads/<task_id>", methods=["DELETE"])
    def api_delete(task_id):
        u, deny = require_user()
        if deny:
            return deny
        t = owned(task_id, u)
        if not t:
            return jsonify({"status": "error", "message": "no such download"}), 404
        queue.remove_task(t)
        # Unlike the control app, this deletes the file. A site user has no
        # other way to reach it, so leaving it behind would create storage
        # nobody can see and nobody can reclaim.
        removed = _delete_files(t, save_dir, u["username"])
        return jsonify({"status": "ok", "filesRemoved": removed})

    # ---------------------------------------------------------------- files
    @app.route("/api/downloads/<task_id>/files", methods=["GET"])
    def api_files(task_id):
        u, deny = require_user()
        if deny:
            return deny
        t = owned(task_id, u)
        if not t:
            return jsonify({"status": "error", "message": "no such download"}), 404
        from api_server import servable_files, MAX_LISTED_FILES
        files = servable_files(t)
        return jsonify({
            "ready": t.status == T.COMPLETED,
            "truncated": len(files) >= MAX_LISTED_FILES,
            "files": [{"index": i, "name": f["name"], "path": f["rel"],
                       "size": f["size"]} for i, f in enumerate(files)],
        })

    @app.route("/api/downloads/<task_id>/file", methods=["GET"])
    @app.route("/api/downloads/<task_id>/file/<int:index>", methods=["GET"])
    def api_file(task_id, index=0):
        u, deny = require_user()
        if deny:
            return deny
        t = owned(task_id, u)
        if not t:
            return jsonify({"status": "error", "message": "no such download"}), 404
        if t.status != T.COMPLETED:
            return jsonify({"status": "error", "code": "not-ready",
                            "message": "That is still downloading."}), 409

        from api_server import servable_files
        files = servable_files(t)
        if not files:
            return jsonify({"status": "error", "code": "gone",
                            "message": "That file is no longer here."}), 404
        if index < 0 or index >= len(files):
            return jsonify({"status": "error", "message": "no such file"}), 404

        f = files[index]
        # Second containment check, independent of ownership. One of the two
        # failing must not be enough to hand over another account's file.
        if not _inside(f["path"], save_dir, u["username"]):
            log.error("refused a file outside %s's folder: %s",
                      u["username"], f["path"])
            return jsonify({"status": "error", "message": "no such file"}), 404

        inline = request.args.get("inline") == "1"
        # conditional=True is what makes this usable on a phone: Werkzeug
        # answers Range requests, so Safari streams a video instead of pulling
        # all of it, and a dropped transfer resumes rather than restarting.
        return send_file(f["path"], as_attachment=not inline,
                         download_name=f["name"], conditional=True)

    return app


def _inside(path, save_dir, username):
    try:
        root = os.path.realpath(utils.user_download_dir(save_dir, username))
        real = os.path.realpath(path)
    except (ValueError, OSError):
        return False
    return real == root or real.startswith(root + os.sep)


def _delete_files(t, save_dir, username):
    """Remove a finished download's files. Confined to the owner's folder.

    Returns how many files went. Anything that resolves outside the folder is
    left alone rather than deleted, because the alternative is a bug that
    removes somebody else's data.
    """
    import shutil
    from api_server import servable_files
    gone = 0
    for f in servable_files(t):
        if not _inside(f["path"], save_dir, username):
            log.error("refused to delete outside %s's folder: %s",
                      username, f["path"])
            continue
        try:
            os.remove(f["path"])
            gone += 1
        except OSError:
            pass
    # A torrent's own directory, once its files are gone.
    root = getattr(t, "save_path", "") or ""
    if root and os.path.isdir(root) and _inside(root, save_dir, username):
        try:
            shutil.rmtree(root, ignore_errors=True)
        except OSError:
            pass
    return gone


def run_site_server(queue, save_dir, port=PORT):
    """Serve the site. Loopback only — a tunnel is what publishes it.

    Waitress rather than Flask's development server: this one streams
    multi-gigabyte files to phones over slow links, and the dev server holds a
    thread per request for the whole transfer with no slow-client protection.
    """
    app = create_site_app(queue, save_dir)
    try:
        from waitress import serve
    except ImportError:
        log.warning("waitress is not installed — falling back to the "
                    "development server, which is not built for streaming "
                    "large files")
        app.run(host="127.0.0.1", port=port, threaded=True,
                use_reloader=False, debug=False)
        return
    serve(app, host="127.0.0.1", port=port,
          # Each in-flight download holds a thread for its whole transfer, so
          # this is sized for concurrent file grabs rather than for page views.
          threads=16,
          # A phone on a slow link is not a stalled client; give it room.
          channel_timeout=900,
          ident="HyperFetch")
