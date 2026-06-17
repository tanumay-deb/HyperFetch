# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for HyperFetch (onedir, windowed)."""
from PyInstaller.utils.hooks import collect_all, collect_submodules

# cryptography is imported lazily inside hls.py -> pull it in explicitly
crypto_datas, crypto_binaries, crypto_hidden = collect_all('cryptography')

hidden = (
    # local modules reached via lazy `import` inside functions
    ['hls', 'downloader', 'queue_manager', 'api_server', 'task', 'utils',
     'crash_reporter', 'updater']
    + crypto_hidden
    + collect_submodules('flask_cors')
)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=crypto_binaries,
    datas=[('assets/icon.ico', 'assets'), ('assets/icon.png', 'assets'),
           ('assets/icons', 'assets/icons')] + crypto_datas,
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
