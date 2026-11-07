# Shared by tools/install-modes.sh and tools/install-theme.sh (sourced, run as root).
# Installs the SDDM theme with its uikit/ kit. Keeps this machine's theme.conf.user.
install_greeter() {
  local repo=$1 theme=/usr/share/sddm/themes/casthub keep=""
  [ -f "$theme/theme.conf.user" ] && keep=$(cat "$theme/theme.conf.user")
  rm -rf "$theme"
  install -d "$theme/uikit"
  install -m644 "$repo"/greeter/*.qml "$repo"/greeter/metadata.desktop "$repo"/greeter/theme.conf "$theme/"
  install -m644 "$repo"/uikit/*.qml "$repo"/uikit/qmldir "$theme/uikit/"
  [ -n "$keep" ] && printf '%s\n' "$keep" > "$theme/theme.conf.user"
  return 0
}
