#!/bin/bash
# M8: put the Cast Hub look on every surface from brand/tokens.toml.
#   tools/asroot.py --why "..." -- tools/install-theme.sh [--splash]
#
# Edit brand/tokens.toml, run `python3 brand/build.py`, commit, then run this. With
# --splash it also rebuilds the boot splash (tools/install-splash.sh, rebuilds initramfs).
#
# Changes: snapshot of / · /usr/share/fonts/casthub · /usr/share/sddm/themes/casthub
#   · /usr/share/color-schemes/CastHub*.colors · /usr/share/plasma/look-and-feel/org.casthub.*
#   · /usr/lib/casthub + /usr/libexec/casthub (Display UI) · /usr/share/wallpapers/CastHub · /usr/share/konsole/CastHub* · /etc/profile.d/casthub-terminal.sh
#   · /etc/xdg/kdeglobals and /etc/xdg/konsolerc (keys set with kwriteconfig6; backups kept)
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

step "Plasma global themes (light, dark)"
for v in light dark; do
  src=$repo/shell/lookandfeel/org.casthub.$v dst=/usr/share/plasma/look-and-feel/org.casthub.$v
  rm -rf "$dst"
  install -d "$dst/contents/splash/uikit"
  install -m644 "$src/metadata.json" "$dst/"
  install -m644 "$src/contents/defaults" "$dst/contents/"
  install -m644 "$src/contents/splash/Splash.qml" "$dst/contents/splash/"
  install -m644 "$repo"/uikit/*.qml "$repo"/uikit/qmldir "$dst/contents/splash/uikit/"
done

step "Wallpaper (built as $user, not root)"
build=$repo/local/wallpaper-build
runuser -u "$user" -- python3 -I "$repo/brand/wallpaper.py" --out "$build" --size "${w}x${h}"
rm -rf /usr/share/wallpapers/CastHub
cp -r --no-preserve=ownership "$build" /usr/share/wallpapers/CastHub
chmod -R u=rwX,go=rX /usr/share/wallpapers/CastHub

step "Konsole (terminal is always dark)"
install -m644 "$repo/modes/console/CastHub.colorscheme" "$repo/modes/workstation/CastHub.profile" \
  "$repo/modes/console/CastHubConsole.profile" /usr/share/konsole/
install -m644 "$repo/system/profile.d/casthub-terminal.sh" /etc/profile.d/casthub-terminal.sh

step "System-wide defaults in /etc/xdg"
for f in kdeglobals konsolerc; do
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
chmod 644 /etc/xdg/kdeglobals /etc/xdg/konsolerc
cat /etc/xdg/kdeglobals

restorecon -R /usr/lib/casthub /usr/libexec/casthub /etc/casthub /usr/share/casthub /usr/share/fonts/casthub /usr/share/sddm/themes/casthub /usr/share/color-schemes \
  /usr/share/plasma/look-and-feel /usr/share/wallpapers/CastHub /usr/share/konsole /etc/xdg 2>/dev/null || true

if [ "${1:-}" = "--splash" ]; then
  step "Boot splash"
  "$repo/tools/install-splash.sh"
fi

step "Done"
cat <<MSG
Installed. New logins pick up the Cast Hub theme. For a user who already has a desktop,
set the wallpaper once as that user (no root needed):
  plasma-apply-wallpaperimage /usr/share/wallpapers/CastHub
MSG
