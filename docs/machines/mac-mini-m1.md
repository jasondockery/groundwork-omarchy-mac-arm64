# Apple Mac mini (M1, 2020)

This is the reference machine. Nothing in `packages/` or `install.sh` depends on it.

| Item | Value |
|---|---|
| Model | Apple Mac mini (M1, 2020) |
| Device tree | `apple,j274` / `apple,t8103` |
| Memory | 16 GB |
| Display | External HDMI, 2560x1440 |
| Pointing device | Apple Magic Trackpad |
| Wi-Fi | Broadcom BCM4378. Older drops are tracked upstream as [omarchy-mac#729](https://github.com/omacom/omarchy-mac/issues/729); recheck on Aurora rather than claiming a current result. Use Ethernet for remote access until that recheck. |
| Microphone | No built-in mic. The older Asahi mic-service error is [omarchy-mac#728](https://github.com/omacom/omarchy-mac/issues/728); recheck on Aurora rather than claiming a current result. |
| Install | Migrated 2026-10-17. Installer 2.2.0, image `omarchy-mac-edge-2026.10.14-79f8e36b977f`. `OMARCHY_PATH=/usr/share/omarchy`; no `~/.local/share/omarchy` checkout. `/var/lib/omarchy/image/target` is absent. |
| Channel | `omarchy-channel-current`: `edge`; `omarchy-version`: `4.0.0.r7235.g967bbdd-1`. pacman: `[omarchy]` `https://pkgs.omarchy.org/edge/$arch`, then `[asahi-alarm]`, then `core`, `extra`, `alarm`, `aur`. |
| Omarchy | `omarchy-dev` and `omarchy-settings-dev` `4.0.0.r7235.g967bbdd-1` (observed 2026-10-23) |
| Apple Silicon packages | `omarchy-mac 0.1.4-1`, `omarchy-mac-boot 20261023-1` |
| Boot chain | m1n1-aurora → U-Boot → Limine → `linux-aurora`. Packages: `linux-aurora 7.2.11.aurora1-1` (running `7.2.11`), `m1n1-aurora 1.6.1.aurora20-3`, `limine 12.9.4-1`. `grub` and `linux-asahi` are not installed. |
| Encryption / disk | LUKS-encrypted btrfs root 384.5G, 2G ext4 `/boot`, 500M vfat `/boot/efi`, macOS APFS 71.1G and RecoveryOS kept. Managed TRIM and locate prunes are decision 0023. A per-directory zstd level is not applied; the mount stays `compress=zstd:3`. None of this is recorded as applied here until a read-only check on the machine says so. |
| Desktop | `hyprland 0.56.2-7`, `quickshell 0.3.2.r7`, `qt6-base 6.12.0-3`, `qt6-declarative 6.12.0-2`. Super+Space / Super+K work; 0 palette `Cannot read property` errors. No failed system or user units. The bar is Quickshell (`quickshell -n -p /usr/share/omarchy/shell`), not Waybar. EasyEffects and `lsp-plugins-lv2` were installed by hand before this layer owned them (2026-11-11). After [decision 0024](../decisions/0024-easyeffects-default-eq.md) is applied, record that `groundwork-easyeffects.service` is enabled; package presence is not that proof. |
| Temp storage | 2026-11-06: `/tmp` was systemd's `tmp.mount` tmpfs (50% of RAM, about 7.7 GB) and 100% full; swap is zram only. No `/etc/fstab` `/tmp` line; `/usr/lib/tmpfiles.d/tmp.conf` is `q /tmp 1777 root root 10d` with no `/etc/tmpfiles.d/tmp.conf`. Root is btrfs subvolume `@`; `/home` is `@home`. Omarchy's snapper `root` config covers `/` only (timeline off, 5 snapshots kept). After [decision 0022](../decisions/0022-tmp-on-disk.md) is applied and the mini rebooted, record `findmnt -T /tmp` here. |

## Local settings that differ from upstream defaults

`~/.config/groundwork/omarchy.json` on this machine lists only these, beyond the choices setup saves:

| Key | Value | Reason |
|---|---|---|
| `scratch_dirs` | One name, kept on the machine, not in this repo | Per-user build and test scratch on disk under `~/.cache`, so large temporary trees use neither RAM nor a synced folder ([decision 0022](../decisions/0022-tmp-on-disk.md)). |

`/tmp` on disk is the default, so `tmp_on_disk` is not set here.

Add another Apple Silicon Mac as a new file in this directory. Do not put its host name, Wi-Fi name, or Tailscale address in the file.
