"""Machine-readable CLI. All mutations target explicit local monitor IDs."""

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from . import __version__, state


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def bounded_int(value):
    number = int(value)
    if not 1 <= number <= 1_073_741_824:
        raise argparse.ArgumentTypeError("must be between 1 and 1073741824")
    return number


def doctor(codex):
    binary = shutil.which(codex)
    if not binary:
        raise ValueError(f"Codex executable not found: {codex}")
    result = subprocess.run([binary, "queue", "--help"], capture_output=True, timeout=10)
    if result.returncode or b"--thread" not in result.stdout or b"--message" not in result.stdout:
        raise ValueError("this Codex executable does not support queue; install a current Codex CLI")
    return {"codex": str(Path(binary).absolute()), "queue_supported": True,
            "note": "CLI capability only; target server support and thread processing are not verified"}


def launch(root, directory, retry=False):
    # Import from this installation even when invoked from another working directory.
    args = [sys.executable, "-m", "openmonitor", "--state-dir", str(root),
            "_run", directory.name]
    if retry:
        args.append("--retry")
    with open(directory / "supervisor.log", "ab") as log:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True, cwd=Path(__file__).resolve().parents[1])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if state.alive(directory) or process.poll() is not None:
            return
        time.sleep(.01)
    raise ValueError(f"supervisor startup not confirmed; inspect {directory / 'supervisor.log'}")


def start(root, args):
    thread = str(uuid.UUID(args.thread))
    if len(args.name) > 100 or not args.name.strip():
        raise ValueError("name must contain 1-100 characters")
    if not 2 <= args.max_events <= 64:
        raise ValueError("max-events must be between 2 and 64 (includes the final event)")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ValueError("a command is required after --")
    codex = doctor(args.codex)["codex"]
    with state.lock(root / "registry.lock"):
        for existing in root.glob("*/config.json"):
            config = state.read(existing)
            if config["thread"] == thread and config["name"] == args.name and state.alive(existing.parent):
                raise ValueError("a monitor with this name and thread is already active")
        ident = uuid.uuid4().hex
        directory = state.private_dir(root / ident)
        config = dict(id=ident, thread=thread, name=args.name, command=command,
                      cwd=os.getcwd(), delivery_cwd=str(root), codex=codex, remote=args.remote, mode=args.mode,
                      timeout=args.timeout, batch_seconds=args.batch_seconds,
                      max_output_bytes=args.max_output_bytes, max_events=args.max_events,
                      delivery_timeout=args.delivery_timeout, created_at=time.time())
        state.write(directory / "config.json", config)
        state.write(directory / "state.json", dict(id=ident, thread=thread, name=args.name,
                    process_state="starting", delivery_state="pending", bytes_read=0,
                    events=0, returncode=None, stop_reason=None, last_output_at=None))
        launch(root, directory)
    return state.status(directory)


def parser():
    p = argparse.ArgumentParser(description="Run local watchers and queue events to an existing Codex thread.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--state-dir", default=str(Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "openmonitor"))
    commands = p.add_subparsers(dest="action", required=True)
    d = commands.add_parser("doctor", help="check local Codex queue capability without sending a message")
    d.add_argument("--codex", default="codex")
    s = commands.add_parser("start", help="detach a command and return its monitor ID")
    s.add_argument("--thread", required=True, help="explicit existing Codex thread UUID")
    s.add_argument("--name", required=True)
    s.add_argument("--mode", choices=["exit", "lines"], default="exit")
    s.add_argument("--timeout", type=positive, default=3600)
    s.add_argument("--batch-seconds", type=positive, default=2)
    s.add_argument("--delivery-timeout", type=positive, default=15)
    s.add_argument("--max-output-bytes", type=bounded_int, default=8_388_608)
    s.add_argument("--max-events", type=bounded_int, default=20)
    s.add_argument("--codex", default="codex")
    s.add_argument("--remote", help="Codex app-server endpoint; passed unchanged to codex queue")
    s.add_argument("command", nargs=argparse.REMAINDER)
    commands.add_parser("list", help="list saved monitor states")
    for action in ("status", "stop", "retry", "_run"):
        sub = commands.add_parser(action)
        sub.add_argument("id")
        if action == "retry":
            sub.add_argument("--accept-duplicate-risk", action="store_true", required=True)
        if action == "_run":
            sub.add_argument("--retry", action="store_true")
    return p


def main():
    args = parser().parse_args()
    os.umask(0o077)
    try:
        if sys.platform not in ("darwin", "linux"):
            raise ValueError("OpenMonitor currently supports macOS and Linux")
        if args.action == "doctor":
            result = doctor(args.codex)
        else:
            root = state.private_dir(args.state_dir)
            if args.action == "start":
                result = start(root, args)
            elif args.action == "list":
                result = [state.status(p.parent) for p in sorted(root.glob("*/state.json"))]
            else:
                directory = state.monitor_dir(root, args.id)
                if args.action == "_run":
                    from .supervisor import run
                    run(directory, retry=args.retry)
                    return
                if args.action == "stop":
                    with state.lock(root / "registry.lock"):
                        state.write(directory / "stop.json", {"requested_at": time.time()})
                if args.action == "retry":
                    with state.lock(root / "registry.lock"):
                        if state.alive(directory):
                            raise ValueError("monitor is still active; stop it before retrying")
                        # Retire the previous stop before launch. A later stop must survive.
                        (directory / "stop.json").unlink(missing_ok=True)
                        launch(root, directory, retry=True)
                result = state.status(directory)
        print(json.dumps(result, ensure_ascii=True))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"openmonitor: {exc}", file=sys.stderr)
        sys.exit(1)
