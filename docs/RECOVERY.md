# Recovery

## The login screen doesn't appear, or looks broken

1. Press **Ctrl + Alt + F3** for a text console and sign in with your admin account.
2. Go back to Fedora's login screen:
   `sudo ~/code/casthub/tools/switch-display-manager.sh plasmalogin && sudo reboot`

If only the *theme* is broken, SDDM shows its built-in fallback theme, which still
lists only the three Cast Hub modes. Pick **Workstation** and sign in normally.

## Stuck in Display mode

- **Ctrl + Alt + Esc** leaves Display mode (M7). In M6 this gains a PIN.
- **Ctrl + Alt + F3** gives a text console.

## Undo the PAM change

`tools/install-modes.sh` saved the original as `/etc/pam.d/sddm.casthub-backup`:
`sudo cp /etc/pam.d/sddm.casthub-backup /etc/pam.d/sddm`

## Desktop theme (M8, `tools/install-theme.sh`)

Back to Fedora's look for everyone who never picked a global theme:

    sudo rm /etc/xdg/kdeglobals                      # did not exist before Cast Hub
    sudo cp -a /etc/xdg/konsolerc.casthub-backup /etc/xdg/konsolerc

Then log out and in. The installed files (`/usr/share/plasma/look-and-feel/org.casthub.*`,
`/usr/share/color-schemes/CastHub*`, `/usr/share/wallpapers/CastHub`) are harmless left in
place. One user only: System Settings → Global Theme → Fedora.

## Desktop (M9: top bar, three views, window frames)

Back to Plasma's own desktop for everyone, keeping the Cast Hub colours:

    sudo kwriteconfig6 --file /etc/xdg/plasmashellrc --group Shell --key ShellPackage --delete
    sudo kwriteconfig6 --file /etc/xdg/kwinrc --group Plugins --key org.casthub.viewsEnabled false
    sudo kwriteconfig6 --file /etc/xdg/kwinrc --group org.kde.kdecoration2 --key library org.kde.breeze
    sudo kwriteconfig6 --file /etc/xdg/kwinrc --group org.kde.kdecoration2 --key theme Breeze

Then log out and in. Plasma's old panel layout was never touched
(`~/.config/plasma-org.kde.plasma.desktop-appletsrc`). Without logging out, as the user:
`qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.changeShell org.kde.plasma.desktop`.
`tools/adopt-desktop.sh` keeps a copy of the files it touches in `local/desktop-backup-*`.
Lock screen back to Plasma's default background:
`sudo kwriteconfig6 --file /etc/xdg/kscreenlockerrc --group Greeter --key WallpaperPlugin --delete`.
If the lock screen itself ever fails ("The screen locker is broken"), switch to a text console
(Ctrl+Alt+F3), log in, and run `loginctl unlock-sessions`.
If the top bar is gone and nothing responds: Ctrl+Alt+F3, log in, run the commands above.

## Receiver (M3, `tools/install-receiver.sh`)

Wi-Fi misbehaving after installing the receiver? Stop the Wi-Fi Direct service; NetworkManager
keeps managing Wi-Fi either way. A Wi-Fi Direct cast is an in-memory NM profile,
`casthub-wifi-direct`; `nmcli connection delete casthub-wifi-direct` ends one by hand.

```sh
sudo systemctl disable --now casthub-p2p.service
sudo rm /etc/systemd/system/casthub-p2p.service /etc/firewalld/zones/casthub-p2p.xml
sudo systemctl daemon-reload && sudo firewall-cmd --reload
sudo systemctl restart wpa_supplicant NetworkManager   # clears our P2P settings
```

## Roll the whole system back to a snapshot

Snapshots are made by `tools/snapshot.sh` before every system change, at the top
level of the root Btrfs volume in `snapshots/`. From a text console or a live USB:

```sh
sudo mount -o subvolid=5 /dev/<root-partition> /mnt
ls /mnt/snapshots                                  # pick one
sudo mv /mnt/root /mnt/root.broken
sudo btrfs subvolume snapshot /mnt/snapshots/<name> /mnt/root   # writable copy
sudo reboot
# once happy: sudo btrfs subvolume delete /mnt/root.broken
```

`/home` is a separate subvolume and is not touched by this.
