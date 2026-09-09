"""Private atomic state and advisory ownership locks; never trust saved PIDs."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import uuid


def private_dir(path):
    path = Path(path).absolute()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError(f"state directory must be a real directory owned by you: {path}")
    if info.st_mode & 0o077:
        raise ValueError(f"state directory must have mode 700: {path}")
    return path


def monitor_dir(root, ident):
    if not re.fullmatch(r"[0-9a-f]{32}", ident):
        raise ValueError("invalid monitor ID")
    path = root / ident
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"monitor not found: {ident}")
    return path


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        with open(temporary, "x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(path, blocking=True):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def alive(directory):
    try:
        with lock(directory / "active.lock", blocking=False):
            return False
    except BlockingIOError:
        return True


def status(directory):
    running = alive(directory)
    value = read(directory / "state.json")
    value["supervisor_alive"] = running
    value["log_dir"] = str(directory)
    if not value["supervisor_alive"]:
        if value["process_state"] in ("starting", "running"):
            value["process_state"] = "failed"
            value["stop_reason"] = "supervisor_lost; owned command may still be running"
        if value["delivery_state"] == "sending":
            value["delivery_state"] = "uncertain"
    return value
