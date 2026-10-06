"""Write the winget manifest for a released version of HyperFetch.

    python tools/winget_manifest.py 2.8.0 [--out DIR]

A winget package is three YAML files a version, sent as a pull request to
microsoft/winget-pkgs under manifests/h/HyperFetch/HyperFetch/<version>/. This
writes them from what the release itself says - the installer's address, its
SHA-256 and the day it was published, all read from GitHub - so nothing is
typed by hand. Check them with `winget validate --manifest DIR` before sending.

What they claim has to be what the installer registers: the name and publisher
under Apps > Installed apps, and Inno Setup's product code (AppId + "_is1").
tests/test_winget_manifest.py holds the two together.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

REPO = "tanumay-deb/HyperFetch"
IDENTIFIER = "HyperFetch.HyperFetch"
SCHEMA = "1.12.0"
SITE = "https://tanumay-deb.github.io/HyperFetch/"
# installer.iss: AppId, with Inno Setup's "_is1" on the end
PRODUCT_CODE = "{8F3C1A92-5D44-4E27-9C61-2B7A0E5F1D33}_is1"


def _head(kind):
    return ("# yaml-language-server: $schema=https://aka.ms/winget-manifest.%s.%s.schema.json\n\n"
            % (kind, SCHEMA))


def _tail(kind):
    return "ManifestType: %s\nManifestVersion: %s\n" % (kind, SCHEMA)


def manifests(version, sha256, released):
    """{file name: text} for one version. `sha256` is the installer's, in hex;
    `released` the day it was published, YYYY-MM-DD."""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256 or ""):
        raise ValueError("not a SHA-256 in hex: %r" % (sha256,))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", released or ""):
        raise ValueError("not a date, YYYY-MM-DD: %r" % (released,))
    same = "PackageIdentifier: %s\nPackageVersion: %s\n" % (IDENTIFIER, version)
    release = "https://github.com/%s/releases" % REPO

    version_file = _head("version") + same + "DefaultLocale: en-US\n" + _tail("version")

    installer = _head("installer") + same + (
        "Platform:\n"
        "- Windows.Desktop\n"
        "MinimumOSVersion: 10.0.17763.0\n"
        "InstallerType: inno\n"
        "Scope: machine\n"
        "InstallModes:\n"
        "- interactive\n"
        "- silent\n"
        "- silentWithProgress\n"
        "UpgradeBehavior: install\n"
        "ElevationRequirement: elevatesSelf\n"
        "ProductCode: '%(code)s'\n"
        "ReleaseDate: %(released)s\n"
        "AppsAndFeaturesEntries:\n"
        "- DisplayName: HyperFetch\n"
        "  Publisher: HyperFetch\n"
        "  ProductCode: '%(code)s'\n"
        "Installers:\n"
        "- Architecture: x64\n"
        "  InstallerUrl: %(release)s/download/v%(version)s/HyperFetch-%(version)s-setup.exe\n"
        "  InstallerSha256: %(sha)s\n"
    ) % {"code": PRODUCT_CODE, "released": released, "release": release,
         "version": version, "sha": sha256.upper()} + _tail("installer")

    locale = _head("defaultLocale") + same + (
        "PackageLocale: en-US\n"
        "Publisher: HyperFetch\n"
        "PublisherUrl: %(site)s\n"
        "PublisherSupportUrl: https://github.com/%(repo)s/issues\n"
        "PrivacyUrl: %(site)sprivacy.html\n"
        "Author: Tanumay Goswami\n"
        "PackageName: HyperFetch\n"
        "PackageUrl: %(site)s\n"
        "License: MIT\n"
        "LicenseUrl: https://github.com/%(repo)s/blob/main/LICENSE\n"
        "Copyright: Copyright (c) 2026 Tanumay Goswami\n"
        "ShortDescription: A download manager that fetches each file over many connections"
        " at once, with streaming video and torrents built in.\n"
        "Description: |-\n"
        "  HyperFetch splits a download across many connections, resumes where it stopped,\n"
        "  saves HLS streams and videos from web pages, and downloads torrents and magnets\n"
        "  without a second program. A browser extension for Chrome, Edge and Firefox hands\n"
        "  downloads to it. Free and open source; everything runs on this computer.\n"
        "Moniker: hyperfetch\n"
        "Tags:\n"
        "- download-manager\n"
        "- downloader\n"
        "- hls\n"
        "- m3u8\n"
        "- magnet\n"
        "- torrent\n"
        "- video-downloader\n"
        "ReleaseNotesUrl: %(release)s/tag/v%(version)s\n"
    ) % {"site": SITE, "repo": REPO, "release": release, "version": version} + _tail("defaultLocale")

    return {IDENTIFIER + ".yaml": version_file,
            IDENTIFIER + ".installer.yaml": installer,
            IDENTIFIER + ".locale.en-US.yaml": locale}


def release_facts(version):
    """(sha256, day published) of a release's installer, as GitHub reports them."""
    url = "https://api.github.com/repos/%s/releases/tags/v%s" % (REPO, version)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "HyperFetch-winget"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    want = "HyperFetch-%s-setup.exe" % version
    for asset in data.get("assets", []):
        if asset.get("name") == want:
            digest = asset.get("digest") or ""
            if not digest.startswith("sha256:"):
                raise SystemExit("GitHub gives no SHA-256 for %s" % want)
            return digest.split(":", 1)[1], (data.get("published_at") or "")[:10]
    raise SystemExit("release v%s has no %s" % (version, want))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version", help="a released version, e.g. 2.8.0")
    ap.add_argument("--out", help="folder to write into (default: "
                                  "manifests/h/HyperFetch/HyperFetch/<version>)")
    a = ap.parse_args(argv)
    sha, day = release_facts(a.version)
    out = a.out or os.path.join("manifests", "h", "HyperFetch", "HyperFetch", a.version)
    os.makedirs(out, exist_ok=True)
    for name, text in manifests(a.version, sha, day).items():
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    print("wrote 3 files to", out)
    print("check them:  winget validate --manifest", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
