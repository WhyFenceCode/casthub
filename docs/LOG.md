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

## M4B: Miracast over the network (done), 2026-11-08

Built in M3 but untested: mDNS `_display._tcp` on 7250, the MS-MICE listener, and the "supports
infrastructure" bit in our Wi-Fi Direct probe responses that should make Windows connect over
the network instead of pairing. Windows has always paired over Wi-Fi Direct so far. To do: find
out why, and get one cast to play `via mice`. M4's two receivers (7250, 7251) depend on it,
since Wi-Fi Direct offers only one receiver per radio.

- Diagnosis: Windows never opened TCP 7250. Per [MS-MICE] (source receive rules and product
  notes), Windows resolves the probe response's host name with DNS and mDNS in parallel and
  gives up after 1.5 s, falling back to Wi-Fi Direct; with an IP Address attribute (0x2005,
  ASCII) it skips resolution. We sent no address. Firewall (7250 open) and network security
  (WPA2, required) were fine.
- Fix: casthub-p2p puts the Wi-Fi interface's IPv4 address(es) in the MS-MICE attributes and
  advertises again when the address changes.
- Acceptance (owner): a Windows 11 PC on the same Wi-Fi cast with Win + K; the hub logged
  `via mice` (MS-MICE over the network, port 7250), 960x540p60 at a steady 60 fps. After the
  cast: Loading, a new name and a new Wi-Fi Direct address. The video sink hung on stop again
  ("did not stop; exiting anyway") and only its own process was affected, as designed.

## M4: multi-port test failed → single receiver, mirrored on every screen (done), 2026-11-08

Test: `casthub-display --screens 2`, two receivers on one radio, CAST-U2X8P (7250, also
announced over Wi-Fi Direct) and CAST-6EX4B (7251), both published over mDNS
(`_display._tcp`). Win + K listed only CAST-U2X8P; a cast to it played `via mice`.

- Finding: Windows builds the Win + K list from Wi-Fi Direct (P2P) discovery, not from mDNS;
  mDNS only resolves a receiver it already found. A radio announces one P2P device, so one
  radio gives one entry in Win + K: `per_screen` doesn't work with Windows.
- Layout options left (SPEC "Screens and layout modes"): `per_radio` (a USB Wi-Fi adapter per
  extra screen) or `mirror_all`. Owner: set aside for now; this Chromebook has one screen.
- Also seen: receiver 0 landed on the second panel screen (WL-2); the hub does not sort
  outputs. Fixed below: the built-in screen (else the first by name) is the main one.
- Decision (owner): M4 is single receiver only. Every screen mirrors one receiver
  (`mirror_all`, now the only layout; `per_screen`/`per_radio` in an old config mean the same).
  The built-in screen is the main one (its shape picks the video modes); the video is decoded
  once and a tee feeds one glimagesink per screen, each window put fullscreen on its own screen
  by Sway con_id. The idle, connecting and PIN pages go to every screen.
- Check: headless Sway with two outputs, a local test stream through the player: one window per
  output, both fullscreen; the player stopped cleanly. M4 done (single only).

## M5: AirPlay (done), 2026-11-08

Owner had installed UxPlay 1.73.7 (Fedora package); nothing else done. Test device: iPhone/iPad.

- `casthub/airplay/uxplay.py`: one UxPlay under the receiver's name. `-vrtp`/`-artp` send the
  stream to our player on localhost (H.264 RTP, L16 RTP), so AirPlay uses the same player,
  mirroring and kill-on-hang as Miracast. Cast start/end from UxPlay's log; busy → restart
  UxPlay (drops the iPhone). After each cast: new name, new device ID. `setpriv --pdeathsig`
  takes UxPlay down with the hub; `casthub-display --kill` stops it too.
- Firewall: the active zone already allows 1025-65535, which covers UxPlay's ports.
- Check: headless Sway, two outputs, a local H.264 + L16 RTP stream through the player: one
  window per screen, fullscreen, clean stop. Live iPhone test pending.
- Live test 1: the iPhone never appeared. UxPlay exited at once: `-m` got the raw 6-byte
  address, which it rejects as non-UTF-8. Fixed (text MAC, test).
- Live test 2: the iPhone connected but the hub heard nothing until UxPlay exited: its stdio
  log into a pipe is block-buffered. Fixed: UxPlay runs under `stdbuf -oL`.
- Live test 3: "Connecting" but no picture. RTP arrived (about 80 packets/s) but
  rtph264depay output nothing: the iPhone sends SPS/PPS and its only keyframe as mirroring
  starts, before a player started on the connection request is listening. Reproduced locally
  (a late join with one keyframe: 0 buffers out; with a keyframe each second: normal). Fixed:
  an AirPlay player waits, listening, whenever the receiver is idle (no window until video
  comes); the cast uses it and a fresh one starts after. Checked headless with a
  single-keyframe stream on two screens.
- Owner: Windows still listed the hub during a cast. Now any cast tells casthub-p2p `busy`:
  the WFD IE says "session not available" and extended listen stops, so Win + K drops it; on
  idle it is discoverable again (then renamed).
- Live test 4: the picture shows on the screen, and Win + K drops the hub during the cast. But
  Stop Mirroring on the iPhone did not end the cast: UxPlay logs
  `raop_rtp_mirror->running is no longer true`, which the hub didn't watch for. It does now
  ("Connection closed on socket" is left out: iOS opens side connections too).
- Ctrl+Alt+Esc (Display mode) during AirPlay: `casthub-leave` → SIGUSR1 → end_all: the player
  stops, a new name, UxPlay restarts under it with a new device ID and key (no `-key`, so
  nothing is stored; that also drops the iPhone), a new Wi-Fi Direct address, discoverable
  again. Covered by a test.
- Accepted by the owner: an iPhone mirrors to the hub (picture on screen, Win + K drops the hub
  during the cast, Stop Mirroring returns it to waiting under a new name). M5 done.

## Fixed: Windows audio over Miracast, 2026-11-08

Owner: no sound from a Windows cast. The hub negotiates AAC 48 kHz stereo and audio packets
reach the demuxer (100-240 per 5 s while sound plays). Locally, a test stream with an AAC
track goes through the same elements to PipeWire, and its stream is linked to the speaker
output. The player now also counts `audio-out` (audio reaching pipewiresink), so the next
Windows cast shows where it stops.
- Live: audio-out counted ~236 per 5 s, but the cast's PipeWire stream stayed in "init" and
  the speaker output suspended. The only other stream was M5's waiting AirPlay player: linked,
  silent, holding the speakers suspended. Reproduced locally: a test stream alone runs the
  speakers; next to an idle L16 stream the speakers stay suspended though the stream "runs".
- Fix: the waiting AirPlay player is video only (the video is what must listen early, for the
  iPhone's one keyframe); AirPlay audio gets its own player for the length of the cast. Both
  paths also resample to a fixed 48 kHz stereo before pipewiresink. Checked headless: idle =
  no Cast Hub audio stream; AirPlay cast and a Windows-style cast both run the speakers.
- Owner: with sound back, Windows audio sounded like a cartoon mouse (fast, high). avdec_aac
  decodes planar (non-interleaved) audio; the pinned format had no layout, so pipewiresink got
  planar buffers and played them as interleaved: double speed, an octave up. The format now
  pins `layout=interleaved` (checked: pipewiresink's caps change from non-interleaved to
  interleaved). Pitch to be confirmed by ear on the next Windows cast.

## M4 locked (done), 2026-11-08

Owner: M4 is done and locked. Single receiver, every screen mirrors it; Windows casts play
picture and sound (audio fixes above).

## School Wi-Fi, stage 1: Cast Hub owns Wi-Fi Direct groups, 2026-11-08

Owner: at school the receiver shows up but casts don't connect. The school network keeps
devices apart (no device-to-device traffic), so the network path can't work there, and the
Wi-Fi Direct fallback crashed the Wi-Fi firmware. Every crash was Cast Hub re-authenticating as
the *client* of the PC's group after pairing: NetworkManager 1.58 asks with go_intent 7 and
Windows with 14, so the PC always owned the group.
- casthub-p2p now pairs with go_intent 15 and owns the group itself: a random /24 in 10/8 that
  doesn't overlap our Wi-Fi, the casthub-p2p firewall zone, and a small DHCP server
  (`discovery/dhcp.py`) that tells the hub the PC's address as soon as it leases it.
- NetworkManager leaves `p2p-*` interfaces alone (`/etc/NetworkManager/conf.d/90-casthub-p2p.conf`)
  and keeps managing normal Wi-Fi; the `casthub-wifi-direct` NM profile is gone.
- Tests: DHCP exchange (offer, ack, nak, renewal), the group subnet, Connect with go_intent 15,
  the group-owner sequence. Live test pending: a Windows cast over Wi-Fi Direct (role GO, no
  firmware crash), at home and then at school.
- Next (stage 2): route detection, the hub's own hotspot for iPhones, a loader before the
  connection instructions.
- Live test 1 (home): the network cast worked (`via mice`). The Wi-Fi Direct cast paired with
  us as group owner and no firmware crash, but the PC never joined: the group came up on a
  different channel from our Wi-Fi (two channels, the radio splitting its time), and formation
  timed out after 15 s. The group now always takes our Wi-Fi's channel (Connect `frequency`),
  which the hotspot stage needs anyway. Covered by a test.

## School Wi-Fi, stage 2: the hub's hotspot, 2026-11-08

Owner: the "same network, another router" test can't happen at home (the only other network is
school's). Hotspot decisions (SPEC D28): `mode = auto | always | off` (default always), internet
sharing as a setting (default off), a Wi-Fi Direct cast pauses the hotspot.
- casthub-p2p runs it (`discovery/hotspot.py`): a `casthub-ap` interface in AP mode through
  wpa_supplicant (no hostapd), on our Wi-Fi's channel; on a radar/DFS channel it leaves that
  network and runs alone. Its own /24, our DHCP server, firewall zone `casthub-hotspot`, a fresh
  name and password per start, the join code in `/run/casthub/hotspot-qr.txt` for the idle screen.
- Paused (QR hidden) during a Wi-Fi Direct cast and while the Wi-Fi interface is re-added for a
  new Wi-Fi Direct address; follows our Wi-Fi to a new channel; stops with the hub or the service.
- `share_internet`: firewalld policy `casthub-share` (NAT), given our Wi-Fi's zone at run time only.
- Tests: helpers, channel rules, each route, pause nesting, sharing, a failed start, config.
  Live test pending: iPhone scans the QR, joins, AirPlay over the hotspot; then a Windows cast
  over Wi-Fi Direct (hotspot pauses, comes back).
- Live test (home): the hotspot came up beside our Wi-Fi, a phone joined it (DHCP lease), an
  iPhone mirrored over AirPlay through the hotspot (owner had turned off its home Wi-Fi), and after the cast the hotspot paused for the new Wi-Fi Direct
  address and came back. No firmware crash. **Partially accepted by the owner.** Still open: a
  Windows cast over Wi-Fi Direct (channel fix + hotspot pausing), and the test at school.

## M6 done (kiosk session), 2026-11-08

Accepted by the owner: Display mode starts on the idle screens from the login screen, the busy
rule works, and Ctrl + Alt + Esc opens the leave page during a cast. Built during M3/M5 (Sway
kiosk, idle/busy screens, leave page); no new work was needed.

## School Wi-Fi, stage 3: instructions follow the route, 2026-11-08

- casthub-p2p sends the route (network / hotspot / hotspot-alone) to the hub, and again to any
  hub that connects later. The receiver's name, and with it the instructions, show only once both
  the name is findable and the route is known; in the instructions' place the screen says
  "Finding the best way to connect". No route within 20 s after the name (an older casthub-p2p):
  same-network instructions. No casthub-p2p at all: same network.
- Rows: same network: Apple "Control Centre › Screen Mirroring › NAME", Windows "Press Win + K".
  Hotspot: Apple "Scan the code, then Screen Mirroring › NAME", Windows unchanged (Wi-Fi Direct
  pauses the hotspot), and a row with the hotspot's name and password for laptops that can't scan
  ("Cast Hub Wi-Fi", or "Wi-Fi · offline" when the hub left the network). QR only on hotspot routes.
- Previews (local only) checked in light and dark. Tests: the name waits for the route, the
  join-code parsing, a later hub hears the route. Live test pending (owner, at school).

## M10 passthrough, stage 1: Cast Hub as a Miracast source, 2026-11-08

Owner decisions (SPEC D29): any cast in → Miracast out; the TV link takes the radio slot (no
hotspot or incoming Wi-Fi Direct meanwhile); TV only (our screens show a status page); leave page
plus a `[passthrough]` config spot (admin-fixed target or the user's choice), since Cast Hub is
heading for offices and lecture halls. Owner has a Miracast TV/projector; nothing tried yet.
- `rtsp/source.py`: the source side of the handshake (M1 and M3–M5 from us, M6/M7 answered,
  M16 keepalives, the sink's key-frame requests, teardown either way). `rtsp/wfd.py` reads a
  sink's formats and picks: the biggest mode up to 1080p, 30 fps preferred, CHP if offered; AAC
  48 kHz stereo, else LPCM.
- Tested over loopback against our own sink (`tests/test_source.py`).
- Next: stage 2, the video out (decode once, scale, VA-API encode, MPEG-TS over RTP).

## M10 passthrough, stage 2: the video out, 2026-11-08

- `media/tv.py`: one sender pipeline for as long as passthrough is on: intervideosrc /
  interaudiosrc → VA-API H.264 (CHP = High without B-frames, or CBP) + AAC → MPEG-TS (WFD PIDs
  0x1011 / 0x1100) → RTP to the TV. Fixed frame rate whatever comes in; black and silence between
  casts so the TV keeps its session; a key frame every second and on the TV's request.
- A cast's input is our usual Miracast or AirPlay receive path with `MediaSpec.tv` set: decoded
  once, scaled to the TV's mode with black borders, handed over in the same process. No windows,
  nothing on our speakers (TV only, D29).
- Live loopback test (skipped without VA-API): a test pattern sent like a PC → our input → the
  sender → a stand-in TV that demuxes it: ~30 fps 1280x720 H.264 + AAC during the cast, black
  after it. (A first stand-in TV saw nothing: it had no queues after tsdemux; the stream was fine.)
- Next: the TV process (sender + inputs, driven by the hub) and stage 3, finding and joining the
  TV over Wi-Fi Direct, which needs the owner's TV.

## M10 passthrough, stages 3-4: the TV link, setup and screens, 2026-11-08

- `media/tvproc.py`: one TV process for the whole passthrough session (sender + the current
  cast's input; the inter elements only work inside one process). `TvInput` looks like a player
  to the receiver; a Windows cast replaces a waiting AirPlay input, which is set up again after.
- Receivers: `set_tv()` sends casts to the TV process (AirPlay picture and sound in one input),
  no windows placed; `tv` tells each screen where its casts go.
- casthub-p2p TV mode: searches for Miracast displays (WFD sinks; PCs, which are sources, are
  left out), joins the chosen one as group owner (never as client: the firmware crash),
  advertises dual-role so Win + K still lists us, pauses the hotspot, refuses PC pairing, and
  reports the TV's address. The casthub-p2p zone opens TCP 7236 for the TV's RTSP.
- `hub/passthrough.py`: tv_on → our Miracast source on 7236 → the TV's handshake picks the mode
  → TV process → receivers. A lost TV is joined again while wanted. `[passthrough] mode =
  user | fixed | off` (+ `target`); fixed (an admin's TV) starts at hub start and locks the choice.
  The hub's control socket (`$XDG_RUNTIME_DIR/casthub-hub.sock`) takes the leave page's commands.
- Screens: "Passthrough setup" on the leave page opens a second view ("To a TV": displays found,
  search again, turn off; four rows like the main choices, Esc goes back). During a cast to the
  TV each screen shows the sender's name and "On <TV>" instead of the video.
- Checked on this machine end to end: the controller, our source on 7236, our own sink as the TV
  (1080p30 + AAC negotiated), the TV process with a test cast: 94 frames + audio in 3 s, then off
  with a clean teardown. Previews (local) light and dark. Tests for each part.
- Live test pending (owner): search finds the TV, it joins, a Windows and an iPhone cast show on it.

## M10 live test 1: the TV refused our channel; the panel gets passthrough setup, 2026-11-08

- Owner: the TV join showed "Connecting" but the TV never saw it. Log: the TV was found, then
  group negotiation failed at once, twice, with status 7, "no common channel". We pinned the
  group to our Wi-Fi's channel (5745 MHz, to keep PCs from timing out); this TV doesn't take it.
- Fix: on "no common channel" casthub-p2p asks once more without a channel, so wpa_supplicant
  picks one both sides list (the radio then shares its time between two channels). Any other
  failure, or a second one, tells the screens why ("Couldn't reach <TV> · <reason>"); failures
  are logged with their meaning. Tests for both.
- Owner: in the Display panel, "Close" closed at once, so passthrough couldn't be set up there.
  Now "Close" opens a short leave page: Close, Passthrough setup, Cancel (Esc also cancels).
  Ctrl+Alt+Esc still closes at once. Previews (local) light and dark; tests load the page and
  check both menus.
- Live test pending: the panel's menu, and the TV joining on a common channel.
- Live test 2: the retry worked. The TV took a 2.4 GHz channel, we became group owner and
  push-button pairing succeeded, but the group went away in the same second, before the TV
  joined it with the group's key (wpa_supplicant: "group formation failure"; a local
  disconnect, no kernel error). Earlier Windows tests also never got a client into our group,
  so this is our group-owner path, not the TV. Cause not known yet: next try runs with
  wpa_supplicant's debug log on, which names whatever removes the group.
- Fixed meanwhile: a formation failure left the screens on "Connecting" for ever and a dead
  group interface behind. casthub-p2p now ends the attempt, tells the screens why, and
  deletes the leftover interface; a group that fails during setup isn't served. Test added.
- Live test 3, with wpa_supplicant's debug log: right after push-button pairing wpa_supplicant
  logs "Removing interface p2p-…" (an outside request, no P2P removal reason). Cause, checked in
  NetworkManager's source: on every GroupStarted, NM's supplicant layer makes an object for the
  group interface; NM's Wi-Fi Direct device ignores groups it isn't activating (managed or not,
  so our drop-in can't stop it) and drops the object, and dropping it sends wpa_supplicant
  RemoveInterface. Any group NM didn't start dies within milliseconds. That is why no PC or TV
  ever got into a group of ours. NM has no setting to turn its Wi-Fi Direct handling off
  (only wpa_supplicant's p2p_disabled, which would turn ours off too). Design decision needed.
- Owner chose to patch NetworkManager ("if it is not a security risk"). NM's removal is a side
  effect of dropping its object, not a safeguard; only root may tell wpa_supplicant to start a
  group (its D-Bus policy). The patch is scoped: NM leaves foreign groups alone only while its
  Wi-Fi Direct device is unmanaged (our drop-in in /etc); managed devices keep stock behaviour.
  Built in a throwaway Fedora container (`tools/build-nm.sh`, release `.casthub1`), installed
  by `tools/install-nm.sh` (snapshot first). No version lock, so a Fedora update wins; then
  casthub-p2p logs that NM is unpatched. Live test pending.
- Live test 4, with the patched NetworkManager: the group stayed up. NM logged "device is
  unmanaged; leaving the group to whoever started it", the TV joined (4-way handshake done)
  and took 10.x.x.10 from our DHCP. Then nothing: the hub's source listened on TCP 7236 (zone
  open), but the TV never connected; a 25 s capture on the group link saw zero packets from it.
- Changes, as phones and PCs do when they join a TV: a push-button provision-discovery request
  before the group negotiation (it is what makes a TV expect a session), and our WFD info says
  "source" while joining instead of dual-role (dual-role again once the TV's session is up,
  `tv_ready` from the hub, so Win + K lists us during passthrough). Tests added.
- Live test 5: the TV answered the pairing request (PROV-DISC-PBC-RESP), joined, took an
  address, and again never connected. Our DHCP gave it no gateway; TVs find their source (the
  group owner) at the gateway. The lease now includes us as the router, in TV mode only (a PC
  would send its internet through us). Next try runs with a capture that starts before the
  join (the first one began a minute late and would have missed the TV's first attempts).
- Live test 6: **connected**. With us as its gateway the TV opened RTSP right after joining
  (LIVE555 sink, 1080p30 CBP + AAC negotiated) and RTP flowed (~60 000 packets). The TV stayed
  black: between casts we sent black, and an iPhone cast never reached it. The cast's hand-over
  (videorate, then a clock-synced intervideosink) got ~1500 frames at once from videorate
  filling the gap since the waiting AirPlay player started, dropped them all as late and then
  stalled (0 frames). The hand-over is now unsynced with no videorate (the sender repeats
  frames at the TV's rate by itself).
- Owner: the TV should show a waiting screen and "connecting" with the dots, but no connection
  instructions (those only on Cast Hub's own screens). `media/tvcard.py` renders the Display
  screen offscreen in a new tvCard mode (clock, name, status and dots; no ways to connect, QR or
  Log out) at the TV's size, as five PNG frames (the dots' steps), redrawn on each state change
  and minute. The sender loops them between casts and while a cast connects, until its first
  frame. Checked: renders light and dark at 1080p (previews local), the card reaches the TV
  channel at 30 fps. Tests for each part.
- Live test 7: still black, with cast frames reaching the sender. A capture showed the TV's
  RTCP to our ports 7237-7238 refused by the group zone. Changes: the zone opens those ports and
  the RTSP source logs the TV's receiver reports (loss, jitter) every 5 s; the transport stream
  now follows WFD more closely (program map on PID 0x0100, access unit delimiters, RTP payload
  type 33) at 5 Mbit/s for the 2.4 GHz link. A local loopback decodes the waiting card from it.
- Owner: after picking a TV, getting back to the phone instructions took too many steps. Picking
  a TV (or turning it off) now closes the leave page on every screen. The Display screen's status
  line says "Connecting to <TV>…", "Couldn't reach <TV>", then "Casts go to <TV>", beside the
  ways to connect a phone (not on the TV's own card). Tests and a preview (tv-joining).
- Live test 8: session up, cast frames at the sender, TV black. A 15 s capture: ~500 RTP
  packets/s to the TV, and every few seconds a 44-byte RTCP packet back that the source didn't
  log, i.e. a receiver report with no blocks: the TV counted none of our packets. SETUP told it
  our RTP comes from server_port 7237, but the sender used a random port. The sender now binds
  7237 (the source's RTCP listener keeps 7238 only), and an empty report is logged as "the TV
  reports it has received nothing from us".
- Owner: return to the Display screen only once the TV is connected. Picking a TV now keeps the
  setup page open while it connects; it closes when passthrough is on, and stays on a failure.
- Live test 9: **the TV shows the picture.** With RTP from server_port 7237 the TV took the
  stream: its waiting screen between casts and an iPhone cast passed through to it.
- Owner: Close, Cancel and Exit side by side confused. Panel names now: "Menu" in the bar opens
  the page; "Quit panel", "Passthrough setup", "Resume"; in the TV view "Stop passthrough".
