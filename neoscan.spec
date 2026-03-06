# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for NeoSCAN.

Build instructions:
  pip install pyinstaller
  pyinstaller neoscan.spec

Output will be in dist/NeoSCAN (directory mode) or dist/NeoSCAN.app (macOS).
"""

from pathlib import Path
import sys

ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "resources"), "resources"),
    ],
    hiddenimports=[
        "serial",
        "serial.tools",
        "serial.tools.list_ports",
        "PyQt6.QtSvg",
        "PyQt6.sip",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

if sys.platform == "darwin":
    # macOS: build as an .app bundle
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="NeoSCAN",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        icon=str(ROOT / "resources" / "icons" / "neoscan.png"),
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="NeoSCAN",
    )
    app = BUNDLE(
        coll,
        name="NeoSCAN.app",
        icon=str(ROOT / "resources" / "icons" / "neoscan.png"),
        bundle_identifier="com.neoscan.NeoSCAN",
        info_plist={
            "CFBundleName": "NeoSCAN",
            "CFBundleDisplayName": "NeoSCAN",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.15",
        },
    )

elif sys.platform == "win32":
    # Windows: single-file executable
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="NeoSCAN",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        icon=str(ROOT / "resources" / "icons" / "neoscan.png"),
    )

else:
    # Linux: directory bundle
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="neoscan",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="NeoSCAN",
    )
