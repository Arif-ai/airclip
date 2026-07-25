"""
build_exe.py — Build AirClip.exe using PyInstaller
"""
import os
import shutil
import subprocess
import sys

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--noconsole",
    "--name", "AirClip",
    "--add-data", "core;core",
    "--icon", "icon.ico",
    "--hidden-import", "plistlib",
    "--hidden-import", "PIL",
    "--hidden-import", "flask",
    "--hidden-import", "pyperclip",
    "--hidden-import", "webview",
    "--hidden-import", "webview.platforms.winforms",
    "--hidden-import", "webview.platforms.edgehtml",
    "--hidden-import", "webview.platforms.edgechromium",
    "--hidden-import", "webview.platforms.mshtml",
    "--hidden-import", "clr",
    "--hidden-import", "pythonnet",
    "--hidden-import", "zeroconf",
    "--hidden-import", "qrcode",
    "main.py"
]

print("Building AirClip.exe with complete pywebview backends...")
result = subprocess.run(cmd, cwd=".")

if result.returncode == 0:
    if os.path.exists("scripts/AirClip.sh"):
        os.makedirs("dist", exist_ok=True)
        shutil.copy("scripts/AirClip.sh", "dist/AirClip.sh")
    print("\n[SUCCESS] Build complete! AirClip.exe is ready in dist/")
else:
    print("\n[ERROR] Build failed.")
    sys.exit(1)
