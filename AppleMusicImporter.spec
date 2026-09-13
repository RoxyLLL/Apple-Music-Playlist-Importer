# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['D:/applemusic/exe_entry.py'],
    pathex=[],
    binaries=[],
    datas=[('D:/applemusic/applemusic/web/static', 'applemusic/web/static')],
    hiddenimports=['uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'uvicorn.logging', 'websockets', 'websockets.legacy', 'websockets.legacy.client', 'cryptography', 'cryptography.hazmat.primitives.ciphers.aead', 'requests', 'pydantic', 'rich', 'typer', 'mutagen', 'mutagen.mp4', 'av'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AppleMusicImporter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:/applemusic/app_icon.ico'],
)
