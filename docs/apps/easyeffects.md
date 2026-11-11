# EasyEffects: agent rules

User guidance: [apps guide](../apps.md#easyeffects). Decision: [0024](../decisions/0024-easyeffects-default-eq.md).

- EasyEffects is the default equalizer. Setup runs `omarchy pkg add easyeffects` and `omarchy pkg add lsp-plugins-lv2`. `omarchy pkg add` is a no-op when the package is already present; a machine that already has EasyEffects (the M1 mini did) is still Groundwork's to keep. `lsp-plugins-lv2` is optional in Arch extra/easyeffects and required for the equalizer.
- Omarchy first (checked 2026-11-11): no `omarchy-install-easyeffects`; `pacman -Si easyeffects` is extra, not the `omarchy` repo. Recheck dates live in `omarchy-feed-watch.json`.
- Autostart is the Groundwork user unit `groundwork-easyeffects.service`, enabled for the next graphical login, never started or restarted during apply. Flags are EasyEffects 8.x (Qt) `--hide-window --service-mode`. Do not use `--gapplication-service` (deprecated GTK leftover). Do not write Waybar config, `~/.config/waybar`, or Hyprland `autostart.lua`. Omarchy's bar is Quickshell; its Tray widget shows StatusNotifier icons only while the process is running.
- Never write `~/.config/easyeffects`, presets, or EasyEffects' own XDG autostart file. `--without-easyeffects` saves `"easyeffects": false` and leaves an existing install, unit, and config alone. Opt-out does not disable or drop the unit; that is a separate `systemctl --user disable --now`.
- Fixture success is not live PipeWire, Bluetooth, or tray acceptance.
