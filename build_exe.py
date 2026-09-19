"""
PyInstaller build script to package AppleMusicImporter into a single portable EXE.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.resolve()
STATIC_SRC = ROOT_DIR / "applemusic" / "web" / "static"

print(f"Project root: {ROOT_DIR}")
print(f"Static source: {STATIC_SRC}")
assert STATIC_SRC.exists(), f"Static dir not found: {STATIC_SRC}"

ICON_PATH = ROOT_DIR / "app_icon.ico"
if ICON_PATH.exists():
    print(f"Icon file found: {ICON_PATH}")

# PyInstaller command arguments
pyinstaller_args = [
    sys.executable,
    "-m", "PyInstaller",
    "--name=AppleMusicImporter",
    "--onefile",
    "--clean",
    "--noconsole",
    f"--icon={ICON_PATH}",
    # Add static files
    f"--add-data={STATIC_SRC};applemusic/web/static",
    # Hidden imports for FastAPI / Uvicorn / WebSockets
    "--hidden-import=uvicorn.protocols.http.auto",
    "--hidden-import=uvicorn.protocols.websockets.auto",
    "--hidden-import=uvicorn.lifespan.on",
    "--hidden-import=uvicorn.logging",
    "--hidden-import=websockets",
    "--hidden-import=websockets.legacy",
    "--hidden-import=websockets.legacy.client",
    "--hidden-import=cryptography",
    "--hidden-import=cryptography.hazmat.primitives.ciphers.aead",
    "--hidden-import=requests",
    "--hidden-import=pydantic",
    "--hidden-import=rich",
    "--hidden-import=typer",
    "--hidden-import=mutagen",
    "--hidden-import=mutagen.mp4",
    "--hidden-import=mutagen.id3",
    "--hidden-import=mutagen.flac",
    "--hidden-import=mutagen.easyid3",
    "--hidden-import=av",
    str(ROOT_DIR / "exe_entry.py"),
]

print("Running PyInstaller...")
print(" ".join(pyinstaller_args))
subprocess.run(pyinstaller_args, cwd=str(ROOT_DIR), check=True)

dist_exe = ROOT_DIR / "dist" / "AppleMusicImporter.exe"
if dist_exe.exists():
    target_exe = ROOT_DIR / "AppleMusicImporter.exe"
    try:
        shutil.copy2(dist_exe, target_exe)
        print(f"\n[SUCCESS] Standalone EXE generated successfully at: {target_exe}")
        print(f"File size: {target_exe.stat().st_size / (1024*1024):.2f} MB")
    except PermissionError:
        print(f"\n[SUCCESS] Standalone EXE generated successfully at: {dist_exe}")
        print(f"File size: {dist_exe.stat().st_size / (1024*1024):.2f} MB")
        print("[Notice] Could not overwrite root AppleMusicImporter.exe because a running instance has it open.")
else:
    print("[ERROR] Failed to locate generated EXE in dist/")
