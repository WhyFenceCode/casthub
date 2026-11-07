#!/usr/bin/env python3
"""Cast Hub root helper: type the sudo password once, then approve each action.

Start it in a terminal you keep open:

    sudo python3 -I ~/code/casthub/tools/rootd.py

Claude Code (or you) then asks for privileged actions with tools/asroot.py.
Each request appears here with its reason, the exact command, and whether any
repo script it runs matches the committed version. Nothing runs until you
press y. Ctrl+C at the prompt (or 3 h idle) ends the helper.

Nothing from here is committed:
  * requests/results live in /run/user/<uid>/casthub-root (tmpfs, outside the repo)
  * the action log is local/root-actions.log (git-ignored)
  * the sudo password is only ever typed into sudo itself
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import functools
from pathlib import Path

print = functools.partial(print, flush=True)  # noqa: A001  keep helper text in order with command output

REPO = Path(__file__).resolve().parents[1]
IDLE_EXIT_S = 3 * 3600
MAX_REQ_BYTES = 64 * 1024
ID_RE = re.compile(r"^[a-z0-9-]{1,48}$")
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")
TEST = os.environ.get("CASTHUB_ROOTD_TEST") == "1"   # tests only: run unprivileged

BOLD, DIM, ACC, RED, GRN, RST = "\033[1m", "\033[2m", "\033[38;2;126;118;208m", "\033[31m", "\033[32m", "\033[0m"


def die(msg: str, code: int = 1) -> None:
    print(f"rootd: {msg}", file=sys.stderr)
    sys.exit(code)


def clean(s: str) -> str:
    """Make untrusted text safe to show: no terminal escape sequences."""
    return CTRL_RE.sub(lambda m: f"\\x{ord(m.group()):02x}", s)


def base_dir(uid: int) -> Path:
    if TEST:
        return Path(os.environ["CASTHUB_ROOTD_BASE"])
    return Path(f"/run/user/{uid}/casthub-root")


class Helper:
    def __init__(self) -> None:
        if TEST:
            self.uid = os.getuid()
        else:
            if os.geteuid() != 0:
                die("start me with: sudo python3 -I " + str(Path(__file__).resolve()))
            if "SUDO_UID" not in os.environ:
                die("start me through sudo (SUDO_UID is not set)")
            self.uid = int(os.environ["SUDO_UID"])
        self.user = pwd.getpwuid(self.uid).pw_name
        self.base = base_dir(self.uid)
        self.req = self.base / "req"
        self.res = self.base / "res"
        self.logf = REPO / "local" / "root-actions.log"
        self.count = 0

    # ---- setup -----------------------------------------------------------
    def prepare(self) -> None:
        parent = self.base.parent
        if not parent.is_dir():
            die(f"{parent} does not exist (is {self.user} logged in?)")
        if self.base.is_symlink():
            die(f"{self.base} is a symlink; refusing")
        if self.base.exists():
            pidf = self.base / "rootd.pid"
            if pidf.exists() and self._alive(pidf):
                die("another root helper is already running")
            shutil.rmtree(self.base)
        self.base.mkdir(mode=0o755)
        self.req.mkdir(mode=0o700)
        self.res.mkdir(mode=0o755)
        if not TEST:
            os.chown(self.base, 0, 0)
            os.chown(self.req, self.uid, -1)    # only the user can submit
            os.chown(self.res, 0, 0)            # only root writes results
        (self.base / "rootd.pid").write_text(str(os.getpid()))
        (REPO / "local").mkdir(exist_ok=True)

    @staticmethod
    def _alive(pidf: Path) -> bool:
        try:
            os.kill(int(pidf.read_text().strip()), 0)
            return True
        except (ValueError, ProcessLookupError):
            return False
        except PermissionError:
            return True

    def log(self, line: str) -> None:
        new = not self.logf.exists()
        with open(self.logf, "a") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {line}\n")
        if new and not TEST:
            os.chown(self.logf, self.uid, -1)

    def as_user(self, *argv: str) -> subprocess.CompletedProcess:
        """Run a read-only helper (git) as the user, never as root: repo config can run commands."""
        cmd = list(argv) if TEST else ["runuser", "-u", self.user, "--", *argv]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20)

    def banner(self) -> None:
        head = self.as_user("git", "-C", str(REPO), "log", "-1", "--format=%h %s").stdout.strip()
        me = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
        print(f"{ACC}■{RST} {BOLD}Cast Hub root helper{RST}  for {self.user}")
        print(f"  repo   {REPO}  ({clean(head)})")
        print(f"  helper sha256 {me}")
        print(f"  {DIM}Every action needs your y. Ctrl+C at a prompt ends the helper.{RST}\n")

    def offer_snapshot(self) -> None:
        if TEST:
            return
        ans = self.ask("Take a Btrfs snapshot of / before any changes this session? [Y/n] ")
        if ans in ("", "y", "yes"):
            r = subprocess.run([str(REPO / "tools" / "snapshot.sh"), "rootd-session"])
            self.log(f"snapshot exit={r.returncode}")
        print()

    # ---- request handling --------------------------------------------------
    @staticmethod
    def ask(prompt: str) -> str:
        try:
            return input(prompt).strip().lower()
        except EOFError:
            return ""

    def read_request(self, path: Path) -> dict | None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            ok = stat.S_ISREG(st.st_mode) and st.st_uid == self.uid and st.st_size <= MAX_REQ_BYTES
            data = os.read(fd, MAX_REQ_BYTES + 1) if ok else b""
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)
        if not ok:
            print(f"{RED}ignored a request file with wrong owner/type/size{RST}")
            return None
        try:
            r = json.loads(data)
            assert isinstance(r, dict) and ID_RE.match(str(r.get("id", "")))
            argv = r["argv"]
            assert isinstance(argv, list) and argv and all(isinstance(a, str) and "\0" not in a and len(a) < 8192 for a in argv)
            r["why"] = str(r.get("why", ""))[:500]
            cwd = Path(os.path.realpath(r.get("cwd") or REPO))
            assert cwd == REPO or REPO in cwd.parents
            r["cwd"] = str(cwd)
            return r
        except (ValueError, KeyError, AssertionError, TypeError):
            print(f"{RED}rejected a malformed request (bad id/argv, or cwd outside the repo){RST}")
            try:
                rid = json.loads(data).get("id", "")
                if ID_RE.match(str(rid)):
                    self.write_result(rid, {"exit": 125, "denied": True})
            except (ValueError, AttributeError):
                pass
            return None

    def repo_files(self, r: dict) -> list[tuple[str, str, str]]:
        """Repo files the command names (scripts it will run), with hash and git state."""
        out = []
        for a in r["argv"]:
            p = Path(a) if os.path.isabs(a) else Path(r["cwd"]) / a
            try:
                rp = p.resolve()
            except OSError:
                continue
            if rp.is_file() and REPO in rp.parents and ".git" not in rp.parts:
                rel = str(rp.relative_to(REPO))
                h = hashlib.sha256(rp.read_bytes()).hexdigest()[:12]
                st = self.as_user("git", "-C", str(REPO), "status", "--porcelain", "--", rel).stdout.strip()
                tracked = self.as_user("git", "-C", str(REPO), "ls-files", "--error-unmatch", "--", rel).returncode == 0
                state = f"{GRN}committed, unchanged{RST}" if (tracked and not st) else f"{RED}NOT COMMITTED / MODIFIED: read it first{RST}"
                out.append((rel, h, state))
        return out

    def show(self, r: dict, files: list) -> None:
        self.count += 1
        print(f"{ACC}■{RST} {BOLD}Request {self.count}{RST}  {DIM}{r['id']}{RST}")
        print(f"  why   {clean(r['why']) or DIM + '(no reason given)' + RST}")
        print(f"  cwd   {clean(r['cwd'])}")
        print(f"  run   {BOLD}{clean(shlex.join(r['argv']))}{RST}")
        for rel, h, state in files:
            print(f"  file  {clean(rel)}  sha256 {h}  {state}")

    def write_result(self, rid: str, result: dict) -> None:
        tmp = self.res / f".{rid}.json"
        tmp.write_text(json.dumps(result))
        tmp.replace(self.res / f"{rid}.json")

    def run(self, r: dict, files: list) -> int:
        # Re-check scripts didn't change between showing and running.
        for rel, h, _ in files:
            if hashlib.sha256((REPO / rel).read_bytes()).hexdigest()[:12] != h:
                print(f"{RED}{rel} changed after it was shown; not running{RST}")
                return 125
        env = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
            "HOME": os.environ.get("HOME", "/root"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": os.environ.get("TERM", "xterm-256color"),
            "SUDO_USER": self.user,
            "SUDO_UID": str(self.uid),
        }
        logp = self.res / f"{r['id']}.log"
        with open(logp, "wb") as out:
            try:
                p = subprocess.Popen(r["argv"], cwd=r["cwd"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            except OSError as e:
                msg = f"could not start: {e}\n".encode()
                sys.stdout.buffer.write(msg)
                out.write(msg)
                return 127
            assert p.stdout is not None
            while True:
                try:
                    chunk = os.read(p.stdout.fileno(), 4096)
                except KeyboardInterrupt:
                    p.send_signal(signal.SIGINT)
                    continue
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
                out.write(chunk)
                out.flush()
            return p.wait()

    def handle(self, path: Path) -> None:
        r = self.read_request(path)
        if r is None:
            return
        files = self.repo_files(r)
        self.show(r, files)
        ans = self.ask(f"  {BOLD}Approve? [y/N]{RST} ")
        if ans not in ("y", "yes"):
            print(f"  {DIM}denied{RST}\n")
            self.log(f"DENIED  {shlex.join(r['argv'])}  why={r['why']!r}")
            self.write_result(r["id"], {"exit": 126, "denied": True})
            return
        print(f"{DIM}---- output ----{RST}")
        code = self.run(r, files)
        colour = GRN if code == 0 else RED
        print(f"{DIM}----{RST} {colour}exit {code}{RST}\n")
        self.log(f"RAN     exit={code}  {shlex.join(r['argv'])}  why={r['why']!r}")
        self.write_result(r["id"], {"exit": code, "denied": False})

    def loop(self) -> None:
        print(f"{DIM}waiting for requests…{RST}")
        last = time.monotonic()
        while time.monotonic() - last < IDLE_EXIT_S:
            pending = sorted(
                (p for p in self.req.iterdir() if p.suffix == ".json" and not p.name.startswith(".")),
                key=lambda p: p.stat().st_mtime,
            )
            for p in pending:
                self.handle(p)
                last = time.monotonic()
                print(f"{DIM}waiting for requests…{RST}")
            if TEST and os.environ.get("CASTHUB_ROOTD_ONESHOT") == "1" and self.count:
                return
            time.sleep(0.25)
        print("idle for 3 h; exiting")

    def close(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)
        self.log("helper stopped")


def main() -> int:
    h = Helper()
    h.prepare()
    h.log("helper started")
    try:
        h.banner()
        h.offer_snapshot()
        h.loop()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
