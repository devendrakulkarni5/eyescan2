# -*- mode: python ; coding: utf-8 -*-
# Single source of truth for the build:  pyinstaller --noconfirm --clean Keeper.spec
# (build_exe.bat does this and then smoke-tests the result.)
from PyInstaller.utils.hooks import collect_all

datas = [('engine/model', 'model'), ('ui', 'ui'), ('LICENSE.txt', '.')]
binaries = []
hiddenimports = ['cli', 'bridge']
for pkg in ('mediapipe', 'onnxruntime', 'webview', 'send2trash', 'rawpy'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# NOTE: do NOT exclude matplotlib / sounddevice / cv2 - mediapipe imports them at load time.
EXCLUDES = ['scipy', 'pandas', 'IPython', 'jupyter', 'notebook', 'pytest', 'sphinx',
            'tensorflow', 'torch', 'tkinter', 'ttkbootstrap']

a = Analysis(
    ['app.py'],
    pathex=['engine'],          # eyescan_core, eyescan_db, ... are plain top-level modules
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Keeper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can corrupt mediapipe/OpenCV DLLs and trips AV
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/Keeper.ico',
    version='assets/version_info.txt',
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name='Keeper')
