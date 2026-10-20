# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# uvicorn 的协议/生命周期实现是动态导入的，必须显式收集
hidden = collect_submodules("uvicorn") + [
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "anyio._backends._asyncio",
]

a = Analysis(
    [os.path.join(ROOT, "src", "dra", "desktop.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=[(os.path.join(ROOT, "src", "dra", "web", "static"), "dra/web/static")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DRA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DRA",
)
