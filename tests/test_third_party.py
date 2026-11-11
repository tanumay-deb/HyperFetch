"""Every package a build ships goes with its licence text (2026-11-11). A
build carried none but aria2's and FFmpeg's - Qt's LGPL among the missing -
and Credits named eleven of the two dozen packages, typed by hand."""
import json
import os
import re

import third_party

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _requirements():
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        return [re.match(r"[A-Za-z0-9._-]+", ln.strip()).group(0)
                for ln in f if ln.strip() and not ln.startswith("#")]


def test_every_package_a_build_ships_goes_with_its_licence_text(tmp_path):
    out = third_party.write(str(tmp_path))
    with open(tmp_path / third_party.CREDITS, encoding="utf-8") as f:
        assert json.load(f) == out
    names = {third_party._key(e["name"]) for e in out}
    # requirements.txt's, and what they need
    for name in _requirements() + ["Python", "Werkzeug", "urllib3", "certifi", "shiboken6"]:
        assert third_party._key(name) in names, name
    for e in out:
        sizes = []
        for f in e["files"]:
            if f.startswith(third_party.FOLDER + "/"):
                path = os.path.join(str(tmp_path), *f.split("/")[1:])
            else:
                path = os.path.join(ROOT, "licenses", f)       # shipped as it is
            sizes.append(os.path.getsize(path))
        assert sizes and max(sizes) > 200, "%s ships with no licence text" % e["name"]
    qt = next(e for e in out if e["name"] == "PySide6")
    assert qt["licence"].startswith("LGPL-3.0")
    assert "LGPL-3.0-only.txt" in qt["files"] and "GPL-3.0-only.txt" in qt["files"]
    assert not any("Commercial" in f for e in out for f in e["files"]), \
        "Qt's commercial licence shipped as if it were the one it is used under"


def test_what_a_build_collected_goes_by_the_package_that_installed_it():
    """A build carries packages nobody listed - yt-dlp's hook brings in
    PyInstaller's own - so a build's list is made from the files it
    collected. By file: another installed package has a "gui" too."""
    import importlib.metadata as md
    flask = md.distribution("Flask")
    init = next(f for f in flask.files if str(f).replace("\\", "/").endswith("flask/__init__.py"))
    got = {third_party._key(d.metadata["Name"]) for d in third_party.packages(
        [str(flask.locate_file(init)), os.path.join(ROOT, "gui", "__init__.py")])}
    assert "flask" in got
    assert got <= {"flask", "pyinstaller"}, "credited what is not shipped: %s" % got


def test_the_build_writes_it_from_what_it_collected_and_ships_it():
    with open(os.path.join(ROOT, "HyperFetch.spec"), encoding="utf-8") as f:
        spec = f.read()
    assert "third_party.write(" in spec and "a.pure + a.binaries + a.datas" in spec
    assert "a.datas += Tree(" in spec
    assert spec.index("third_party.write(") > spec.index("a = Analysis(")
    assert "'third_party'" in spec, "Credits imports it lazily: PyInstaller must be told"


def test_a_built_app_reads_the_list_its_build_wrote(tmp_path, monkeypatch):
    import sys
    folder = tmp_path / "licenses" / third_party.FOLDER
    folder.mkdir(parents=True)
    listed = [{"name": "X", "version": "1", "licence": "MIT", "url": "", "files": []}]
    (folder / third_party.CREDITS).write_text(json.dumps(listed), encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert third_party.credits() == listed

