"""Build a self-contained Python payload on the target OS (no runtime pip install)."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

desktop = Path(__file__).resolve().parents[1]
root = desktop.parent
build = desktop / "build"
environment = build / "python-build"
python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not python.exists():
    venv.EnvBuilder(with_pip=True).create(environment)

def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, cwd=root)

run(python, "-m", "pip", "install", "-r", desktop / "backend-requirements.txt", f"{root}[openai]")
run(python, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
    "--name", "feiyu-backend", "--collect-all", "feiyu", "--copy-metadata", "feiyu-agent",
    "--distpath", build / "payload", "--workpath", build / "pyinstaller",
    "--specpath", build, desktop / "scripts/backend_entry.py")
payload = build / "payload/feiyu-backend"
binary = payload / ("feiyu-backend.exe" if os.name == "nt" else "feiyu-backend")
run(binary, "--version")
run(python, desktop / "tests/check_backend.py", binary)
destination = build / "backend"
if destination.exists():
    shutil.rmtree(destination)
shutil.copytree(payload, destination)
print(f"Desktop backend ready: {destination}")
