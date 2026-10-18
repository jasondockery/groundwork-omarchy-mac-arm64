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
| Omarchy | `4.0.2-2` |
| Kernel | `linux-asahi 7.1.13.asahi3-2` |
| Desktop | Hyprland 0.56.2, quickshell 0.3.2, Qt 6.12.0 |

Add another Apple Silicon Mac as a new file in this directory. Do not put its host name, Wi-Fi name, or Tailscale address in the file.
