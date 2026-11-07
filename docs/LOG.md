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

## M7: Login screen and modes (done), 2026-11-07

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

Installed through the root helper (snapshots taken first): packages, theme, sessions, `casthub`
account, PAM rule, SDDM config. Real `sddm-greeter-qt6 --test-mode` loads the theme with no
errors (a deliberately broken copy falls back to the built-in theme, as intended). Display
manager switched to SDDM.

Left for acceptance (after reboot): install, switch to SDDM, then check that Display
starts with no password, Console works for admins only, and leaving a mode returns to the login screen.

## M7 acceptance + owner feedback, 2026-11-07

Owner tested after reboot: login screen, Display (no password), Console and Workstation all work.
Fixes from that test:
- Light/dark now follows the selected user's own Plasma setting (published at login by
  `casthub-appearance` to `/var/lib/casthub/appearance/<user>`), falling back to light.
  Only the boot splash is always dark.
- Sign-in errors show beside the password field (with a shake); footer and top-bar centre
  text are centred on the screen.

## M8 (started early): boot splash, 2026-11-07

- Fedora's `bgrt` Plymouth theme re-showed the firmware logo (the coreboot rabbit). Replaced by
  a Cast Hub theme: the signature writes itself on the dark background (design page 2).
- `brand/signature.py`: Mrs Saint Delafield outlines positioned by Pango; overlay against the
  mockup coincides. Timing from the brand page (0.55 s stroke, 0.2 s stagger, fill fade, 0.9 s
  flourish; 3.85 s total). `brand/flourish.json` traced from the mockup's accent swash.
- `brand/splash.py` generates frames for the real screen at install time; text is pre-rendered,
  so the initramfs needs no fonts. `tools/install-splash.sh` backs up the initramfs first.
- Not changed: the firmware's own logo, shown for a moment before Linux starts.

## M7 accepted, 2026-11-07

Owner confirmed after reboot: signature boot splash, light login screen following the user's
setting, inline password errors, centred footer. M7 done.

## M8: desktop theme (in progress), 2026-11-07

Owner choices: pastel status colours; M8 themes Plasma (custom shell is M9); the Display QR
joins the hotspot.
- Status colours are pastels (`brand/tokens.toml`). They fail as text on paper, so they are
  shown as chips with ink text; Plasma's status *text* colours are deepened only as far as
  needed for 4.5:1 (`status_text()`, tested).
- `uikit/`: the shared QML kit (tokens, labels, pills, chips, grid, wordmark). The greeter uses it;
  Display screens and the M9 shell will too.
- `shell/`: Plasma colour schemes and global themes `org.casthub.light` / `org.casthub.dark`
  (no panel layout, so nobody's panels are reset), a login splash, and `brand/wallpaper.py`
  (background + 12-column grid, built for the real screen).
- `tools/install-theme.sh`: one installer for every themed surface. It sets system defaults in
  `/etc/xdg`, so every user who never picked a global theme follows Cast Hub.
- Terminal status (owner request): ink text on a pastel highlight. A failed command leaves an
  `exit N` chip before the prompt; `casthub_chip error|warn|ok|info TEXT` for scripts; gcc
  diagnostics as chips. Only in the Cast Hub Konsole profiles (`system/profile.d/casthub-terminal.sh`).
- Fixed: `install-modes.sh` did not copy `uikit/` with the greeter (`tools/lib/greeter.sh`).
- Owner test after reboot: works; login splash was caught half-drawn (fade-in + stage-driven
  progress bar, and Plasma closes the splash before the last stage). Now drawn complete from
  the first frame, no progress bar. The rabbit before Linux is the firmware logo (D16: later).
- Display screens (design pages 12-13, own 1080x633 frame, measured to within ~2 px):
  `casthub/ui/` idle / connecting / streaming + busy toast, one full-screen window per output,
  hotspot QR only when the hotspot service has written its join code (D13); the UI never
  holds the Wi-Fi passphrase. `casthub/config.py` (validated TOML) arrives early from M3.
- Leave page (`casthub/switcher/`): Ctrl+Alt+Esc → Return to login / Restart / Shut down.
  Owner decision: no PIN, since leaving only returns to the login screen (D7). If the UI is not
  running, Ctrl+Alt+Esc still leaves. The PIN (scrypt hash, `casthub-pin`) is kept for the LAN
  page only (D10).
- Terminal colours now reference the colour tokens; test: one accent edit changes every
  generated surface and no QML hard-codes a brand colour.
