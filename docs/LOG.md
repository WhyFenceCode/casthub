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

## M9: desktop shell (in progress), 2026-11-07

Owner answers: nothing done yet; Plasma's tray kept if it does not stand out (else text only);
agenda hidden when no calendar is set up unless it is a holiday; thin bar everywhere.
- Built on Plasma (D17): shell package `org.casthub.desktop` (selected in
  `/etc/xdg/plasmashellrc`), desktop layer `org.casthub.desktopview` (Today panel, agenda,
  meters, app panel frame; desktop tiles, faint date, footer), top-bar widgets
  (`org.casthub.topbar`, `org.casthub.clock`) beside Plasma's system tray, a transparent
  Plasma theme, the QML window decoration `org.casthub.decoration`, and the KWin script
  `org.casthub.views` (three virtual desktops; Konsole/Dolphin docked into the panel frame).
- Panel geometry comes from `[shell]` tokens into both the desktop layer and the KWin script.
- Dolphin resizes itself after it first appears and KWin does not resend an unchanged size
  request, so the script re-fits on buffer changes and nudges the request.
- `tools/preview_shell.sh`: headless Plasma (virtual KWin) with system/xdg standing in for
  /etc/xdg; screenshots go to `local/previews/`.
- Tray: monochrome symbolic icons in the bar's text colour; kept (D18).
- `tools/adopt-desktop.sh`: for accounts that logged in before (Plasma only rebuilds theme
  defaults when the theme name changes).
- Owner check: desktop looks good. Added "Log out" at the right of the top bar (inside the
  clock widget, so existing layouts get it too); it opens Plasma's logout confirmation.
- Owner check: boot splash froze half-written when Plymouth handed over to the login screen,
  and its background should be pure black. Plymouth now draws the full signature on quit, and
  the splash uses `[boot] bg = #000000` (frames, labels and screen).
- Owner check, round 2:
  - Plasma's leave screen (logout confirmation) is ours now (`contents/logout/Logout.qml` in the
    shell package): the Display leave-page frame, right-snapped choices, keys, Plasma's
    countdown kept for single-action requests. The top bar's Log out opens it with all choices.
  - Lock / wake-from-sleep screen: Plasma's unlock UI on a black grid background with the full
    signature (`brand/wallpaper.py --lock`, `/etc/xdg/kscreenlockerrc`). Plasma's unlock logic is
    kept on purpose: a broken lock screen locks the owner out.
  - Login screen "Switch user" cycled through a one-user list, so it did nothing. It now steps on
    to "Other user", where any account name can be typed.
  - Display: "Log out" in the bar opens the leave page; the leave page's first choice is
    "Log out"; "Passthrough setup" is listed as Later (D21, M10). The ways-to-connect block and
    the leave choices are right-snapped grids, each key on one line.
  - KWin blur off: unused by the design, and it failed every frame on this GPU.
- Owner check, round 3:
  - Alt+Tab: `shell/tabbox/org.casthub.switcher` (numbered rows: app, title, view).
  - Notifications, volume/brightness OSD, tray pop-ups, KRunner, tooltips: Plasma 6.8 compiles
    their QML in, so they are themed through the `casthub` desktop theme (generated pop-up frame,
    tooltip and 2 px progress bar, `brand/build.py`) plus the colour scheme and fonts.
  - Admin password prompt (polkit agent): also compiled in; it follows the colour scheme, fonts and
    our window frame. Its layout is KDE's.
  - Boot splash: the signature could show cut off when the screen changed size during boot (the
    real graphics driver replacing the firmware framebuffer) or a second screen appeared; the
    Plymouth script now re-centres and scales to the smallest screen whenever the screens change.
- Owner check, round 4: the lock / wake-from-sleep screen is ours now
  (`contents/lockscreen/LockScreen.qml` in the shell package): 24-hour time and date on the
  left, the signature on two lines on the right (`signature.py --two-lines`, rendered at install),
  password block and Switch user / Sleep / Unlock in the login screen's style. Unlocking goes
  through kscreenlocker's authenticator only; tested with `kscreenlocker_greet --testing`
  (which also showed a load error falls back to KDE's built-in locker, not a locked-out screen).

## M3: receiver (done), 2026-11-08

Acceptance (owner, over Wi-Fi Direct): a Windows 11 PC casts with Win + K and the picture
shows fullscreen. The hub logged all 11 successful casts as `via p2p`: Windows paired
directly with the Chromebook. Casting over the same network (MS-MICE, port 7250) is built
but has never carried a cast; it is M4B. 102 tests pass.

- Hub, MICE listener, RTSP sink (M1-M16 handshake), GStreamer pipeline (VA-API H.264, AAC).
  The M3 reply lists only parameters we support; Windows stalled when it saw others.
- Wi-Fi Direct: push-button pairing through NetworkManager (NM 1.58 has no PIN). Windows'
  invitations to old groups are refused, so it pairs again.
- Video freeze fixed: an audio sink that never pre-rolled held the pipeline (async=false).
  waylandsink crashed on output resize, so the video uses glimagesink. Each cast plays in its
  own process (`casthub/media/player.py`), which the hub kills when the cast ends. A stuck
  sink once froze the whole hub.
- A cast ends when the PC's packets stop for 8 s (Wi-Fi Direct link dropped).
- Receiver names `CAST-XXXXX` (D26), new at hub start and after every cast, with a new random
  Wi-Fi Direct address each time. The screen says Loading (moving dots) until the name is
  findable.
- Video modes offered in the shape of the screen or window (extend mode).
- `casthub-display` opens Display mode as a panel inside Workstation or Console (D27);
  `--capture FILE` for debugging, `--kill` for a stuck panel.
- Known limits: no PIN pairing over Wi-Fi Direct (NetworkManager); multi-receiver placement
  and port 7251 are M4.

## M4B: Miracast over the network (open), 2026-11-08

Built in M3 but untested: mDNS `_display._tcp` on 7250, the MS-MICE listener, and the "supports
infrastructure" bit in our Wi-Fi Direct probe responses that should make Windows connect over
the network instead of pairing. Windows has always paired over Wi-Fi Direct so far. To do: find
out why, and get one cast to play `via mice`. M4's two receivers (7250, 7251) depend on it,
since Wi-Fi Direct offers only one receiver per radio.
