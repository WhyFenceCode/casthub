#!/bin/bash
# M7: install the Cast Hub login screen and the three modes.
#   sudo tools/install-modes.sh [--location "City"]
#
# Safe to re-run. It does NOT switch the display manager. That is a separate,
# reversible step: sudo tools/switch-display-manager.sh sddm
#
# What it changes:
#   snapshot of / (tools/snapshot.sh) · packages: sddm sddm-wayland-plasma sway
#   /usr/share/fonts/casthub · /usr/share/sddm/themes/casthub · /usr/share/casthub
#   /usr/libexec/casthub · /usr/lib/systemd/user/casthub-*.target
#   /usr/share/konsole/CastHub* · /etc/sddm.conf.d/casthub.conf
#   user `casthub` + group `nopasswdlogin` · one line in /etc/pam.d/sddm
set -euo pipefail

[ "$EUID" -eq 0 ] || { echo "run with sudo: sudo $0 $*" >&2; exit 1; }
repo=$(cd "$(dirname "$0")/.." && pwd)
location=""
[ "${1:-}" = "--location" ] && location=${2:-}

# Output goes to local/ only (git-ignored), owned by the invoking user.
mkdir -p "$repo/local"
logf="$repo/local/install-modes-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$logf") 2>&1
[ -n "${SUDO_USER:-}" ] && chown "$SUDO_USER": "$logf"

step() { printf '\n== %s\n' "$*"; }

step "Snapshot"
"$repo/tools/snapshot.sh" pre-m7-modes

step "Generated brand files are current"
python3 -I "$repo/brand/build.py" --check

step "Packages (dnf will ask before changing anything)"
dnf install sddm sddm-wayland-plasma sway konsole

step "Fonts"
install -d /usr/share/fonts/casthub
install -m644 "$repo"/brand/fonts/*.ttf "$repo"/brand/fonts/OFL-*.txt /usr/share/fonts/casthub/
fc-cache -f /usr/share/fonts/casthub >/dev/null

step "Login theme"
theme=/usr/share/sddm/themes/casthub
# shellcheck source=lib/greeter.sh
. "$repo/tools/lib/greeter.sh"
install_greeter "$repo"
admins=$(getent group wheel | cut -d: -f4)
{
  echo "# Written by tools/install-modes.sh for this machine. Not in the repo."
  echo "[General]"
  echo "AdminUsers=$admins"
  echo "Location=$location"
} > "$theme/theme.conf.user"
echo "admins offered Console: ${admins:-<none>}"

step "Sessions and mode files"
install -d /usr/share/casthub/wayland-sessions /usr/share/casthub/x11-sessions
for m in workstation display console; do
  install -m644 "$repo/modes/$m/session.desktop" "/usr/share/casthub/wayland-sessions/casthub-$m.desktop"
  install -Dm644 "$repo/modes/$m/casthub-$m.target" "/usr/lib/systemd/user/casthub-$m.target"
done
install -Dm755 "$repo/modes/casthub-session" /usr/libexec/casthub/casthub-session
install -Dm755 "$repo/modes/display/casthub-leave" /usr/libexec/casthub/casthub-leave
install -Dm755 "$repo/modes/workstation/casthub-appearance" /usr/libexec/casthub/casthub-appearance
install -Dm644 "$repo/modes/workstation/casthub-appearance.service" /usr/lib/systemd/user/casthub-appearance.service
install -Dm644 "$repo/modes/workstation/casthub-appearance.path" /usr/lib/systemd/user/casthub-appearance.path
# Per-user light/dark for the login screen: anyone may add their own file, nobody can
# list, replace or delete another user's (sticky bit).
install -d -m 1733 -o root -g root /var/lib/casthub/appearance
install -Dm644 "$repo/modes/display/sway.conf" /usr/share/casthub/display/sway.conf
install -Dm644 "$repo/modes/console/sway.conf" /usr/share/casthub/console/sway.conf
install -Dm644 "$repo/modes/console/CastHubConsole.profile" /usr/share/konsole/CastHubConsole.profile
install -Dm644 "$repo/modes/console/CastHub.colorscheme" /usr/share/konsole/CastHub.colorscheme
install -Dm644 "$repo/docs/SPEC.md" /usr/share/doc/casthub/SPEC.md
systemctl --global enable casthub-workstation.target casthub-appearance.service casthub-appearance.path

step "SDDM config"
install -Dm644 "$repo/system/sddm/casthub.conf" /etc/sddm.conf.d/casthub.conf

step "Display account"
getent group nopasswdlogin >/dev/null || groupadd --system nopasswdlogin
if ! id casthub >/dev/null 2>&1; then
  useradd --create-home --comment "Cast Hub display" --shell /bin/bash casthub
fi
passwd --lock casthub >/dev/null          # no password at all; only the PAM rule lets it in
usermod -aG nopasswdlogin casthub
id -nG casthub | grep -qw wheel && { echo "ERROR: casthub must not be in wheel" >&2; exit 1; }

step "PAM: passwordless login for casthub only, via SDDM only"
pam=/etc/pam.d/sddm
# PAM has no end-of-line comments, so the marker is its own line above the rule.
marker='# casthub: Display mode logs in the casthub account without a password (SPEC D5)'
rule='auth        sufficient    pam_succeed_if.so quiet user = casthub user ingroup nopasswdlogin'
if ! grep -qF "$marker" "$pam"; then
  cp -a "$pam" "$pam.casthub-backup"
  awk -v m="$marker" -v r="$rule" '!done && /^auth/ { print m; print r; done=1 } { print }' "$pam.casthub-backup" > "$pam"
fi
grep -n 'casthub' "$pam"

restorecon -R /usr/share/sddm/themes/casthub /usr/share/casthub /usr/libexec/casthub /usr/share/fonts/casthub /etc/sddm.conf.d /var/lib/casthub 2>/dev/null || true

step "Done"
if [ "$(readlink -f /etc/systemd/system/display-manager.service 2>/dev/null)" = /usr/lib/systemd/system/sddm.service ]; then
  echo "Installed. SDDM is the active login screen; changes show at the next login screen."
else
  echo "Installed. Nothing has switched yet: the current login screen is unchanged."
fi
cat <<EOF

Preview the real theme (as your normal user, not root):
  sddm-greeter-qt6 --test-mode --theme $theme

Switch the login screen to SDDM (reversible; takes effect on reboot):
  sudo tools/switch-display-manager.sh sddm

Log: $logf
EOF
