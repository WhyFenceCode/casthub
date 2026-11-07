#!/usr/bin/env python3
"""Ask the running root helper (tools/rootd.py) to run one command as root.

    tools/asroot.py --why "Install Cast Hub packages" -- dnf install sway

The command appears in the helper's terminal and runs only if you approve it
there. Output streams back here; the exit code is the command's (126 = denied,
125 = helper not running or script changed, 124 = timed out waiting).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path


def base_dir() -> Path:
    if os.environ.get("CASTHUB_ROOTD_TEST") == "1":
        return Path(os.environ["CASTHUB_ROOTD_BASE"])
    return Path(f"/run/user/{os.getuid()}/casthub-root")


def helper_running(base: Path) -> bool:
    try:
        os.kill(int((base / "rootd.pid").read_text().strip()), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--why", required=True, help="reason shown to the approver")
    ap.add_argument("--cwd", default=None, help="working directory inside the repo (default: repo root)")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds to wait for approval + run")
    ap.add_argument("argv", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
    if not argv:
        ap.error("no command given (put it after --)")

    base = base_dir()
    if not helper_running(base):
        repo = Path(__file__).resolve().parents[1]
        print("The root helper isn't running. Start it in a terminal you keep open:\n"
              f"    sudo python3 -I {repo}/tools/rootd.py", file=sys.stderr)
        return 125

    rid = time.strftime("%H%M%S") + "-" + secrets.token_hex(4)
    req = {"id": rid, "argv": argv, "why": a.why, "cwd": os.path.abspath(a.cwd) if a.cwd else None}
    tmp = base / "req" / f".{rid}.tmp"
    tmp.write_text(json.dumps(req))
    tmp.replace(base / "req" / f"{rid}.json")
    print(f"[asroot] waiting for approval in the root helper terminal… ({rid})", file=sys.stderr, flush=True)

    logp, resp = base / "res" / f"{rid}.log", base / "res" / f"{rid}.json"
    pos, deadline = 0, time.monotonic() + a.timeout
    while time.monotonic() < deadline:
        if logp.exists():
            with open(logp, "rb") as f:
                f.seek(pos)
                chunk = f.read()
                pos += len(chunk)
                if chunk:
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
        if resp.exists():
            if logp.exists():  # drain the tail
                with open(logp, "rb") as f:
                    f.seek(pos)
                    sys.stdout.buffer.write(f.read())
            result = json.loads(resp.read_text())
            if result.get("denied"):
                print("[asroot] denied by the approver", file=sys.stderr)
            return int(result["exit"])
        if not helper_running(base):
            print("[asroot] the root helper stopped", file=sys.stderr)
            return 125
        time.sleep(0.2)
    (base / "req" / f"{rid}.json").unlink(missing_ok=True)
    print("[asroot] timed out", file=sys.stderr)
    return 124


if __name__ == "__main__":
    sys.exit(main())
