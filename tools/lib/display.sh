# Shared by tools/install-modes.sh and tools/install-theme.sh (sourced, run as root).
# Installs the Display-mode UI: the casthub Python package (with uikit/ copied in, not
# linked), its launchers and the Display Sway config.
install_display() {
  local repo=$1 lib=/usr/lib/casthub
  rm -rf "$lib/casthub"
  install -d "$lib"
  cp -rL --no-preserve=ownership "$repo/casthub" "$lib/casthub"
  find "$lib/casthub" -name __pycache__ -prune -exec rm -rf {} +
  chmod -R u=rwX,go=rX "$lib"
  install -Dm755 "$repo/modes/display/casthub-leave" /usr/libexec/casthub/casthub-leave
  install -Dm755 "$repo/modes/display/casthub-display-ui" /usr/libexec/casthub/casthub-display-ui
  install -Dm755 "$repo/modes/display/casthub-pin" /usr/libexec/casthub/casthub-pin
  install -Dm644 "$repo/modes/display/sway.conf" /usr/share/casthub/display/sway.conf
  install -d -m 0750 -o root -g casthub /etc/casthub
  return 0
}
