"""The winget package: `winget install HyperFetch.HyperFetch`.

A package in Microsoft's community repository is three small YAML files for
each version, sent as a pull request to microsoft/winget-pkgs.
tools/winget_manifest.py writes them for a release from what the release
itself says (the installer's address and SHA-256); these tests keep what they
claim in step with what the installer really registers.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

SHA = "4dc9ec03ad623bba1471b55619e05050079b8ff9344a53ed178d2901ac2eabb2"


def _iss(name):
    text = open(os.path.join(ROOT, "installer.iss"), encoding="utf-8").read()
    m = re.search(r"^%s=(.*)$" % re.escape(name), text, re.M)
    assert m, f"installer.iss has no {name}"
    value = m.group(1).strip()
    for define in re.findall(r"\{#(\w+)\}", value):
        d = re.search(r'^\s*#define %s "([^"]*)"' % define, text, re.M)
        value = value.replace("{#%s}" % define, d.group(1))
    return value


def _files(version="2.8.0"):
    import winget_manifest
    return winget_manifest.manifests(version, SHA, "2026-10-07")


def _field(text, name):
    m = re.search(r"^%s: (.*)$" % re.escape(name), text, re.M)
    return m.group(1).strip().strip("'\"") if m else None


def test_three_files_named_as_the_repository_wants_them():
    assert sorted(_files()) == ["HyperFetch.HyperFetch.installer.yaml",
                                "HyperFetch.HyperFetch.locale.en-US.yaml",
                                "HyperFetch.HyperFetch.yaml"]


def test_each_says_which_package_version_and_kind_it_is():
    kinds = {"HyperFetch.HyperFetch.yaml": "version",
             "HyperFetch.HyperFetch.installer.yaml": "installer",
             "HyperFetch.HyperFetch.locale.en-US.yaml": "defaultLocale"}
    for name, text in _files("2.8.0").items():
        assert _field(text, "PackageIdentifier") == "HyperFetch.HyperFetch", name
        assert _field(text, "PackageVersion") == "2.8.0", name
        assert _field(text, "ManifestType") == kinds[name], name
        assert _field(text, "ManifestVersion") == "1.12.0", name
        assert text.startswith("# yaml-language-server: $schema="), name
        assert text.endswith("\n") and "\t" not in text, name


def test_the_installer_is_the_releases_own_file_and_checksum():
    text = _files("2.8.0")["HyperFetch.HyperFetch.installer.yaml"]
    assert ("InstallerUrl: https://github.com/tanumay-deb/HyperFetch/releases/download/"
            "v2.8.0/HyperFetch-2.8.0-setup.exe") in text
    assert "InstallerSha256: " + SHA.upper() in text
    assert _field(text, "InstallerType") == "inno"
    assert "Architecture: x64" in text
    assert _field(text, "ReleaseDate") == "2026-10-07"


def test_it_names_the_program_the_way_windows_lists_it():
    """winget matches a package to what is installed by the entry under
    Apps > Installed apps. Where the two disagree, an installed HyperFetch is
    not recognised, and `winget upgrade` offers nothing or installs twice."""
    files = _files()
    installer = files["HyperFetch.HyperFetch.installer.yaml"]
    locale = files["HyperFetch.HyperFetch.locale.en-US.yaml"]
    app_id = _iss("AppId").replace("{{", "{")
    assert "ProductCode: '%s_is1'" % app_id in installer
    assert _field(locale, "PackageName") == _iss("UninstallDisplayName") == "HyperFetch"
    assert _field(locale, "Publisher") == _iss("AppPublisher")
    assert "DisplayName: " + _iss("UninstallDisplayName") in installer


def test_it_says_what_the_program_is_and_where_it_comes_from():
    locale = _files()["HyperFetch.HyperFetch.locale.en-US.yaml"]
    assert _field(locale, "License") == "MIT"
    assert _field(locale, "PackageUrl") == "https://tanumay-deb.github.io/HyperFetch/"
    assert _field(locale, "PrivacyUrl") == "https://tanumay-deb.github.io/HyperFetch/privacy.html"
    short = _field(locale, "ShortDescription")
    assert short and len(short) <= 256
    assert _field(locale, "ReleaseNotesUrl").endswith("/releases/tag/v2.8.0")


def test_a_checksum_that_is_not_one_is_refused():
    import pytest
    import winget_manifest
    for bad in ("", "sha256:" + SHA, SHA[:-1], "z" * 64):
        with pytest.raises(ValueError):
            winget_manifest.manifests("2.8.0", bad, "2026-10-07")


# ---- the installer says what it is, like the exe it carries ----------------------
def test_the_installer_carries_its_own_name_maker_and_version():
    """The setup exe is the first file a download scanner sees, and it said
    nothing about itself: no company, no product, version 0.0.0.0."""
    assert _iss("VersionInfoVersion") == _iss("AppVersion")
    assert _iss("VersionInfoCompany") == "HyperFetch"
    assert _iss("VersionInfoProductName") == "HyperFetch"
    assert "Tanumay Goswami" in _iss("VersionInfoCopyright")
    assert _iss("AppPublisherURL").startswith("https://")
