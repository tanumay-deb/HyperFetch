"""Who else HyperFetch is made of, and on what terms: the Python packages a
build ships, their versions, licences and licence texts, worked out from what
is installed. HyperFetch.spec writes it into the build (licenses/third-party/,
credits.json beside the texts); Credits reads that, or works it out when run
from source. Credits was a list typed by hand - eleven of the two dozen
packages a build carries - and a build shipped no licence text but aria2's
and FFmpeg's: Qt's LGPL among the missing (2026-11-11).

aria2 and FFmpeg are programs, not packages: licenses/THIRD-PARTY-NOTICES.txt
says which build of each is shipped and where its source is.
"""
import importlib.metadata as md
import json
import os
import platform
import re
import shutil
import sys
import sysconfig

import utils

ROOT = os.path.dirname(os.path.abspath(__file__))
LICENSES = utils.resource_path("licenses")
FOLDER = "third-party"          # in a build's licenses/
CREDITS = "credits.json"
# Qt's wheels carry only Qt's commercial licence. HyperFetch uses Qt under the
# LGPL-3.0, which asks for its text and the GPL's to go with it.
LGPL_TEXTS = ("LGPL-3.0-only.txt", "GPL-3.0-only.txt")
_TEXT = re.compile(r"(?i)^(licen[cs]e|copying|notice|authors)")


def _key(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _needs(dist):
    """The packages `dist` needs on this machine: requirements whose markers
    hold, an extra's left out."""
    try:
        from packaging.requirements import Requirement
    except ImportError:
        # shortcut: without packaging a requirement is taken whatever its
        # marker says (an extra's are still left out); a build always has it
        return [re.match(r"[A-Za-z0-9._-]+", r.strip()).group(0) for r in dist.requires or []
                if not re.search(r"\bextra\s*==", r)]
    out = []
    for r in map(Requirement, dist.requires or []):
        if r.marker is None or r.marker.evaluate({"extra": ""}):
            out.append(r.name)
    return out


def owners(files):
    """The installed distributions these files are of, by each one's list
    of what it installed: a build ships what its Analysis collected, and a
    package can come in unasked (yt-dlp's hook brings PyInstaller's own).
    By file, not by name: another package can install a "gui" too."""
    want = {os.path.normcase(os.path.abspath(f)) for f in files if f}
    out = []
    for dist in md.distributions():
        if any(os.path.normcase(os.path.abspath(str(dist.locate_file(f)))) in want
               for f in dist.files or []):
            out.append(dist)
    return out


def packages(files=None):
    """The installed distributions a build ships: those of `files`, what
    the build collected (HyperFetch.spec) - or, run from source,
    requirements.txt's and what they need. And PyInstaller, whose
    bootloader starts the exe."""
    if files is not None:
        todo = owners(files)
    else:
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
            todo = [re.match(r"[A-Za-z0-9._-]+", ln.strip()).group(0)
                    for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    todo.append("pyinstaller")
    seen, out = set(), []
    while todo:
        dist = todo.pop(0)
        if isinstance(dist, str):
            if _key(dist) in seen:
                continue
            try:
                dist = md.distribution(dist)
            except md.PackageNotFoundError:
                continue                # its marker kept it off; no build tools here
        key = _key(dist.metadata["Name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(dist)
        if files is None and key != "pyinstaller":
            todo += _needs(dist)        # what PyInstaller needs is not shipped
    return out


def _licence(meta):
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    text = (meta.get("License") or "").strip()
    if text and len(text) <= 60 and "\n" not in text:
        return text                     # a name; some packages put the whole text here
    return ", ".join(c.rsplit(" :: ", 1)[-1] for c in meta.get_all("Classifier") or []
                     if c.startswith("License :: ") and c.count(" :: ") >= 2)


def _home(meta):
    urls = {}
    for v in meta.get_all("Project-URL") or []:
        label, _, url = v.partition(",")
        urls.setdefault(label.strip().lower(), url.strip())
    for label in ("homepage", "home", "source", "source code", "repository", "documentation"):
        if urls.get(label):
            return urls[label]
    return (meta.get("Home-page") or "").strip() or next(iter(urls.values()), "")


def _texts(dist, licence):
    """(file on disk, path to ship it at) of each licence text that goes with
    a package: what its .dist-info carries, in its own folders (pywin32 has a
    License.txt in each), the LGPL's for Qt, else ours from its repository
    (licenses/<name>-LICENSE.txt: flask-cors's wheel carries none). Ours go
    as licenses/ has them: no path."""
    texts = []
    for f in dist.files or []:
        p = str(f).replace("\\", "/")
        if ".dist-info/" not in p:
            continue
        rel = p.split(".dist-info/", 1)[1]
        rel = rel[len("licenses/"):] if rel.startswith("licenses/") else rel
        name = rel.rsplit("/", 1)[-1]
        if _TEXT.match(name) and not name.startswith("LicenseRef-Qt-Commercial"):
            texts.append((str(dist.locate_file(f)), rel))
    if "LGPL-3.0" in licence:
        texts += [(os.path.join(LICENSES, t), None) for t in LGPL_TEXTS]
    own = os.path.join(LICENSES, "%s-LICENSE.txt" % _key(dist.metadata["Name"]))
    if not texts and os.path.isfile(own):
        texts = [(own, None)]
    return texts


def _entries(files=None):
    """({name, version, licence, url}, [licence text on disk]) of Python and
    of every package a build ships (packages)."""
    py = os.path.join(sys.base_prefix, "LICENSE.txt")            # Windows
    if not os.path.isfile(py):
        py = os.path.join(sysconfig.get_path("stdlib"), "LICENSE.txt")
    yield ({"name": "Python", "version": platform.python_version(), "licence": "PSF-2.0",
            "url": "https://www.python.org/"},
           [(py, "LICENSE.txt")] if os.path.isfile(py) else [])
    for dist in packages(files):
        meta = dist.metadata
        licence = _licence(meta)
        yield ({"name": meta["Name"], "version": dist.version, "licence": licence,
                "url": _home(meta)}, _texts(dist, licence))


def write(dest, files=None):
    """Into dest - what a build ships as licenses/third-party/ - each one's
    licence texts, in a folder of its name and version, and credits.json,
    which names them relative to licenses/. `files`: what the build
    collected (packages). Returns what it wrote there."""
    os.makedirs(dest, exist_ok=True)
    out = []
    for entry, texts in _entries(files):
        folder = "%s-%s" % (entry["name"], entry["version"])
        files = []
        for path, rel in texts:
            if rel is None:
                files.append(os.path.basename(path))    # licenses/ goes in as it is
                continue
            target = os.path.join(dest, folder, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(path, target)
            files.append("%s/%s/%s" % (FOLDER, folder, rel))
        out.append(dict(entry, files=files))
    bare = [e["name"] for e in out if not e["files"]]
    if bare:
        raise SystemExit("no licence text to ship with %s: put one in licenses/ as "
                         "<name>-LICENSE.txt (third_party._texts)" % ", ".join(bare))
    with open(os.path.join(dest, CREDITS), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def credits():
    """What Credits lists: [{name, version, licence, url, files}]. A frozen
    app has no package metadata to work it out from: it reads what its
    build wrote."""
    if getattr(sys, "frozen", False):
        try:
            with open(os.path.join(sys._MEIPASS, "licenses", FOLDER, CREDITS),
                      encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []
    return [dict(entry, files=[]) for entry, _ in _entries()]
