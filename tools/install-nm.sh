#!/bin/bash
# Install the patched NetworkManager built by tools/build-nm.sh.
#   tools/asroot.py --why "..." -- tools/install-nm.sh
#
# Changes: snapshot of / · the NetworkManager packages already installed are replaced by the
# same version with ".casthub1" (the VPN plugins are separate packages and stay) · NetworkManager
# restarts (Wi-Fi drops for a few seconds). No version lock: a newer Fedora NetworkManager
# replaces ours on update. Undo: docs/RECOVERY.md, "Patched NetworkManager".
set -euo pipefail

[ "$EUID" -eq 0 ] || { echo "run as root (through tools/asroot.py)" >&2; exit 1; }
repo=$(cd "$(dirname "$0")/.." && pwd)
rpms="$repo/local/nm-build/out"
step() { printf '\n== %s\n' "$*"; }

evr=$(rpm -q --qf '%{VERSION}-%{RELEASE}' NetworkManager)
case "$evr" in *casthub*) echo "already patched: NetworkManager-$evr"; exit 0;; esac

# Only packages built from NetworkManager's own source, and only those installed now.
files=()
for name in $(rpm -qa 'NetworkManager*' --qf '%{NAME}\n' | sort); do
  f=$(ls "$rpms/$name"-[0-9]*.casthub1.*.rpm 2>/dev/null | head -1 || true)
  [ -n "$f" ] && files+=("$f")
done
[ "${#files[@]}" -gt 0 ] || { echo "no patched RPMs in $rpms (run tools/build-nm.sh)" >&2; exit 1; }
ver=$(rpm -qp --qf '%{VERSION}-%{RELEASE}' "${files[0]}")
case "$ver" in "${evr%.fc*}".casthub1.*) ;; *) echo "built $ver doesn't match installed $evr: rebuild" >&2; exit 1;; esac

step "Snapshot"
"$repo/tools/snapshot.sh" pre-nm-patch

step "Packages"
printf '  %s\n' "${files[@]##*/}"
dnf -y -q install "${files[@]}"
rpm -q NetworkManager

step "Restart NetworkManager"
systemctl restart NetworkManager
sleep 5
nmcli -t -f DEVICE,TYPE,STATE device | grep -E 'wifi' || true
echo
echo "Done. casthub-p2p's Wi-Fi Direct groups now stay up (the P2P device must be unmanaged)."
