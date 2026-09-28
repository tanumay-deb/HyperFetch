"""The browser extensions are one extension: edge_ext and firefox_ext carry
chrome_ext's code byte for byte.

chrome_ext is the copy CI syntax-checks and the jsdom suite tests, so a copy
that fell behind would ship untested code under the same version number.
Firefox differs only in its manifest: the background runs as scripts (Firefox
has no service-worker background), and Mozilla requires the gecko settings.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = ["background.js", "content.js", "popup.html", "popup.js",
           "welcome.html", "welcome.js",
           "icons/icon16.png", "icons/icon48.png", "icons/icon128.png"]


def _read(browser, name):
    with open(os.path.join(ROOT, browser + "_ext", name), "rb") as f:
        return f.read()


def _manifest(browser):
    return json.loads(_read(browser, "manifest.json").decode("utf-8"))


def test_edge_and_firefox_run_chromes_code():
    for browser in ("edge", "firefox"):
        for name in RUNTIME:
            assert _read(browser, name) == _read("chrome", name), \
                "%s_ext/%s differs from chrome_ext - copy it over" % (browser, name)


def test_edge_manifest_is_chromes():
    assert _read("edge", "manifest.json") == _read("chrome", "manifest.json")


def test_firefox_manifest_differs_only_where_firefox_needs_it():
    chrome, firefox = _manifest("chrome"), _manifest("firefox")
    assert firefox["background"] == {"scripts": ["background.js"]}
    gecko = firefox["browser_specific_settings"]["gecko"]
    # Fixed for good once the add-on is signed: a new id is a new add-on, and
    # everyone who installed the old one stops getting updates.
    assert gecko["id"] == "hyperfetch@tanumay-deb.github.io"
    # What the extension hands to the desktop app - link and page addresses,
    # cookies, the downloads people start. Mozilla counts data given to a local
    # app, so "none" would be a false declaration.
    assert gecko["data_collection_permissions"] == {
        "required": ["browsingActivity", "websiteContent", "websiteActivity"]}
    assert gecko["strict_min_version"] == "140.0"   # first with data_collection_permissions
    shared = lambda m: {k: v for k, v in m.items()
                        if k not in ("background", "browser_specific_settings")}
    assert shared(firefox) == shared(chrome)
