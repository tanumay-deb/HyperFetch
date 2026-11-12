"""YouTube's challenge solver ships with HyperFetch (yt-dlp-ejs).

A YouTube request that carries the browser's sign-in gets formats whose links
need the "n challenge" solved, by running YouTube's own script. yt-dlp does it
with a JavaScript runtime (deno, the one it enables by default) and the solver
scripts of the yt-dlp-ejs package. Its built-in copy of the lib script for deno
imports npm packages, which deno fetches only when told to (yt-dlp's remote
components, off by default), so without the package no solver loads: every
format dropped out and the download failed. User's log, 2026-11-12: "Remote
components challenge solver script (deno) and NPM package (deno) were
skipped". Measured with YouTube's player script and deno 2.9.6: not solved
without the package, solved with 0.8.0, nothing fetched.
"""
import os
import re
import shutil
import urllib.request

import pytest

import yt_dl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def _pin(text, name):
    m = re.search(r'(?m)^\s*"?%s==([0-9][^"\s,]*)' % re.escape(name), text)
    return m.group(1) if m else None


def _minor(version):
    return tuple(int(x) for x in version.split(".")[:2])


def test_requirements_ship_the_solver_this_yt_dlp_takes():
    from yt_dlp.extractor.youtube.jsc._builtin.vendor import VERSION
    pin = _pin(_read("requirements.txt"), "yt-dlp-ejs")
    assert pin, "requirements.txt does not ship yt-dlp-ejs"
    # yt-dlp refuses a script whose major.minor differs from its own (ejs.py)
    assert _minor(pin) == _minor(VERSION), (pin, VERSION)


def test_a_pip_install_gets_the_same_yt_dlp_and_solver():
    """pyproject.toml pinned yt-dlp 2026.6.9 while the build ran 2026.8.19: a
    solver of one yt-dlp's version is refused by another's."""
    req, proj = _read("requirements.txt"), _read("pyproject.toml")
    for name in ("yt-dlp", "yt-dlp-ejs"):
        assert _pin(proj, name) == _pin(req, name), name


def test_the_build_collects_the_solver():
    spec = _read("HyperFetch.spec")
    assert "collect_all('yt_dlp_ejs')" in spec


def test_yt_dlp_takes_the_solver_from_the_package():
    """Both scripts from the package: no remote component, no npm."""
    import yt_dlp
    from yt_dlp.extractor.youtube.jsc._builtin.deno import DenoJCP
    from yt_dlp.extractor.youtube.jsc._builtin.ejs import ScriptSource, ScriptType
    from yt_dlp.extractor.youtube.jsc._director import initialize_jsc_director
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                           "logger": yt_dl._YtLog()}) as ydl:
        director = initialize_jsc_director(ydl.get_info_extractor("Youtube"))
        deno = next(p for p in director.providers.values() if isinstance(p, DenoJCP))
        for kind in (ScriptType.LIB, ScriptType.CORE):
            assert deno._get_script(kind).source is ScriptSource.PYPACKAGE, kind


@pytest.mark.network
@pytest.mark.skipif(not shutil.which("deno"), reason="needs deno on PATH")
def test_a_real_youtube_challenge_is_solved():
    """YouTube's current player script, from its own address (a static file:
    no video page, no video, no sign-in), solved by deno."""
    import yt_dlp
    from yt_dlp.extractor.youtube.jsc._director import initialize_jsc_director
    from yt_dlp.extractor.youtube.jsc.provider import (
        JsChallengeRequest, JsChallengeType, NChallengeInput)
    api = urllib.request.urlopen("https://www.youtube.com/iframe_api", timeout=20)
    pid = re.search(r"/s/player/([0-9a-zA-Z_-]+)/",
                    api.read().decode().replace("\\/", "/")).group(1)
    player = "https://www.youtube.com/s/player/%s/player_ias.vflset/en_US/base.js" % pid
    challenge = "aBcDeFgHiJkLmNoP"
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                           "logger": yt_dl._YtLog()}) as ydl:
        director = initialize_jsc_director(ydl.get_info_extractor("Youtube"))
        out = director.bulk_solve([JsChallengeRequest(
            JsChallengeType.N, NChallengeInput(player, [challenge]), "probe")])
    solved = {k: v for _, resp in out for k, v in resp.output.results.items()}
    assert solved.get(challenge) and solved[challenge] != challenge, solved
