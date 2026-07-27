"""Create and maintain Magnolia's transcript-sync venv.

Provides two functions:
  venv_python(root) — returns the expected venv interpreter path (read-only)
  ensure(root)      — creates the venv + installs deps if absent (idempotent)
"""
import os
import subprocess
import sys
import venv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platform_lib  # noqa: E402
import profile_lib  # noqa: E402

_VENV_DIR = "venv"
_REQUIREMENTS = "requirements-transcript.txt"
_EXTRA_DEPS = ["ruamel.yaml"]


def _venv_root(root=None):
    base = root or profile_lib.PM_OS_DIR
    return os.path.join(base, _VENV_DIR)


def venv_python(root=None):
    """Return the path to the venv's Python interpreter (no side effects)."""
    vr = _venv_root(root)
    if platform_lib.os_kind() == "windows":
        return os.path.join(vr, "Scripts", "python.exe")
    return os.path.join(vr, "bin", "python3")


def ensure(root=None):
    """Create the venv and install deps if the interpreter doesn't exist yet."""
    py = venv_python(root)
    if os.path.isfile(py):
        return py

    vr = _venv_root(root)
    base = root or profile_lib.PM_OS_DIR
    req = os.path.join(base, _REQUIREMENTS)

    venv.create(vr, with_pip=True, clear=False)

    if os.path.isfile(req):
        subprocess.run(
            [py, "-m", "pip", "install", "-q", "-r", req],
            check=True, timeout=120,
        )

    if _EXTRA_DEPS:
        subprocess.run(
            [py, "-m", "pip", "install", "-q"] + _EXTRA_DEPS,
            check=True, timeout=60,
        )

    return py


if __name__ == "__main__":
    p = ensure()
    print(f"Venv ready: {p}")
