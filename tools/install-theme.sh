#!/bin/bash
# M8: put the Cast Hub look on every surface from brand/tokens.toml.
#   tools/asroot.py --why "..." -- tools/install-theme.sh [--splash]
#
# Edit brand/tokens.toml, run `python3 brand/build.py`, commit, then run this. With
# --splash it also rebuilds the boot splash (tools/install-splash.sh, rebuilds initramfs).
#
# Changes: snapshot of / · /usr/share/fonts/casthub · /usr/share/sddm/themes/casthub
#   · /usr/share/color-schemes/CastHub*.colors · /usr/share/plasma/{look-and-feel,shells,plasmoids,desktoptheme}/…casthub*
#   · /usr/share/kwin/{decorations,scripts}/org.casthub.*
#   · /usr/lib/casthub + /usr/libexec/casthub (Display UI) · /usr/share/wallpapers/CastHub · /usr/share/konsole/CastHub* · /etc/profile.d/casthub-terminal.sh
#   · /etc/xdg/{kdeglobals,konsolerc,kwinrc,plasmashellrc} (keys set with kwriteconfig6; backups kept)
# Users who never picked a global theme get Cast Hub at their next login. Anyone who
# picked one in System Settings keeps it. Undo: docs/RECOVERY.md, "Desktop theme".
set -euo pipefail

[ "$EUID" -eq 0 ] || { echo "run as root (through tools/asroot.py)" >&2; exit 1; }
user=${SUDO_USER:?run through sudo or the root helper so the builds can run as you}
repo=$(cd "$(dirname "$0")/.." && pwd)
step() { printf '\n== %s\n' "$*"; }
# shellcheck source=lib/greeter.sh
. "$repo/tools/lib/greeter.sh"
# shellcheck source=lib/display.sh
. "$repo/tools/lib/display.sh"

step "Snapshot"
"$repo/tools/snapshot.sh" pre-theme

step "Generated brand files are current"
python3 -I "$repo/brand/build.py" --check

step "Screen size"
w=1920 h=1080
for c in /sys/class/drm/card*-*; do
  if [ "$(cat "$c/status" 2>/dev/null)" = connected ] && [ -s "$c/modes" ]; then
    IFS=x read -r w h < <(head -1 "$c/modes"); break
  fi
done
echo "${w}x${h}"

step "Fonts"
install -d /usr/share/fonts/casthub
install -m644 "$repo"/brand/fonts/*.ttf "$repo"/brand/fonts/OFL-*.txt /usr/share/fonts/casthub/
fc-cache -f /usr/share/fonts/casthub >/dev/null

step "Login screen"
install_greeter "$repo"

step "Display screens and PIN page"
install_display "$repo"

step "Plasma colour schemes"
install -m644 "$repo"/shell/colors/CastHub*.colors /usr/share/color-schemes/

step "Desktop: global themes, shell, top bar, desktop view, decoration, views script"
"$repo/tools/lib/install-shell-user.sh" "$repo" /usr/share
# Location and coordinates for the desktop footer, from /etc/casthub/casthub.toml.
python3 -I - "$repo" /usr/share/plasma/plasmoids/org.casthub.desktopview/contents/config/main.xml <<'PY'
import sys
from xml.sax.saxutils import escape
sys.path.insert(0, sys.argv[1])
from casthub import config
try:
    c = config.load()
    loc, coords = c.location, c.coordinates()
except (OSError, config.ConfigError):
    loc = coords = ""
path = sys.argv[2]
text = open(path, encoding="utf-8").read()
text = text.replace("@LOCATION@", escape(loc)).replace("@COORDINATES@", escape(coords))
open(path, "w", encoding="utf-8").write(text)
print(f"footer: {loc or '(no location set)'}")
PY

step "Wallpaper (built as $user, not root)"
build=$repo/local/wallpaper-build
runuser -u "$user" -- python3 -I "$repo/brand/wallpaper.py" --out "$build" --size "${w}x${h}"
rm -rf /usr/share/wallpapers/CastHub
cp -r --no-preserve=ownership "$build" /usr/share/wallpapers/CastHub
chmod -R u=rwX,go=rX /usr/share/wallpapers/CastHub
# Lock / wake-from-sleep background: black, grid, signature.
runuser -u "$user" -- python3 -I "$repo/brand/wallpaper.py" --lock --out "$build-lock" --size "${w}x${h}"
rm -rf /usr/share/wallpapers/CastHubLock
cp -r --no-preserve=ownership "$build-lock" /usr/share/wallpapers/CastHubLock
chmod -R u=rwX,go=rX /usr/share/wallpapers/CastHubLock

step "Konsole (terminal is always dark)"
install -m644 "$repo/modes/console/CastHub.colorscheme" "$repo/modes/workstation/CastHub.profile" \
  "$repo/modes/console/CastHubConsole.profile" /usr/share/konsole/
install -m644 "$repo/system/profile.d/casthub-terminal.sh" /etc/profile.d/casthub-terminal.sh

step "System-wide defaults in /etc/xdg"
for f in kdeglobals konsolerc kwinrc plasmashellrc kscreenlockerrc; do
  [ -e "/etc/xdg/$f" ] && [ ! -e "/etc/xdg/$f.casthub-backup" ] && cp -a "/etc/xdg/$f" "/etc/xdg/$f.casthub-backup"
done
kw() { kwriteconfig6 --file "/etc/xdg/$1" --group "$2" --key "$3" "$4"; }
kw kdeglobals KDE LookAndFeelPackage org.casthub.light
kw kdeglobals KDE DefaultLightLookAndFeel org.casthub.light
kw kdeglobals KDE DefaultDarkLookAndFeel org.casthub.dark
kw kdeglobals General ColorScheme CastHubLight
kw kdeglobals General font "Instrument Sans,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
kw kdeglobals General menuFont "Instrument Sans,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
kw kdeglobals General toolBarFont "Instrument Sans,9,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
kw kdeglobals General smallestReadableFont "Instrument Sans,8,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
kw kdeglobals General fixed "DM Mono,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
kw kdeglobals WM activeFont "Instrument Sans,10,-1,5,600,0,0,0,0,0,0,0,0,0,0,1"
kw konsolerc "Desktop Entry" DefaultProfile CastHub.profile
# Shell package, views script, decoration, Meta key, Konsole chrome (SPEC D17).
for f in konsolerc kwinrc plasmashellrc kscreenlockerrc; do python3 -I "$repo/tools/lib/merge-ini.py" "$repo/system/xdg/$f" "/etc/xdg/$f"; done
chmod 644 /etc/xdg/kdeglobals /etc/xdg/konsolerc /etc/xdg/kwinrc /etc/xdg/plasmashellrc /etc/xdg/kscreenlockerrc
cat /etc/xdg/kdeglobals

restorecon -R /usr/lib/casthub /usr/libexec/casthub /etc/casthub /usr/share/casthub /usr/share/fonts/casthub /usr/share/sddm/themes/casthub /usr/share/color-schemes \
  /usr/share/plasma /usr/share/kwin /usr/share/wallpapers/CastHub /usr/share/wallpapers/CastHubLock /usr/share/konsole /etc/xdg 2>/dev/null || true

if [ "${1:-}" = "--splash" ]; then
  step "Boot splash"
  "$repo/tools/install-splash.sh"
fi

step "Done"
cat <<MSG
Installed. New logins get the Cast Hub desktop. A user who already logged in before keeps
their old theme defaults until they run, as themselves (no root needed):
  tools/adopt-desktop.sh
MSG
