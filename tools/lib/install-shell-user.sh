#!/bin/bash
# Install the Cast Hub desktop packages into a data dir (an XDG_DATA_HOME for previews, or
# /usr/share at install time). Usage: install-shell-user.sh <repo> <datadir>
set -euo pipefail
repo=$1 dst=$2
copy() { rm -rf "$2"; mkdir -p "$(dirname "$2")"; cp -rL --no-preserve=ownership "$1" "$2"; }
for v in light dark; do
  copy "$repo/shell/lookandfeel/org.casthub.$v" "$dst/plasma/look-and-feel/org.casthub.$v"
  mkdir -p "$dst/plasma/look-and-feel/org.casthub.$v/contents/splash"
  copy "$repo/uikit" "$dst/plasma/look-and-feel/org.casthub.$v/contents/splash/uikit"
done
copy "$repo/shell/plasma-shell/org.casthub.desktop" "$dst/plasma/shells/org.casthub.desktop"
for p in "$repo"/shell/plasmoids/*/; do
  id=$(basename "$p")
  copy "$p" "$dst/plasma/plasmoids/$id"
  [ -d "$dst/plasma/plasmoids/$id/contents/ui" ] && copy "$repo/uikit" "$dst/plasma/plasmoids/$id/contents/ui/uikit"
done
if [ -d "$repo/shell/plasma-theme/casthub" ]; then copy "$repo/shell/plasma-theme/casthub" "$dst/plasma/desktoptheme/casthub"; fi
if [ -d "$repo/shell/decoration/org.casthub.decoration" ]; then
  copy "$repo/shell/decoration/org.casthub.decoration" "$dst/kwin/decorations/org.casthub.decoration"
  copy "$repo/uikit" "$dst/kwin/decorations/org.casthub.decoration/contents/ui/uikit"
fi
if [ -d "$repo/shell/kwin-script/org.casthub.views" ]; then copy "$repo/shell/kwin-script/org.casthub.views" "$dst/kwin/scripts/org.casthub.views"; fi
mkdir -p "$dst/color-schemes"; cp "$repo"/shell/colors/*.colors "$dst/color-schemes/"
