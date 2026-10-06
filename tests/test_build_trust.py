"""What makes the shipped HyperFetch.exe look like what it is.

Microsoft Defender quarantined 2.7.1 while it was being installed
("Trojan:Win32/Bearfoos.A!ml", 2026-10-06). `!ml` is a machine-learning guess,
not a match with known malware, and what could be checked said the file was the
honest build:

  * the installed exe was byte for byte the one GitHub built from the tag;
  * 2.7.0 (not flagged) and 2.7.1 were built on the same runner image with the
    same Python, PyInstaller and libraries - only this app's own code differed;
  * Defender on a clean machine, real-time and cloud protection on, scanned
    that same exe as clean.

An unsigned exe that says nothing about itself, started by the stock
PyInstaller bootloader that a great deal of real malware is packed with, is
what such a model distrusts. These are the parts of that which cost nothing.
"""
import os
import re
import sys

import utils

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ---- the exe says what it is ---------------------------------------------------
def test_the_exe_carries_its_name_maker_and_version():
    import version_resource
    text = version_resource.version_file(utils.APP_VERSION)
    for field in ("CompanyName", "FileDescription", "ProductName", "LegalCopyright",
                  "OriginalFilename", "FileVersion", "ProductVersion"):
        assert "StringStruct('%s'" % field in text, f"{field} is missing"
    assert "StringStruct('ProductVersion', '%s')" % utils.APP_VERSION in text
    assert "StringStruct('OriginalFilename', 'HyperFetch.exe')" in text


def test_the_numbers_in_it_are_the_apps_version():
    import version_resource
    text = version_resource.version_file("3.14.2")
    assert "filevers=(3, 14, 2, 0)" in text and "prodvers=(3, 14, 2, 0)" in text


def test_a_version_that_is_not_three_numbers_still_builds():
    import version_resource
    assert "filevers=(2, 8, 0, 0)" in version_resource.version_file("2.8")
    assert "filevers=(2, 8, 1, 0)" in version_resource.version_file("2.8.1-rc1")


def test_the_spec_puts_it_in_the_exe():
    spec = _read("HyperFetch.spec")
    assert "version_resource" in spec
    exe = spec[spec.index("exe = EXE("):spec.index("coll = COLLECT(")]
    assert re.search(r"^\s*version=", exe, re.M), "EXE() is given no version resource"


# ---- the tools that build it are named, and the bootloader is ours --------------
def test_the_build_tools_are_pinned():
    """An unpinned `pip install pyinstaller` builds each release with whatever
    is newest that day: a different bootloader without anyone having chosen it."""
    lines = [ln.strip() for ln in _read("requirements-build.txt").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    names = {re.split(r"[=<>~! ]", ln, 1)[0].lower() for ln in lines}
    assert {"pyinstaller", "pillow"} <= names
    for ln in lines:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][0-9A-Za-z.]*", ln), f"not pinned: {ln}"


def test_both_builds_install_them_and_compile_the_bootloader():
    for wf in ("ci.yml", "release.yml"):
        text = _read(".github", "workflows", wf)
        assert "requirements-build.txt" in text, f"{wf} does not use the pinned tools"
        assert "--no-binary pyinstaller" in text, (
            f"{wf} uses the prebuilt bootloader every PyInstaller app shares")
        assert not re.search(r"pip install[^\n]*\bpyinstaller pillow\b", text), (
            f"{wf} still installs whatever PyInstaller is newest")


# ---- nothing is published that Defender already knows -----------------------------
def test_a_release_is_scanned_before_it_is_attached():
    """It would not have stopped 2.7.1 - that was Defender's guess on one PC,
    and the same exe scans clean - but a bundled tool or library that has
    turned up on Defender's list should never reach a release. Tried in CI on
    the real build (passes) and on the standard antivirus test file (refused)."""
    text = _read(".github", "workflows", "release.yml")
    scan = text.index("defender_scan.ps1")
    assert text.index("Build app + installer") < scan < text.index("Attach to the GitHub Release")
    step = text[scan:text.index("\n", scan)]
    assert "dist/HyperFetch" in step and "dist/installer" in step, step


def test_the_scan_takes_the_runners_exclusions_off_first():
    """GitHub's runners exclude C:\\ and D:\\ from Defender: with those in
    place a scan reads nothing, and passes."""
    script = _read("tools", "defender_scan.ps1")
    assert script.index("Remove-MpPreference -ExclusionPath") < script.index("-Scan")
    assert "exit 1" in script

