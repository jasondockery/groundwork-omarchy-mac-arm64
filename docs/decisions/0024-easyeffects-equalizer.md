# 0024. EasyEffects equalizer on by default

Checked 2026-11-11. Omarchy's PipeWire/WirePlumber stack exposes volume and output selection only. There is no equalizer in the desktop, the Install menu, or an `omarchy-install-*` helper. Jason wants one included by default on these machines, with a per-output preset story for a Bluetooth speaker, and no surprise coloring of sound.

## Omarchy first

Checked 2026-11-11:

| Path | Result |
|---|---|
| `omarchy pkg present` / Omarchy package set | No EasyEffects package in the Omarchy set. |
| `omarchy-install-*` | No `omarchy-install-*equalizer*` or `omarchy-install-*easyeffects*` command. |
| pkgs.omarchy.org `edge/aarch64` | No `easyeffects` in the `omarchy` repo. |
| Arch Linux ARM `extra` | `easyeffects` 8.3.2-1 aarch64. Optional equalizer plugins are `lsp-plugins-lv2` 1.2.37-1 aarch64, also `extra`. |

Groundwork therefore installs both with `omarchy pkg add`. Recheck by 2026-12-11, or when Omarchy adds an installer or package.

## Decision

- **On by default.** `./install.sh` and `update-all` install `easyeffects` and `lsp-plugins-lv2` and enable a Groundwork-owned user unit so the processor is in the PipeWire graph after login. `--without-easyeffects` saves `"easyeffects": false` in `~/.config/groundwork/omarchy.json` and leaves an existing install and unit alone, as `--without-keyd` does for keyd. Only the opt-out is saved, so the machine's config lists only what differs from this default. `--with-easyeffects` removes that key. A file written before this choice existed is on.
- **No EasyEffects config is written.** Upstream's empty pipeline is the flat default: no equalizer, compressor, or other plugin is enabled until you add one. This layer does not ship a preset, does not set `process-all-outputs`, and does not name a Bluetooth device. Per-output presets stay in the app (Autoload), on the machine.
- **A user unit, not a Hyprland bind and not XDG autostart.** Omarchy is Hyprland via UWSM, not Plasma, so EasyEffects 8's in-app autostart (KDE/XDG) is not a reliable login path here. The Arch package ships no systemd unit (file list for 8.3.2-1: `usr/bin/easyeffects` and desktop/icon/locale files). `config/easyeffects.service` is the only override: `After=`/`Wants=pipewire.service`, `PartOf=`/`WantedBy=graphical-session.target`, `ExecStart=easyeffects --hide-window --service-mode` (EasyEffects 8; the GTK `--gapplication-service` flag is not used), and `Restart=on-failure` with `RestartSec=2`. Each of those differs from "no unit" and has a reason in that file. Setup copies it to `~/.config/systemd/user/easyeffects.service`, numbered-backups a different existing file, refuses a symlink, runs `systemctl --user daemon-reload`, and `enable --now`. A missing user bus (SSH without a desktop) prints the enable command and continues. `--without-easyeffects` does not disable the unit.
- **No keybinding.** Omarchy's `Super+Space` launcher already finds the packaged `com.github.wwmm.easyeffects.desktop`. Decision 0007 leaves desktop binds to Omarchy. There is no stock EasyEffects shortcut to document.
- **`omarchy-feed-watch.json` watches for an Omarchy route.** The self-install stays needed until Omarchy ships a package or `omarchy-install-*`. `lsp-plugins-lv2` is a dependency of this app, not a separate watched app.

## What setup changes

| Setting | Upstream default | This layer | Reason |
|---|---|---|---|
| `easyeffects` package | not installed | `omarchy pkg add easyeffects` | Arch `extra` aarch64 equalizer UI for PipeWire |
| `lsp-plugins-lv2` package | optional, often absent | `omarchy pkg add lsp-plugins-lv2` when its LV2 bundles are missing | EasyEffects equalizer, compressor, delay, and loudness plugins |
| user unit `easyeffects.service` | none | installed and enabled, opt-out with `--without-easyeffects` | processor in the PipeWire graph at graphical login without opening a window |
| EasyEffects pipeline / presets | empty | unchanged | no surprise coloring of sound |
| Hyprland binds | Omarchy's | unchanged | launcher is enough |

## Not done

- No live apply on the reference mini in this change. Package presence after the owner runs setup is not acceptance that Bluetooth output, a preset, or latency is good.
- No WONDERBOOM (or any device) preset, name, or address in this repo.
- No calf, zam-plugins, mda.lv2, or x42 plugins. Equalizer is the asked-for tool; `lsp-plugins-lv2` supplies it.
- No doctor check. Missing EasyEffects is a setup preference, not a host-tool gap (decision 0021).
- groundwork-mac, groundwork-windows, and groundwork-linux are unchanged.

Recheck on 2026-12-11, or when Omarchy ships EasyEffects, or when EasyEffects changes `--hide-window --service-mode`.
