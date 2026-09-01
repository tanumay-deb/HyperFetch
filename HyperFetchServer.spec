# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for HyperFetch Server — the engine and the users site, no UI.

Same source tree as HyperFetch.spec, different entry point and a hard exclusion
of Qt. That exclusion is the whole point: PySide6 is ~92 MB of a ~300 MB build
and a machine with no display needs none of it.

If Qt ever creeps back into the engine this build still succeeds and then fails
at runtime, which is the worst way to find out — so tests/test_server_headless
asserts that neither server.py nor queue_manager imports it.
"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# cryptography is imported lazily inside hls.py
crypto_datas, crypto_binaries, crypto_hidden = collect_all('cryptography')

try:
    ytdlp_datas, ytdlp_binaries, ytdlp_hidden = collect_all('yt_dlp')
except Exception:
    ytdlp_datas, ytdlp_binaries, ytdlp_hidden = [], [], []

hidden = (
    # Reached via lazy `import` inside functions, so PyInstaller cannot see them.
    ['hls', 'downloader', 'queue_manager', 'api_server', 'task', 'utils',
     'torrent', 'aria2d', 'yt_dl', 'doh', 'upnp', 'history', 'crash_reporter',
     'web_auth', 'site_server', 'site_auth', 'site_limits', 'site_audit',
     'site_tunnel', 'waitress']
    + crypto_hidden + ytdlp_hidden
    + collect_submodules('flask_cors')
    + collect_submodules('waitress')
)

extra_datas = []
# The built users site. Without it the server answers a holding page, which is
# a working server showing the wrong thing — so CI asserts it is present.
if os.path.isdir('site'):
    extra_datas.append(('site', 'site'))
# The control page, for the browser extension on the same machine.
if os.path.isdir('web'):
    extra_datas.append(('web', 'web'))

# aria2c is not optional here: a server that cannot do torrents is not the
# thing anybody installed it for.
if os.path.isfile(os.path.join('bin', 'aria2c.exe')):
    extra_datas.append(('bin/aria2c.exe', 'bin'))
elif os.path.isfile(os.path.join('bin', 'aria2c')):
    extra_datas.append(('bin/aria2c', 'bin'))

# ffmpeg is 138 MB and only needed to merge yt-dlp streams. Set
# HYPERFETCH_SERVER_FFMPEG=1 to include it; most servers do not want it, and
# on Linux it is a package manager away.
if os.environ.get('HYPERFETCH_SERVER_FFMPEG') == '1':
    for name in ('ffmpeg.exe', 'ffmpeg'):
        p = os.path.join('bin', name)
        if os.path.isfile(p):
            extra_datas.append((p.replace('\\', '/'), 'bin'))
            break

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=crypto_binaries + ytdlp_binaries,
    datas=extra_datas + crypto_datas + ytdlp_datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # The reason this build exists. Everything Qt, and the two GUI packages
        # that import it — listing the packages as well as the library means a
        # stray `import gui2` fails the build rather than dragging Qt back in.
        'PySide6', 'shiboken6', 'PyQt5', 'PyQt6',
        'gui', 'gui2',
        'tkinter', 'pytest',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HyperFetchServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # a service: its log is the console
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='HyperFetchServer',
)
