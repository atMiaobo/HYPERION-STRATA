"""Private, atomic XDG state; never write compositor configuration files."""
import json
import os
from pathlib import Path
import tempfile


def directory(kind):
    defaults = {"CONFIG": Path.home()/".config", "CACHE": Path.home()/".cache",
                "RUNTIME": Path("/tmp")/f"cadere-{os.getuid()}"}
    base = Path(os.environ.get(f"XDG_{kind}_HOME", defaults[kind])) if kind != "RUNTIME" else Path(os.environ.get("XDG_RUNTIME_DIR", defaults[kind]))
    if kind == "RUNTIME" and "XDG_RUNTIME_DIR" not in os.environ:
        base.mkdir(mode=0o700, exist_ok=True)
        if base.is_symlink() or base.stat().st_uid != os.getuid(): raise ValueError("unsafe runtime fallback")
        base.chmod(0o700)
    path = base/"hyperion-cadere"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError("unsafe XDG state directory")
    path.chmod(0o700)
    return path


def atomic(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".cadere-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def save(path, value):
    atomic(path, json.dumps(value, allow_nan=False, indent=2)+"\n")


def read(path, default=None):
    try:
        if Path(path).stat().st_size > 2_000_000: return default
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default
