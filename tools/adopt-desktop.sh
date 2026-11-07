#!/bin/bash
# M9: move an account that logged in before the Cast Hub desktop was installed onto it.
# Run as that user (no root):  tools/adopt-desktop.sh
#
# New accounts need none of this. Plasma regenerates ~/.config/kdedefaults from the global
# theme only when the theme's name changes, so an account already on org.casthub.light still
# has the old defaults (Breeze window frames). This backs up the files involved to
# local/desktop-backup-<time>/ and drops the marker, so the next login rebuilds them.
# The old Plasma layout (plasma-org.kde.plasma.desktop-appletsrc) is left as it was.
# Undo: docs/RECOVERY.md, "Desktop".
set -euo pipefail
[ "$EUID" -ne 0 ] || { echo "run as the desktop user, not root" >&2; exit 1; }
repo=$(cd "$(dirname "$0")/.." && pwd)
cfg=${XDG_CONFIG_HOME:-$HOME/.config}
[ -d /usr/share/plasma/shells/org.casthub.desktop ] || { echo "install first: tools/install-theme.sh (as root)" >&2; exit 1; }

backup=$repo/local/desktop-backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup"
for f in kdedefaults kdeglobals kwinrc plasmashellrc plasmarc konsolerc plasma-org.kde.plasma.desktop-appletsrc; do
  [ -e "$cfg/$f" ] && cp -a "$cfg/$f" "$backup/"
done
echo "backup: ${backup#"$repo"/}"

pkg=$(kreadconfig6 --file kdeglobals --group KDE --key LookAndFeelPackage)
case "$pkg" in
  ""|org.casthub.*) rm -f "$cfg/kdedefaults/package"; echo "theme defaults will be rebuilt at next login" ;;
  *) echo "global theme is $pkg (chosen in System Settings); left as is" ;;
esac
# A shell chosen per user would win over /etc/xdg; clear only ours or Plasma's default.
shell=$(kreadconfig6 --file plasmashellrc --group Shell --key ShellPackage)
case "$shell" in ""|org.kde.plasma.desktop) kwriteconfig6 --file plasmashellrc --group Shell --key ShellPackage --delete ;; esac
echo "Log out and back in to start the Cast Hub desktop."
