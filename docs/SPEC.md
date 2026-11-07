# Cast Hub: Design and Build Spec

Nov 7, 2026 · Torsten

> **Read "Decisions" first.** This spec was written before the design mockups existed.
> Where the two disagree, the decisions below and the mockups in `design/` win.

## Decisions (Nov 7, 2026, supersede the text below)

| # | Topic | Decision |
|---|-------|----------|
| D1 | Design source | `design/OS Embedded Device — Design Concepts.pdf` is the single set of design drawings. Screens it doesn't show (busy, PIN overlay, etc.) are designed in the same style. The five-separate-PDFs plan is dropped. |
| D2 | Brand values | Taken from the mockups; cross-checked against the brand page on the Torsten Young website (/en/brand). Accent `#7E76D0`, paper `#E6E6E6`, night `#161616`, ink `#0B0B0B`. Fonts: Bricolage Grotesque (display/clock), Instrument Sans (text), DM Mono (labels), Mrs Saint Delafield (signature only). |
| D3 | Themes | The OS has a light and a dark theme (mockup pages alternate light/dark). The terminal is always dark. The boot splash is always dark. |
| D4 | Boot splash | The "Torsten Young" signature writes itself the way it does on the website: each letter's outline is stroked over 0.55 s, letters start 0.2 s apart, each fill fades in 0.4 s later over 0.35 s while its stroke fades out, then the accent flourish draws last over 0.9 s (round caps). Glyphs come from Mrs Saint Delafield, converted to paths at build time; frames are pre-rendered for Plymouth. |
| D5 | Modes | Three modes, not two: **Workstation** (desktop, sign in), **Display** (Cast Hub receiver, no sign-in), **Console** (maintenance shell, sign in, admin accounts only, sudo available). |
| D6 | Login manager | **SDDM** with a custom QML theme. Fedora 45's Plasma Login Manager compiles its greeter into the binary and cannot be themed, so it is replaced. |
| D7 | Switcher | The login screen *is* the mode switcher. In Display mode, Ctrl + Alt + Esc + PIN opens a small overlay that leaves display mode (back to the login screen) or restarts/shuts down. No separate switcher app elsewhere. |
| D8 | Desktop | Follows the mockups (top bar with Console / Files / Desktop tabs, today panel, system meters, custom file views), not the "bottom dock" plan. Plasma remains the underlying engine where it helps; the visible shell is custom. |
| D9 | Miracast | M2 connection tests are done (owner). We build our own Python Miracast receiver; lazycast is a reference only. |
| D10 | LAN switch page | Protected by the switcher PIN. |
| D11 | Repo | Public on GitHub. `docs/SPEC.pdf` is never committed (kept in `local/`); this Markdown file is the tracked spec. Commits are authored by the owner with Claude as co-author. |
| D12 | Status colours | Slightly pastel success/warning/error. Shown as chips: ink text on the pastel. Where status must be *text* (Plasma), it is deepened just enough for 4.5:1. The terminal shows status the same way (ink on pastel). |
| D13 | Display QR | The QR code on the Display idle screen joins the Cast Hub hotspot and is shown only when the hotspot is on. |
| D14 | Desktop build | M8 themes Plasma (light/dark) for every user. M9 builds the custom shell on top of Plasma (KWin, Wi-Fi, notifications and lock screen stay Plasma's). Every control must either have a place in the mockup layout or live behind a settings window; if that turns out impossible on Plasma, switch to a fully custom shell. |
| D15 | Console and Files tabs | Real Konsole and Dolphin windows, styled to match and docked into the panel beside Today. |
| D16 | Firmware logo | The coreboot logo shown before Linux starts stays for now (changing it needs a firmware flash); to be revisited. |

---

Cast Hub turns the PUJJOTEEN Chromebook (i3-N305, 8 GB RAM, 64 GB eMMC, Fedora KDE Plasma
on MrChromebox firmware) into a branded machine with two modes: a normal desktop, and a
login-free wireless display that Windows, Mac and iPhone users can cast to with nothing
installed. Windows casting uses a custom Miracast receiver built in this repo; Apple casting
uses UxPlay, an existing AirPlay receiver. This doc is both the design record and the brief to
hand Claude Code when starting the repo.

## Rule: nothing from this machine goes to GitHub

The repo holds code, templates and docs only. Nothing read from, generated on, or describing
this specific machine, its network or the devices that cast to it is ever committed or pushed.

Never commit:

- **Real config:** `/etc/casthub/casthub.toml`, the switcher PIN or its hash, Wi-Fi and hotspot
  names and passwords.
- **Hardware identifiers:** HWIDs, serial numbers, MAC addresses, UUIDs, receiver
  `container_id` GUIDs, firmware backups.
- **Network details:** IP addresses, hostnames, SSIDs, and names of PCs or phones that cast.
- **Diagnostics:** journal or log dumps, `iw list` / `lspci` / `vainfo` output, Wireshark
  captures, screenshots or photos of the screen.

How the repo enforces it:

- Only `casthub.example.toml`, with placeholder values, is tracked. The real config lives
  outside the repo.
- Probe scripts write to `local/`, which is git-ignored along with `*.pcap`, `*.log`, `*.rom`,
  `*.bin` and screenshots.
- Git hooks (`.githooks/pre-commit`, `commit-msg`, `pre-push`) block MAC addresses, IP
  addresses (documentation ranges excepted), GUIDs outside test fixtures, literal secrets, and
  any string listed in `local/denylist.txt`, plus this machine's own identifiers detected at
  run time.
- Test fixtures use made-up values, never recorded real traffic.
- Claude Code never runs `git push` without asking, and shows the list of changed files first.

## The modes

One Fedora install, several login sessions. A mode is a session file plus a systemd target, so
modes never need a reinstall or a second OS. *(See D5: three modes.)*

| Mode | What runs | How it starts |
|------|-----------|---------------|
| Workstation (Desktop) | Custom shell on Plasma, normal apps, user logs in | Pick the tile on the login screen and sign in |
| Display (Cast Hub) | Sway kiosk, Miracast receivers, UxPlay, hotspot, idle screens | Pick the tile on the login screen; no password needed |
| Console | Fullscreen branded terminal, admin accounts only | Pick the tile and sign in as an admin (wheel) user |

- Each mode lives in its own folder: `modes/<name>/` with `session.desktop`, `<name>.target`
  and its services.
- Switching ends the session and returns to the login screen with the chosen mode preselected.

## Custom login screen and desktop UI

### Login screen (the mode picker)

A custom SDDM theme written in QML (D6). SDDM already lists sessions and starts them, so the
theme only has to present them as modes.

- **Layout:** as in the mockups: wordmark, big clock and date on the left, mode list on the
  right, user and password below, Restart / Shut down bottom right.
- **Workstation / Console:** show the user and password field, then start the session.
  Console is only offered to, and only starts for, members of `wheel`.
- **Display:** one press, no password. It logs in the unprivileged `casthub` user, allowed by a
  PAM rule (`pam_succeed_if` on a `nopasswdlogin` group) that covers only that account.
- **Keyboard-first:** arrow keys / Tab move between modes, Enter picks one.
- **Return path:** leaving a mode lands back here with that mode preselected.
- **Fallback:** if the theme fails to load, SDDM falls back to its default theme.

### Desktop UI

*(Superseded by D8.)* Originally: keep KDE Plasma and retheme it (stage 1), then add custom
widgets: launcher, mode button, cast status (stage 2). Now: build the shell shown in the
mockups. Done when a fresh user account sees the branded desktop with no manual setup.

### Shared component kit (`uikit/`)

- One QML module with brand tokens (colours, type scale, spacing, radii, motion) and
  components: tile, button, PIN pad, toast, status chip, QR card.
- Used by the login screen, the desktop shell, the Display idle and busy screens, and the PIN
  overlay, so every surface looks like one product.
- `brand/` holds the source values; a generator writes the QML token file, the Plasma colour
  scheme, the Plymouth theme and the SDDM theme config from them.

## Branding

One folder, `brand/`, is the only place colours, fonts and logos are defined. Every surface is
generated from it.

- The mockups in `design/` are the source of truth. Tokens, components and layouts are built to
  match them. Anything that had to be guessed is listed for the owner to confirm.
- Each built screen is screenshotted beside its mockup page for review. Those screenshots stay in
  `local/`, never in the repo.

| File | Holds |
|------|-------|
| `tokens.toml` | Colours (light + dark), font families, type sizes, radius, spacing, motion |
| `logo.svg`, `logo-mark.svg` | Wordmark and the small square mark |
| `signature.svg` | Signature glyph paths + flourish (generated) |
| `fonts/` | Font files (all SIL OFL, bundling allowed) |

| Surface | Generated by `brand/build.py` | Installed to |
|---------|-------------------------------|--------------|
| Boot splash | Plymouth theme (always dark) | `/usr/share/plymouth/themes/casthub/` |
| Login screen | SDDM theme with tokens | `/usr/share/sddm/themes/casthub/` |
| Desktop | Colour schemes, shell theme, wallpaper | per user / system |
| Display screens, PIN overlay | `uikit/Tokens.qml` | bundled with `uikit/` |

To change the look: edit `tokens.toml`, run `brand/build.py`, then `tools/install-theme.sh`.

## Login-free casting

In Display mode nobody logs in: the machine shows branded idle screens and accepts casts, like
a smart TV.

- **No password:** a dedicated, unprivileged `casthub` user; a PAM rule allows this for that
  one user only. Optionally the machine starts in Display mode at boot.
- **Idle screen per display:** clock, date, receiver name, instructions per platform
  (AirPlay / Miracast / QR), hotspot QR code.
- **Busy rule:** one active stream per screen. A second sender gets a branded "busy" message,
  or replaces the first if `busy_policy = "replace"`.
- **Escape:** Ctrl + Alt + Esc is bound in Sway itself, and apps can't inhibit compositor
  shortcuts, so a fullscreen stream can't swallow it. It opens the PIN overlay (D7).
- **Fallbacks:** long-press in a screen corner (touchscreen only); Ctrl + Alt + F3 for a text
  console; a small PIN-protected web page on the LAN (D10).

## Windows: custom Miracast receiver

Windows users press Win + K, pick the screen, and choose Extend or Duplicate with Win + P.
Nothing is installed on the PC. This repo builds the receiver in Python (D9), using lazycast
and MiracleCast as references.

Two transports, one receiver:

- **Miracast over Infrastructure (MS-MICE):** PC and Chromebook on the same network
  (Windows 10 v1809+). Discovery still needs Wi-Fi on the PC.
- **Wi-Fi Direct:** fallback when the PC isn't on the same network; also used for discovery,
  so the radio must support P2P.

Connection sequence, per receiver:

1. The receiver advertises itself: Wi-Fi Direct display IEs with Microsoft's "supports
   infrastructure" vendor extension, plus an mDNS record `<name>._display._tcp.local` on its
   port with a `container_id` GUID.
2. The PC connects to the receiver's TCP port (7250 by default) and sends `SOURCE_READY` with
   its friendly name, RTSP port and source ID.
3. The receiver connects back to the PC's RTSP port and runs the Wi-Fi Display handshake
   (M1–M7), advertising H.264 video at its screen's resolution and AAC audio.
4. The PC streams MPEG-TS over RTP/UDP. GStreamer decodes it on the Intel GPU and shows it
   fullscreen on the assigned screen.
5. `STOP_PROJECTION`, a lost keepalive (M16) or the PC disconnecting returns the screen to idle.

Out of scope for v1: DTLS media encryption, PIN pairing, and sending input back to the PC.

## Apple: AirPlay via UxPlay

- One UxPlay process per screen, each with its own name (`-n Brand-2`), its own ports, and its
  window pinned to that screen by Sway.
- The hub starts and stops these processes as monitors are plugged in or removed.
- Senders must be on the same network or the Cast Hub hotspot.
- Known limits: iPhones mirror only; portrait phones show with side bars; DRM apps usually
  show black.

## Screens and layout modes

Every connected screen is its own cast target. How Miracast maps onto screens is a config
option, because one Wi-Fi radio may only present one Miracast receiver.

| Layout | Miracast | AirPlay | Use when |
|--------|----------|---------|----------|
| `per_screen` | One receiver per screen on one radio, ports 7250, 7251… | One UxPlay per screen | The multi-port test passes |
| `per_radio` | One receiver per Wi-Fi adapter, each mapped to a screen | One UxPlay per screen | Multi-port fails; add USB Wi-Fi adapters |
| `mirror_all` | One receiver, decoded once, shown on every screen | One UxPlay, mirrored | Everything else fails |

Config lives at `/etc/casthub/casthub.toml`; see `casthub.example.toml` for the format.

## Build spec

Build and test on the Chromebook itself: the receivers depend on its Wi-Fi chip, GPU and
screens.

| Area | Choice |
|------|--------|
| OS | Fedora KDE Plasma (current stable), RPM Fusion codecs, intel-media-driver |
| Language | Python 3.12+, asyncio throughout, type hints, ruff + pytest |
| Media | GStreamer via PyGObject: `udpsrc ! rtpmp2tdepay ! tsdemux ! h264parse ! vah264dec ! waylandsink`, audio `aacparse ! avdec_aac ! pipewiresink` |
| Wi-Fi Direct | wpa_supplicant P2P over D-Bus (`dbus-fast`) |
| mDNS | Avahi over D-Bus |
| Kiosk | Sway, controlled over its IPC socket (`i3ipc`) |
| AirPlay | UxPlay, one subprocess per screen |
| Login | SDDM with a custom QML theme (D6) |
| Services | systemd units and targets; config in TOML (`tomllib`) |

### Repo layout

```
casthub/
  CLAUDE.md                 # working rules for Claude Code
  .gitignore                # local/, captures, logs, firmware, screenshots
  .githooks/                # privacy scanner + pre-commit / commit-msg / pre-push
  casthub.example.toml      # placeholder config; the real one is never tracked
  docs/SPEC.md              # this document
  docs/LOG.md               # milestone notes
  design/                   # PDF mockups: the visual source of truth
  casthub/
    config.py               # load + validate casthub.toml
    hub/                    # screen manager: Sway outputs → receivers
    discovery/p2p.py        # Wi-Fi Direct device + WFD/MS-MICE IEs
    discovery/mdns.py       # _display._tcp records per receiver
    mice/                   # TCP listener, MS-MICE TLV parser
    rtsp/                   # WFD RTSP client, M1–M7 + M16 keepalive
    media/                  # GStreamer pipelines, mirror_all tee
    airplay/                # UxPlay process manager
    ui/                     # idle / busy / connecting screens (QML)
    switcher/               # PIN overlay (QML)
  uikit/                    # shared QML components + generated Tokens.qml
  greeter/                  # SDDM theme: the mode-picker login screen
  shell/                    # desktop shell
  modes/workstation/        # session file + target
  modes/display/            # session, target, sway config, leave script
  modes/console/            # session, target, sway config, Konsole profile
  modes/casthub-session     # session gatekeeper: who may start which mode
  brand/                    # tokens.toml, logos, signature, build.py
  tools/                    # probe scripts (write to local/ only)
  tests/                    # unit tests with made-up fixture values
  local/                    # git-ignored
```

### Component contracts

- **hub** watches Sway output events and keeps one Receiver per screen (per layout mode).
  Receiver = name, output, Miracast port, UxPlay process, state:
  `idle | connecting | streaming | busy`.
- **mice** parses MS-MICE TLVs (FRIENDLY_NAME, RTSP_PORT, SOURCE_ID) and emits
  `SourceReady(addr, rtsp_port, source_id)` / `StopProjection`.
- **rtsp** takes a `SourceReady`, runs the handshake, and returns the negotiated video format
  and RTP port, or a typed error.
- **media** starts and stops a pipeline for one receiver on one output and reports first-frame
  and errors.
- **ui** renders state per screen; it never blocks the receiver.
- Every component logs structured events to the journal.

## Milestones and acceptance criteria

M1 (Fedora installed) and M2 (Miracast go/no-go connection tests) are done.

| # | Milestone | Done when |
|---|-----------|-----------|
| M7 | Login screen and modes | SDDM theme with Workstation, Display and Console; Display starts with no password; Console only for admins; leaving a mode returns to the login screen |
| M8 | Branding and graphics | Tokens extracted and confirmed; boot splash, login, desktop, Display screens and PIN overlay match the mockups; one colour change in `brand/tokens.toml` updates all of them after one rebuild |
| M3 | Repo skeleton + receiver | Privacy guardrails *(done)*; config loader, hub, MICE listener and RTSP handshake; one receiver on 7250 shows a Windows cast fullscreen |
| M4 | Multi-port test | Two mDNS receivers (7250, 7251): Win + K lists both and a cast to 7251 plays; layout mode chosen from the result |
| M5 | AirPlay | One UxPlay per screen; an iPhone mirrors to a named screen |
| M6 | Kiosk session | Display mode boots to idle screens; busy rule works; Ctrl + Alt + Esc + PIN opens the overlay during a stream |
| M9 | Desktop shell | The mockup desktop (top bar, Console / Files / Desktop, today panel, meters) installed for a fresh user with no manual setup |

## Constraints and open questions

- **Decode budget:** the i3-N305's GPU should handle 2–3 simultaneous 1080p H.264 streams;
  measure before promising more screens.
- **Screens:** one USB-C port, so monitors, charger and any USB Wi-Fi adapter share one dock.
- **Wi-Fi chip:** needs P2P-client/P2P-GO for Miracast discovery and AP mode for the hotspot.
- **Open:** does Win + K list several receivers from one radio? (M4)
- **Open:** does Windows honour a non-7250 port in the mDNS record? (M4)
- **Open:** Windows appears to project to one wireless display at a time; this design assumes
  one screen per sending device.

## Sources

- MS-MICE: Miracast over Infrastructure (Microsoft Open Specifications)
- Microsoft: Implementing wireless projection over an existing Wi-Fi network
- lazycast: a simple wireless display receiver
- MiracleCast / Mirai: Miracast daemon and CLI for Linux
- Torsten Young website, brand page (/en/brand): fonts, colours, signature animation
