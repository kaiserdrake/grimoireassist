# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build recipe for the portable one-folder distribution.
# Build with build.bat (or: pyinstaller GrimoireAssist.spec --noconfirm).
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("grimoireassist/data/icon.ico", "grimoireassist/data")]
# easyocr loads character sets / model configs from its package dir at runtime,
# and picks recognizer modules by name via importlib — neither is visible to
# static analysis, so collect both explicitly.
datas += collect_data_files("easyocr")
hiddenimports = collect_submodules("easyocr") + [
    "pygrabber",
    "pygrabber.dshow_graph",
]

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GrimoireAssist",
    icon="grimoireassist/data/icon.ico",
    console=False,  # windowless, like the pythonw launch in run.bat
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GrimoireAssist",
)
