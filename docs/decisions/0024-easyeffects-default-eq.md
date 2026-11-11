# 0024. EasyEffects is the default equalizer

Checked 2026-11-11 on the M1 mini (Omarchy edge, Quickshell bar). EasyEffects was already installed on that machine; this layer had not installed it.

## Problem

Omarchy has no built-in equalizer. PipeWire is the session audio. Bluetooth speakers such as a UE WONDERBOOM, and HDMI, are used as they come. The usual Arch answer is EasyEffects (`extra/easyeffects`) with `lsp-plugins-lv2` for the equalizer. On the mini those packages were already present from an earlier hand install. Groundwork did not own them, so a fresh machine would not get them, and a rerun would not know they were supposed to be there.

EasyEffects 8.x (Qt) equalizes output only while its process is running. Omarchy's bar is Quickshell (`quickshell -n -p /usr/share/omarchy/shell`), not Waybar. The Tray widget (`plugins/bar/widgets/Tray.qml`) shows StatusNotifier icons only for running apps. A package install without a background service therefore leaves both the equalizer and the tray icon off until someone opens the window by hand.

## Omarchy first (2026-11-11)

1. `omarchy pkg` / `omarchy pkg add`: Arch extra `easyeffects` and `lsp-plugins-lv2` are the packages. There is no Omarchy-named wrapper. `omarchy pkg add` is the install path, and it is a no-op when the package is already present.
2. Omarchy installers: no `omarchy-install-easyeffects` (or similar) on `PATH`.
3. `pkgs.omarchy.org/edge/aarch64`: `pacman -Si easyeffects` is extra, not the `omarchy` repo.

Groundwork therefore uses `omarchy pkg add` for the packages and owns only the user unit that Omarchy does not provide. Recheck when Omarchy ships an installer or an `omarchy` package; feed-watch lists this app.

## Decision

- **Install by default.** Setup and `update-all` run `omarchy pkg add easyeffects` and `omarchy pkg add lsp-plugins-lv2`. `lsp-plugins-lv2` is optional in the Arch `easyeffects` package and required for the equalizer; it is added on its own so a machine that already has EasyEffects without it still gets the plugins. A package that is already installed is left installed. `--without-easyeffects` records `"easyeffects": false` in `~/.config/groundwork/omarchy.json` and leaves an existing install, user unit, and `~/.config/easyeffects` alone. Only the opt-out is saved, so the machine's config lists only what differs from this default. A missing key means on.
- **Background service, next graphical login.** `scripts/groundwork-easyeffects.py configure` writes `~/.config/systemd/user/groundwork-easyeffects.service` and runs `systemctl --user enable` only. It does not start or restart the process during apply, so a live audio session is not given new filters, and an SSH apply without `WAYLAND_DISPLAY` does not try to open a tray. The unit is `WantedBy=graphical-session.target` and `PartOf` that target, the same pattern as screen sharing: it starts with the desktop (Quickshell, PipeWire) and stops on logout. `After=` and `Wants=` `pipewire.service`.
- **EasyEffects 8.x flags.** `ExecStart` is `easyeffects --hide-window --service-mode`. `--gapplication-service` is the old GTK flag: still accepted for compatibility, marked deprecated in `command_line_parser.cpp`, and must not be used for a new unit ([wwmm/easyeffects#4446](https://github.com/wwmm/easyeffects/issues/4446), [#4466](https://github.com/wwmm/easyeffects/issues/4466)). `ExecStop` is `easyeffects --quit`.
- **Never write EasyEffects' own files.** Setup does not create or edit `~/.config/easyeffects`, presets, or EasyEffects' XDG autostart file. Those belong to the app. If the GUI already enabled autostart, EasyEffects is single-instance; this layer does not delete that file.
- **Not Waybar, not Hyprland autostart.lua.** Omarchy owns the bar and `autostart.lua`. This repo stows only `packages/hypr` `input.lua`. A Waybar snippet or `~/.config/waybar` would be the wrong desktop.

## Why a user unit instead of no autostart

Without a running process there is no equalizer and no tray icon. Desktop apps that are only launched by hand (Obsidian, FreeTube) do not have that constraint. Screen sharing and Syncthing already use systemd `--user` for session services this layer owns. Enable-without-now matches "do not fight existing audio" during apply.

## Applying and undoing

A fresh run and an update both install the packages when the preference is on, then enable the unit. The equalizer and tray appear at the next desktop login. Opening **EasyEffects** from `Super+Space` still works; it talks to the running service.

To turn it off:

1. `./install.sh --without-easyeffects` so later runs skip it.
2. `systemctl --user disable --now groundwork-easyeffects.service` if the unit was already enabled.
3. Leave `~/.config/easyeffects` in place unless you mean to delete presets.

`omarchy pkg drop easyeffects lsp-plugins-lv2` removes the packages. That is a separate choice; opt-out does not drop them.

## Not done

- No live apply of this unit on the mini in this change. Package presence there is not that proof. The owner runs `./install.sh` or `update-all`, then logs out and back in.
- No default EQ curve. Presets stay the user's.
- No copy of EasyEffects' XDG autostart, and no edit of Omarchy's Quickshell or Hyprland autostart.

Recheck on 2027-02-11, or when Omarchy adds an EasyEffects installer, or when EasyEffects drops `--hide-window --service-mode`.
