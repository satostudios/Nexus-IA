# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for NEXUS.
#   pyinstaller nexus.spec                  -> dist/NEXUS.exe   (single file)
#   set NEXUS_ONEDIR=1 & pyinstaller ...    -> dist/NEXUS/NEXUS.exe (faster start)
#
# Model files are NOT embedded: Needle weights/engine, Whisper and Piper voices
# are fetched once and cached (see README, "Model management").

import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ONEDIR = os.environ.get("NEXUS_ONEDIR") == "1"

datas = [
    ("assets/shaders", "assets/shaders"),
    ("assets/fonts", "assets/fonts"),
    ("assets/config", "assets/config"),
    ("assets/icon", "assets/icon"),
]
datas += collect_data_files("piper")            # espeak-ng-data, phoneme tables
datas += collect_data_files("faster_whisper")   # VAD / tokenizer assets

binaries = []
binaries += collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("onnxruntime")
binaries += collect_dynamic_libs("piper")

hiddenimports = [
    "needle", "needle.agent.fetch", "needle.agent.tools", "needle._worker", "needle._telemetry",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "win32com.client", "pythoncom", "win32gui", "win32con", "win32process", "win32job", "win32api", "psutil",
    "send2trash", "mss", "mss.tools",
]
hiddenimports += collect_submodules("app")

excludes = [
    "tkinter", "matplotlib", "jax", "jaxlib", "flax", "optax", "torch", "tensorflow", "IPython",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick3D",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtDesigner", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "needle.playground", "needle.environments", "needle.model", "needle.cli", "needle.platform",
    "pytest", "pyflakes",
]

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

common = dict(
    name="NEXUS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon/nexus.ico",
)

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="NEXUS")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common)
