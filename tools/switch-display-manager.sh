#!/bin/bash
# Switch the login screen between SDDM (Cast Hub) and Fedora's Plasma Login Manager.
#   sudo tools/switch-display-manager.sh sddm         # use the Cast Hub login screen
#   sudo tools/switch-display-manager.sh plasmalogin  # back to Fedora's default
# Takes effect on the next reboot. Recovery: docs/RECOVERY.md
set -euo pipefail

[ "$EUID" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
case "${1:-}" in
  sddm) on=sddm; off=plasmalogin ;;
  plasmalogin) on=plasmalogin; off=sddm ;;
  *) echo "usage: $0 sddm|plasmalogin" >&2; exit 2 ;;
esac

rpm -q "$( [ $on = sddm ] && echo sddm || echo plasma-login-manager )" >/dev/null \
  || { echo "$on is not installed" >&2; exit 1; }
if [ $on = sddm ]; then
  [ -f /usr/share/sddm/themes/casthub/Main.qml ] || { echo "run tools/install-modes.sh first" >&2; exit 1; }
fi

systemctl disable "$off.service" 2>/dev/null || true
systemctl enable --force "$on.service"
echo "Login screen set to $on. Reboot to use it."
echo "If it fails to appear: Ctrl+Alt+F3, sign in, run: sudo $(cd "$(dirname "$0")" && pwd)/switch-display-manager.sh $off"
