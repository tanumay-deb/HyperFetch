# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for HyperFetch (onedir, windowed)."""
import os
import re
import sys
import tempfile
from PyInstaller.utils.hooks import collect_all, collect_submodules

# What the exe says about itself (name, maker, version): tools/version_resource.py
sys.path.insert(0, os.path.join(SPECPATH, 'tools'))
import version_resource
with open(os.path.join(SPECPATH, 'utils.py'), encoding='utf-8') as _f:
    _version = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', _f.read(), re.M).group(1)
version_info = os.path.join(tempfile.mkdtemp(), 'version_info.txt')
with open(version_info, 'w', encoding='utf-8') as _f:
    _f.write(version_resource.version_file(_version))

# cryptography is imported lazily inside hls.py -> pull it in explicitly
crypto_datas, crypto_binaries, crypto_hidden = collect_all('cryptography')

# yt-dlp is imported lazily inside yt_dl.py -> pull it + its extractors in
try:
    ytdlp_datas, ytdlp_binaries, ytdlp_hidden = collect_all('yt_dlp')
except Exception:
    ytdlp_datas, ytdlp_binaries, ytdlp_hidden = [], [], []
# its own PyInstaller hook is for building, not running (excludes, below)
ytdlp_hidden = [m for m in ytdlp_hidden if not m.startswith('yt_dlp.__pyinstaller')]
# YouTube's challenge solver: yt-dlp takes its scripts from this package, and
# without it a signed-in YouTube request gets no video formats (2026-11-12).
# Imported here so a build without it stops instead of shipping that.
import yt_dlp_ejs  # noqa: E402,F401
ejs_datas, ejs_binaries, ejs_hidden = collect_all('yt_dlp_ejs')

hidden = (
    # local modules reached via lazy `import` inside functions
    ['hls', 'downloader', 'queue_manager', 'api_server', 'task', 'utils',
     'crash_reporter', 'updater', 'torrent', 'aria2d', 'yt_dl', 'doh', 'unblock', 'upnp',
     'history', 'trackers', 'scrape', 'swarm', 'third_party',
     'gui2.dialogs.history', 'gui2.dialogs.queues', 'gui2.dialogs.console',
     'gui2.dialogs.host_rules', 'gui2.dialogs.welcome']
    + crypto_hidden + ytdlp_hidden + ejs_hidden
    + collect_submodules('flask_cors')
)

# Bundle the aria2c sidecar (BitTorrent/magnet engine) IF present. Drop the
# official aria2c.exe into bin/ before building; torrent.aria2c_path() looks
# there first. Absent -> the build still works, torrents just report
# "aria2c not found" until the binary ships.
extra_datas = []
# The web UI's files. Lazy-loaded from disk at request time, so PyInstaller
# cannot infer them — without this the frozen build serves a 404 for /ui.
if os.path.isdir('web'):
    extra_datas.append(('web', 'web'))
if os.path.isfile(os.path.join('bin', 'aria2c.exe')):
    extra_datas.append(('bin/aria2c.exe', 'bin'))
elif os.path.isfile(os.path.join('bin', 'aria2c')):
    extra_datas.append(('bin/aria2c', 'bin'))
# ffmpeg for yt-dlp merges (1080p/4K + DASH-only videos); optional
if os.path.isfile(os.path.join('bin', 'ffmpeg.exe')):
    extra_datas.append(('bin/ffmpeg.exe', 'bin'))
    # Which FFmpeg this is - build.ps1 fetches the latest - goes beside the GPL
    # notice (licenses/THIRD-PARTY-NOTICES.txt): its commit is where its source is.
    import subprocess
    _ver = subprocess.run([os.path.join('bin', 'ffmpeg.exe'), '-hide_banner', '-version'],
                          capture_output=True, text=True, timeout=60).stdout.splitlines()
    os.makedirs('build', exist_ok=True)
    with open(os.path.join('build', 'FFmpeg-version.txt'), 'w', encoding='utf-8') as _f:
        print(_ver[0] if _ver else 'unknown', file=_f)
    extra_datas.append((os.path.join('build', 'FFmpeg-version.txt'), 'licenses'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=crypto_binaries + ytdlp_binaries + ejs_binaries,
    datas=[('assets/icon.ico', 'assets'), ('assets/icon.png', 'assets'),
           ('assets/icons', 'assets/icons'),
           ('LICENSE', '.'),                                # About -> License
           ('licenses', 'licenses'),     # the GPL programs' licences + where their source is
           ('notes', 'notes'),           # each version's notes: the What's new window
           ] + extra_datas + crypto_datas + ytdlp_datas + ejs_datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'PySide6.QtWebEngineCore',
              'PySide6.Qt3DCore', 'PySide6.QtMultimedia', 'PySide6.QtQuick',
              'PySide6.QtBluetooth', 'PySide6.QtPdf',
              # collect_all('yt_dlp') takes yt-dlp's own PyInstaller hook, which
              # imports PyInstaller: every build shipped PyInstaller's code (GPLv2)
              # and the tools it uses, 2.8 MB nothing runs (2026-11-12)
              'yt_dlp.__pyinstaller', 'PyInstaller'],
    noarchive=False,
)

# Every package the build ships - those of the files Analysis collected - with
# its version, licence and licence texts, as licenses/third-party/
# (third_party.py; Settings -> About -> Credits reads its credits.json)
sys.path.insert(0, SPECPATH)
import third_party
third_party.write(os.path.join('build', third_party.FOLDER),
                  files=[src for _, src, _ in a.pure + a.binaries + a.datas])
a.datas += Tree(os.path.join('build', third_party.FOLDER),
                prefix=os.path.join('licenses', third_party.FOLDER))
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HyperFetch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # windowed GUI app
    icon='assets/icon.ico',
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='HyperFetch',
)
