#!/bin/bash
# Run the Cast Hub desktop headless and screenshot it, without touching the real session:
# a throwaway HOME, its own D-Bus session and a virtual KWin output. Output: local/previews/.
#
#   tools/preview_shell.sh [--scheme light|dark] [--size 1920x1080] [--wait 8] [--tab Console|Files|Desktop] [-- cmd...]
#
# Optional "cmd" runs inside the session after Plasma starts (e.g. to open windows or switch
# tabs); the screenshot is taken after it. Packages are installed into the throwaway
# XDG_DATA_HOME, so nothing system-wide is needed.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
scheme=light size=1920x1080 wait=8 keep=0 tab="" extra=()
while [ $# -gt 0 ]; do
  case $1 in
    --scheme) scheme=$2; shift 2 ;;
    --size) size=$2; shift 2 ;;
    --wait) wait=$2; shift 2 ;;
    --keep) keep=1; shift ;;
    --tab) tab=$2; shift 2 ;;
    --) shift; extra=("$@"); break ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
w=${size%x*} h=${size#*x}
base=$repo/local/shell-preview
out=$repo/local/previews
mkdir -p "$out"
rm -rf "$base"; mkdir -p "$base"/{home,config,data,cache,state,run}; chmod 700 "$base/run"
export HOME=$base/home XDG_CONFIG_HOME=$base/config XDG_DATA_HOME=$base/data XDG_CACHE_HOME=$base/cache \
       XDG_STATE_HOME=$base/state XDG_RUNTIME_DIR=$base/run
# system/xdg stands in for the /etc/xdg keys tools/install-theme.sh merges.
mkdir -p "$base/xdg"; cp "$repo"/system/xdg/* "$base/xdg/"
export XDG_CONFIG_DIRS=$base/xdg:/etc/xdg XDG_DATA_DIRS=/usr/local/share:/usr/share
export QT_FORCE_STDERR_LOGGING=1 XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=KDE KDE_FULL_SESSION=true QT_QPA_PLATFORM=wayland
unset DISPLAY WAYLAND_DISPLAY SWAYSOCK

"$repo/tools/lib/install-shell-user.sh" "$repo" "$XDG_DATA_HOME"
lnf=org.casthub.$scheme
cat > "$XDG_CONFIG_HOME/kdeglobals" <<CFG
[KDE]
LookAndFeelPackage=$lnf
[General]
ColorScheme=CastHub${scheme^}
font=Instrument Sans,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1
fixed=DM Mono,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1
CFG
"$(command -v plasma-apply-colorscheme)" --help >/dev/null 2>&1 || true
# Colour sections, as plasma-apply-colorscheme would write them.
cat "$repo/shell/colors/CastHub${scheme^}.colors" | sed -n '/^\[Colors:/,$p' >> "$XDG_CONFIG_HOME/kdeglobals"
printf "[Theme]\nname=casthub\n" > "$XDG_CONFIG_HOME/plasmarc"
mkdir -p "$HOME/Desktop/embedded-os" "$HOME/Desktop/screen-share"
: > "$HOME/Desktop/resume.pdf"; : > "$HOME/Desktop/boot-logo.svg"; : > "$HOME/Desktop/notes.md"
printf "[General]\nLastSeenVersion=%s\n" "$(plasmashell --version | awk "{print \$2}")" > "$XDG_CONFIG_HOME/plasma-welcomerc"
mkdir -p "$XDG_CONFIG_HOME/autostart"
printf "[Desktop Entry]\nHidden=true\n" > "$XDG_CONFIG_HOME/autostart/org.kde.plasma-welcome.desktop"

shot=$out/shell-$scheme-${w}x${h}${tab:+-$tab}${PREVIEW_TAG:+-$PREVIEW_TAG}.png
dbus-run-session -- bash -c '
  set -u
  QT_LOGGING_RULES="kwin_scripting*=true;js*=true;qml*=true;default.debug=true" kwin_wayland --virtual --width '"$w"' --height '"$h"' --socket ch-preview --no-lockscreen >"'"$base"'/kwin.log" 2>&1 &
  for i in $(seq 50); do [ -S "$XDG_RUNTIME_DIR/ch-preview" ] && break; sleep 0.1; done
  export WAYLAND_DISPLAY=ch-preview
  # Normally started by systemd; plasmashell waits for activities before loading its layout.
  /usr/libexec/kactivitymanagerd >"'"$base"'/kamd.log" 2>&1 &
  kded6 >"'"$base"'/kded.log" 2>&1 &
  sleep 1
  plasmashell --no-respawn >"'"$base"'/plasmashell.log" 2>&1 &
  psh=$!
  sleep '"$wait"'
  kill -0 $psh 2>/dev/null || { wait $psh; echo "plasmashell exited ($?)"; }
  '"${extra:+$(printf '%q ' "${extra[@]}")}"'
  if [ -n "'"$tab"'" ]; then
    gdbus call --session --dest org.kde.kglobalaccel --object-path /component/kwin --method org.kde.kglobalaccel.Component.invokeShortcut "CastHub '"$tab"'" >/dev/null
    sleep 7
  fi
  sleep 1
  spectacle -b -n -f -o "'"$shot"'" >"'"$base"'/spectacle.log" 2>&1 || echo "screenshot failed (see spectacle.log)"
  kill $psh $(jobs -p) 2>/dev/null; wait 2>/dev/null
' || true
[ -f "$shot" ] && echo "wrote ${shot#$repo/}"
grep -iE "casthub|org.casthub|uikit" "$base/plasmashell.log" | grep -v propertyCache | head -30 || true
grep -iE "casthub" "$base/kwin.log" | head -20 || true
[ "$keep" = 1 ] || true
