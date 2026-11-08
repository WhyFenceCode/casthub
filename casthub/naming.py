"""Receiver names: `CAST-` + 5 letters/digits, new each hub start and after each cast (SPEC D26).

The 5 characters come from a hash of the machine ID, the screen index and a timestamp, so
they look random, never repeat in practice, and reveal nothing about the machine. Windows then
lists a fresh display after every disconnect (owner, Nov 8) instead of one it remembers. A
name pinned in casthub.toml (`[[screen]]` with `output` and `name`) wins and never changes.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

PREFIX = "CAST-"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTUVWXYZ"     # no I, L, O: easy to read off a screen
LENGTH = 5


def machine_id() -> str:
    try:
        return Path("/etc/machine-id").read_text().strip()
    except OSError:
        return ""


def session_salt() -> str:
    """A fresh value per hub start or disconnect."""
    return str(time.time_ns())


def session_name(index: int, salt: str, mid: str | None = None) -> str:
    """`CAST-XXXXX` for screen `index` in the session identified by `salt`."""
    digest = hashlib.sha256(f"{machine_id() if mid is None else mid}:{index}:{salt}".encode()).digest()
    n = int.from_bytes(digest[:8], "big")
    chars = []
    for _ in range(LENGTH):
        n, r = divmod(n, len(ALPHABET))
        chars.append(ALPHABET[r])
    return PREFIX + "".join(chars)
