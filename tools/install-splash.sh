#!/bin/bash
# Cast Hub boot splash: the signature writes itself on a dark screen (design page 2).
# Replaces Fedora's `bgrt` Plymouth theme, which re-shows the firmware's logo (the
# coreboot rabbit on MrChromebox firmware).
#   run through the root helper:  tools/asroot.py --why "..." -- tools/install-splash.sh
#
# Changes: snapshot of / · /usr/share/plymouth/themes/casthub · default Plymouth theme
#          · rebuilt initramfs for the running kernel (the old one is kept as *.casthub-backup)
# Undo:    sudo plymouth-set-default-theme bgrt -R
set -euo pipefail

[ "$EUID" -eq 0 ] || { echo "run as root (through tools/asroot.py)" >&2; exit 1; }
user=${SUDO_USER:?run through sudo or the root helper so the build can run as you}
repo=$(cd "$(dirname "$0")/.." && pwd)
kver=$(uname -r)
img=/boot/initramfs-$kver.img
theme=/usr/share/plymouth/themes/casthub
build=$repo/local/splash-build
step() { printf '\n== %s\n' "$*"; }

step "Snapshot"
"$repo/tools/snapshot.sh" pre-splash

step "Screen size"
w=1920 h=1080
for c in /sys/class/drm/card*-*; do
  if [ "$(cat "$c/status" 2>/dev/null)" = connected ] && [ -s "$c/modes" ]; then
    IFS=x read -r w h < <(head -1 "$c/modes"); break
  fi
done
echo "${w}x${h}"

step "Build frames (as $user, not root)"
runuser -u "$user" -- python3 -I "$repo/brand/splash.py" --out "$build" --width "$w" --height "$h" --host "$(hostname -s)"

step "Back up the current initramfs"
if [ -e "$img.casthub-backup" ]; then
  echo "backup already exists: $img.casthub-backup"
else
  cp -a "$img" "$img.casthub-backup"
  echo "saved $img.casthub-backup"
fi

step "Install theme"
rm -rf "$theme"
install -d "$theme"
install -m644 "$build"/* "$theme/"
restorecon -R "$theme" 2>/dev/null || true

step "Activate and rebuild the initramfs (takes a minute)"
plymouth-set-default-theme casthub -R

step "Verify"
echo "default theme: $(plymouth-set-default-theme)"
n=$(lsinitrd "$img" | grep -c 'plymouth/themes/casthub/' || true)
echo "files from the theme in the initramfs: $n"
[ "$n" -gt 0 ] || { echo "theme missing from initramfs" >&2; exit 1; }

cat <<MSG

Done. The signature splash shows on the next boot, shutdown and restart.
Undo: sudo plymouth-set-default-theme bgrt -R
      (or restore $img.casthub-backup)
MSG
