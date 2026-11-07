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

## M7: Login screen and modes (in progress), 2026-11-07

Built (repo):
- `greeter/`: SDDM Qt 6 theme matching design pages 3–5 (light + dark, auto by time of day).
  Arrow keys / Tab change mode, Enter starts. Console is offered only to `wheel` users.
  After leaving Display, Workstation is preselected.
- `modes/`: three session files, user targets, `casthub-session` gatekeeper, Display and
  Console Sway configs, always-dark Konsole profile.
- `brand/tokens.toml` + `brand/build.py` (M7 subset), OFL fonts in `brand/fonts/`.
- `system/sddm/casthub.conf`: theme, hidden `casthub` user, sessions restricted to the three modes.
- `tools/snapshot.sh`, `tools/install-modes.sh`, `tools/switch-display-manager.sh`,
  `docs/RECOVERY.md`.
- `tools/preview_greeter.py` renders the theme off-screen with a mocked SDDM (output → `local/`).

Verified without root: theme renders and matches the mockups at 1080×675 and 1920×1080;
keyboard flow and `sddm.login` calls correct; gatekeeper allow/refuse table (tests).

Left for acceptance (needs sudo + reboot): install, switch to SDDM, then check that Display
starts with no password, Console works for admins only, and leaving a mode returns to the login screen.
