# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

APP_NAME = "Razer Opus X Control"
ENTRY_SCRIPT = "main.py"

datas = [("razer.ico", ".")]
binaries = []
hiddenimports = []

for package in ("bleak", "pystray", "PIL", "winrt"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

hiddenimports += ["bleak.backends.winrt"]
hiddenimports = sorted(set(hiddenimports))

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="razer.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
