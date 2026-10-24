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
| Encryption / disk | LUKS-encrypted btrfs root 384.5G, 2G ext4 `/boot`, 500M vfat `/boot/efi`, macOS APFS 71.1G and RecoveryOS kept. |
| Desktop | `hyprland 0.56.2-7`, `quickshell 0.3.2.r7`, `qt6-base 6.12.0-3`, `qt6-declarative 6.12.0-2`. Super+Space / Super+K work; 0 palette `Cannot read property` errors. No failed system or user units. |

Add another Apple Silicon Mac as a new file in this directory. Do not put its host name, Wi-Fi name, or Tailscale address in the file.
