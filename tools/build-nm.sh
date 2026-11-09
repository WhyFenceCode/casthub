#!/bin/bash
# Build NetworkManager with Cast Hub's Wi-Fi Direct patch, in a throwaway Fedora container.
#   tools/build-nm.sh            (as your user; no root, nothing installed on this machine)
#
# Why: NetworkManager removes every Wi-Fi Direct group it didn't start itself (docs/LOG.md,
# M10 live test 3), so casthub-p2p's groups die within milliseconds. The patch
# (system/networkmanager/patches/) leaves such groups alone, but only while NetworkManager's
# Wi-Fi Direct device is unmanaged (system/networkmanager/casthub-p2p.conf).
#
# Builds the version that is installed now with ".casthub1" after its release (3.fc45 →
# 3.fc45.casthub1: sorts just above Fedora's same build, below its next one), and puts the
# RPMs in local/nm-build/out/. Install with tools/install-nm.sh. A newer Fedora NetworkManager
# replaces ours on the next update (security fixes win); Wi-Fi Direct then stops working until
# this is run again for the new version.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
out="$repo/local/nm-build"
mkdir -p "$out/out"
# The Fedora build ours is based on (also when a .casthub build is installed already).
evr=$(rpm -q --qf '%{VERSION}-%{RELEASE}' NetworkManager | sed -E 's/\.casthub[0-9]+//')
fedora=$(. /etc/os-release && echo "$VERSION_ID")
patch=0001-wifi-p2p-leave-groups-of-unmanaged-p2p-device.patch
echo "building NetworkManager-$evr + $patch in fedora:$fedora"

podman run --rm \
  -v "$out":/out:Z \
  -v "$repo/system/networkmanager/patches/$patch":/patch:ro,Z \
  "registry.fedoraproject.org/fedora:$fedora" bash -euo pipefail -c "
    dnf -q -y install dnf-plugins-core rpm-build >/dev/null
    cd /tmp
    dnf -q download --source NetworkManager-$evr
    rpm -i --define '_topdir /tmp/rpmbuild' NetworkManager-*.src.rpm 2>/dev/null
    cp /patch /tmp/rpmbuild/SOURCES/$patch
    spec=/tmp/rpmbuild/SPECS/NetworkManager.spec
    # Our patch after the sources; %autosetup -p1 applies it. Release gets .casthub1 after %{?dist}.
    sed -i '0,/^Source0*:/s//Patch9001: $patch\n&/' \$spec
    sed -i -E 's/^(Release:[[:space:]]*[^%]+%\{\?dist\})/\1.casthub1/' \$spec
    grep -E '^(Release|Patch9001):' \$spec
    dnf -q -y builddep \$spec >/dev/null
    rpmbuild -bb --nocheck --define '_topdir /tmp/rpmbuild' \$spec > /tmp/build.log 2>&1 || { tail -40 /tmp/build.log; exit 1; }
    rm -f /out/out/*.rpm
    cp /tmp/rpmbuild/RPMS/*/NetworkManager*.rpm /out/out/
"
ls "$out/out"
