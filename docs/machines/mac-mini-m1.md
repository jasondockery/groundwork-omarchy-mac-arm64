# Apple Mac mini (M1, 2020)

This is the reference machine. Nothing in `packages/` or `install.sh` depends on it.

| Item | Value |
|---|---|
| Model | Apple Mac mini (M1, 2020) |
| Device tree | `apple,j274` / `apple,t8103` |
| Memory | 16 GB |
| Display | External HDMI, 2560x1440 |
| Pointing device | Apple Magic Trackpad |
| Wi-Fi | Broadcom BCM4378. Drops are tracked upstream. Use Ethernet for remote access. |
| Microphone | No built-in mic. The Asahi mic service error does not block the desktop. |
| Omarchy | `omarchy-dev 4.0.0.r7080.g60f5b49-1` (observed 2026-10-18) |
| Kernel | Running `7.1.12-7-2-ARCH`; package `linux-aurora 7.1.12.aurora7-2` |
| Desktop | `hyprland 0.56.2-7`, `quickshell 0.3.2.r7.g2b4501a-1`, `qt6-base 6.12.0-3` |

Add another Apple Silicon Mac as a new file in this directory. Do not put its host name, Wi-Fi name, or Tailscale address in the file.
