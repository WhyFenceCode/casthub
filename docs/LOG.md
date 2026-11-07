# Milestone log

Short notes per milestone. No machine-specific output here: that goes in `local/`.

## M1: Fedora installed (done by owner)

## M2: Miracast go/no-go (done by owner)

Connection tests passed. Decision: build our own Python receiver (SPEC D9).

## Privacy guardrails (from M3), 2026-11-07

- `.gitignore`, `.githooks/` (pre-commit, commit-msg, pre-push → `privacy_scan.py`),
  `casthub.example.toml`, `CLAUDE.md`.
- `docs/SPEC.pdf` is blocked; `docs/SPEC.md` is the tracked spec.
- Tests: `python3 -I tests/test_privacy_scan.py` → 11 pass. Each hook verified to block a
  planted value.

## Login manager check, 2026-11-07

Fedora 45 ships Plasma Login Manager, whose greeter is compiled in and has no theme support.
Decision: switch to SDDM in M7 (SPEC D6).
