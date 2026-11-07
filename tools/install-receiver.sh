#!/bin/bash
# M3: install the Miracast receiver pieces.
#   tools/asroot.py --why "..." -- tools/install-receiver.sh
#
# Changes: snapshot of / · /usr/lib/casthub + /usr/libexec/casthub (incl. casthub-hub, casthub-p2p)
#   · /usr/share/casthub/display/sway.conf · /etc/systemd/system/casthub-p2p.service (enabled, started)
#   · /etc/firewalld/zones/casthub-p2p.xml (used only for Wi-Fi Direct group interfaces)
# The firewall's active zone is not changed. Undo: docs/RECOVERY.md, "Receiver".
set -euo pipefail

[ "$EUID" -eq 0 ] || { echo "run as root (through tools/asroot.py)" >&2; exit 1; }
repo=$(cd "$(dirname "$0")/.." && pwd)
step() { printf '\n== %s\n' "$*"; }
# shellcheck source=lib/display.sh
. "$repo/tools/lib/display.sh"

step "Snapshot"
"$repo/tools/snapshot.sh" pre-receiver

step "Packages present"
for p in dnsmasq wpa_supplicant avahi python3-dbus-fast python3-i3ipc gstreamer1-plugins-bad-free; do
  rpm -q "$p" >/dev/null || { echo "missing package: $p" >&2; exit 1; }
done

step "Receiver code"
install_display "$repo"

step "Firewall zone for Wi-Fi Direct groups"
install -Dm644 "$repo/system/firewalld/casthub-p2p.xml" /etc/firewalld/zones/casthub-p2p.xml
restorecon -F /etc/firewalld/zones/casthub-p2p.xml 2>/dev/null || true
firewall-cmd --reload >/dev/null
firewall-cmd --get-zones | tr ' ' '\n' | grep -qx casthub-p2p && echo "zone casthub-p2p loaded"

step "casthub-p2p service"
install -Dm644 "$repo/system/systemd/casthub-p2p.service" /etc/systemd/system/casthub-p2p.service
systemctl daemon-reload
systemctl enable casthub-p2p.service
systemctl restart casthub-p2p.service
sleep 2
systemctl --no-pager --lines=0 status casthub-p2p.service | head -3

echo
echo "Done. Logs: journalctl -u casthub-p2p. Test from the desktop: python3 -m casthub.hub"
