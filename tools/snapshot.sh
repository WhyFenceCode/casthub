#!/bin/bash
# Read-only Btrfs snapshot of the root subvolume, taken before system changes.
#   sudo tools/snapshot.sh <label>
# Snapshots live at the top level of the root Btrfs volume in snapshots/.
# Restoring one is described in docs/RECOVERY.md.
set -euo pipefail

label=${1:?usage: snapshot.sh <label>}
[ "$EUID" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
[ "$(findmnt -no FSTYPE /)" = btrfs ] || { echo "root is not btrfs; no snapshot taken" >&2; exit 1; }

dev=$(findmnt -no SOURCE / | sed 's/\[.*//')
subvol=$(findmnt -no FSROOT /)          # e.g. /root
mnt=$(mktemp -d)
mount -o subvolid=5 "$dev" "$mnt"
trap 'umount "$mnt"; rmdir "$mnt"' EXIT

mkdir -p "$mnt/snapshots"
name="${subvol#/}-$(date +%Y%m%d-%H%M%S)-$label"
btrfs subvolume snapshot -r "$mnt$subvol" "$mnt/snapshots/$name" >/dev/null
echo "snapshot taken: snapshots/$name"
