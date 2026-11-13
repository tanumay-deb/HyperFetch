"""The extension's page script runs in every frame, in all three browsers.

A video playing inside an embedded player - a frame from another site, as a
"Server 2" picker loads - got no Download button: the page script ran in the
top frame only (reported in Firefox, 2026-11-13). Since 1.9.6 it runs in every
frame, and the worker sends each frame only the streams it asked for
(chrome_ext/test/sniff-facts.test.js, pick-stream.test.js).
"""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROWSERS = ("chrome_ext", "edge_ext", "firefox_ext")


def _manifest(folder):
    with open(os.path.join(ROOT, folder, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("folder", BROWSERS)
def test_the_page_script_runs_in_embedded_frames(folder):
    scripts = _manifest(folder)["content_scripts"]
    assert all(s.get("all_frames") is True for s in scripts if "content.js" in s["js"])


@pytest.mark.parametrize("folder", BROWSERS)
def test_the_page_script_listens_before_the_page_loads(folder):
    """At document_idle an embedded player that fetches its playlist while its
    page loads had its stream sent before the frame's script listened, and
    Chrome dropped it: no button (measured in Edge 157, 2 of 2)."""
    scripts = _manifest(folder)["content_scripts"]
    assert all(s.get("run_at") == "document_start" for s in scripts if "content.js" in s["js"])


def test_the_three_browsers_carry_one_version():
    versions = {f: _manifest(f)["version"] for f in BROWSERS}
    assert len(set(versions.values())) == 1, versions
