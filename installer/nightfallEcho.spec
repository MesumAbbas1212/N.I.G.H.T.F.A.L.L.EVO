# -*- mode: python ; coding: utf-8 -*-
import os
cwd = 'D:\\TiTech Prabha Solution\\NIGHTFALL AI\\NIGHTFALL AI\\NIGHTFALL-AI---Lite-main\\NIGHTFALL-AI---Lite-main'

a = Analysis(
    ['D:\\TiTech Prabha Solution\\NIGHTFALL AI\\NIGHTFALL AI\\NIGHTFALL-AI---Lite-main\\NIGHTFALL-AI---Lite-main\\main.py'],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(cwd, 'assets'), 'assets'),
        (os.path.join(cwd, 'config'), 'config'),
        (os.path.join(cwd, 'core'), 'core'),
        (os.path.join(cwd, 'NIGHTFALL_connect'), 'NIGHTFALL_connect'),
        (os.path.join(cwd, 'actions'), 'actions'),
        (os.path.join(cwd, 'dashboard/static'), 'dashboard/static'),
        (os.path.join(cwd, 'smart_home'), 'smart_home'),
        (os.path.join(cwd, 'workspace_store.py'), '.'),
        (os.path.join(cwd, 'README.md'), '.'),
        (os.path.join(cwd, 'requirements.txt'), '.'),
        (os.path.join(cwd, 'app_version.txt'), '.')
    ],
    hiddenimports=[
        'mediapipe',
        'cv2',
        'instagrapi',
        'google.genai',
        'PyQt6',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebChannel',
        'pyautogui',
        'sounddevice',
        'keyboard',
        'docx',
        'pptx',
        'multipart'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Filter out the ig_browser_profile cache to avoid MAX_PATH crashes and bloating the EXE
a.datas = [x for x in a.datas if 'ig_browser_profile' not in x[0]]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NIGHTFALLEcho',
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
    icon=__import__('os').path.join(cwd, 'assets/NIGHTFALL_Lite_Logo.ico') if __import__('os').path.exists(__import__('os').path.join(cwd, 'assets/NIGHTFALL_Lite_Logo.ico')) else None,
    version=__import__('os').path.join(cwd, 'version.txt')
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NIGHTFALLEcho'
)
