#!/usr/bin/env python3
"""Set one key in /etc/casthub/casthub.toml (root, through the root helper).

    tools/asroot.py --why "..." -- tools/set-config.py <section> <key> <value>

The value is written as a TOML string unless it is true/false or an integer. The result
must still load with casthub.config (else nothing is written). A Btrfs snapshot is taken
first and the old file is kept as casthub.toml.bak. Values never enter the repo.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from casthub import config  # noqa: E402

PATH = config.DEFAULT_PATH


def render(value: str) -> str:
    if value in ("true", "false") or re.fullmatch(r"-?\d+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_key(text: str, section: str, key: str, value: str) -> str:
    lines = text.split("\n")
    line = f"{key} = {render(value)}"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"[{section}]"), None)
    if start is None:
        return text.rstrip("\n") + f"\n\n[{section}]\n{line}\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"\s*{re.escape(key)}\s*=", lines[i]):
            lines[i] = line
            return "\n".join(lines)
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, line)
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    if os.geteuid() != 0:
        print("run as root (through tools/asroot.py)", file=sys.stderr)
        return 1
    section, key, value = sys.argv[1:]
    old = PATH.read_text() if PATH.exists() else ""
    new = set_key(old, section, key, value)
    import tomllib
    config.parse(tomllib.loads(new))          # raises on a bad value: nothing written
    subprocess.run([str(ROOT / "tools" / "snapshot.sh"), "pre-config"], check=True)
    if PATH.exists():
        shutil.copy2(PATH, PATH.with_suffix(".toml.bak"))
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(new)
    shutil.chown(PATH, "root", "casthub")
    PATH.chmod(0o640)
    print(f"set [{section}] {key} in {PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
