"""Switcher PIN: stored only as a salted scrypt hash (`scrypt$n$r$p$salt$hash`, base64)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

N, R, P = 2**14, 8, 1
PIN_RE = re.compile(r"\d{4,8}")


def valid_pin(pin: str) -> bool:
    return bool(PIN_RE.fullmatch(pin))


def hash_pin(pin: str, salt: bytes | None = None) -> str:
    if not valid_pin(pin):
        raise ValueError("PIN must be 4 to 8 digits")
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(pin.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${N}${R}${P}${b64(salt)}${b64(dk)}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, want = stored.split("$")
        if kind != "scrypt":
            return False
        dk = hashlib.scrypt(pin.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(want))
    except (ValueError, TypeError):
        return False
