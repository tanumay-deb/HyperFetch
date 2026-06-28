# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for HyperFetch (onedir, windowed)."""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# cryptography is imported lazily inside hls.py -> pull it in explicitly
crypto_datas, crypto_binaries, crypto_hidden = collect_all('cryptography')

# yt-dlp is imported lazily inside yt_dl.py -> pull it + its extractors in
try:
    ytdlp_datas, ytdlp_binaries, ytdlp_hidden = collect_all('yt_dlp')
except Exception:
    ytdlp_datas, ytdlp_binaries, ytdlp_hidden = [], [], []

hidden = (
    # local modules reached via lazy `import` inside functions
    ['hls', 'downloader', 'queue_manager', 'api_server', 'task', 'utils',
     'crash_reporter', 'updater', 'torrent', 'yt_dl', 'doh', 'upnp', 'history',
     'gui2.dialogs.history', 'gui2.dialogs.queues', 'gui2.dialogs.console',
     'gui2.dialogs.host_rules']
    + crypto_hidden + ytdlp_hidden
    + collect_submodules('flask_cors')
)

# Bundle the aria2c sidecar (BitTorrent/magnet engine) IF present. Drop the
# official aria2c.exe into bin/ before building; torrent.aria2c_path() looks
# there first. Absent -> the build still works, torrents just report
# "aria2c not found" until the binary ships.
extra_datas = []
if os.path.isfile(os.path.join('bin', 'aria2c.exe')):
    extra_datas.append(('bin/aria2c.exe', 'bin'))
elif os.path.isfile(os.path.join('bin', 'aria2c')):
    extra_datas.append(('bin/aria2c', 'bin'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=crypto_binaries + ytdlp_binaries,
    datas=[('assets/icon.ico', 'assets'), ('assets/icon.png', 'assets'),
           ('assets/icons', 'assets/icons')] + extra_datas + crypto_datas + ytdlp_datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'PySide6.QtWebEngineCore',
              'PySide6.Qt3DCore', 'PySide6.QtMultimedia', 'PySide6.QtQuick',
              'PySide6.QtBluetooth', 'PySide6.QtPdf'],
    noarchive=False,
)
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='HyperFetch',
)
