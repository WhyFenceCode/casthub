"""Merge the keys of a KDE-style ini file into another with kwriteconfig6, one key at a time,
so keys already in the target stay. Usage: merge-ini.py <source> <target>"""

import subprocess
import sys


def entries(path: str):
    group = None
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            group = [g for g in line[1:-1].split("][")]
            continue
        key, _, value = line.partition("=")
        yield group, key.strip(), value.strip()


def main() -> int:
    src, dst = sys.argv[1:3]
    for group, key, value in entries(src):
        cmd = ["kwriteconfig6", "--file", dst]
        for g in group:
            cmd += ["--group", g]
        subprocess.run([*cmd, "--key", key, value], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
