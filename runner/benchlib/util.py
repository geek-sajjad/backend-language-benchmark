import datetime as dt
import json
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]

_log_file = None


def set_log_file(path):
    global _log_file
    _log_file = pathlib.Path(path)
    _log_file.parent.mkdir(parents=True, exist_ok=True)


def log(msg):
    line = f"{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    if _log_file:
        with _log_file.open("a") as f:
            f.write(line + "\n")


def die(msg):
    log(f"ERROR: {msg}")
    sys.exit(1)


def atomic_write(path, text):
    """Temp file + rename, so a crash never leaves a half-written result."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_json(path, obj):
    atomic_write(path, json.dumps(obj, indent=2, sort_keys=False) + "\n")


def read_json(path, default=None):
    path = pathlib.Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def utc_now():
    return dt.datetime.now(dt.timezone.utc)
