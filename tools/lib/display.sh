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
  install -Dm755 "$repo/modes/display/casthub-hub" /usr/libexec/casthub/casthub-hub
  install -Dm755 "$repo/modes/display/casthub-p2p" /usr/libexec/casthub/casthub-p2p
  install -Dm644 "$repo/modes/display/casthub-display.target" /usr/lib/systemd/user/casthub-display.target
  install -Dm644 "$repo/modes/display/casthub-hub.service" /usr/lib/systemd/user/casthub-hub.service
  install -Dm644 "$repo/modes/display/sway.conf" /usr/share/casthub/display/sway.conf
  # Display as a panel inside Workstation or Console (casthub-display, Ctrl+Alt+Esc closes it)
  install -Dm755 "$repo/modes/display/casthub-display" /usr/bin/casthub-display
  install -Dm755 "$repo/modes/display/casthub-panel-close" /usr/libexec/casthub/casthub-panel-close
  install -Dm644 "$repo/modes/display/panel.conf" /usr/share/casthub/display/panel.conf
  install -Dm755 "$repo/modes/console/casthub-console-escape" /usr/libexec/casthub/casthub-console-escape
  install -Dm644 "$repo/modes/console/sway.conf" /usr/share/casthub/console/sway.conf
  install -d -m 0750 -o root -g casthub /etc/casthub
  return 0
}
