# Status

Reference machine: Apple Mac mini (M1, 2020). Details are in `docs/machines/mac-mini-m1.md`. The files in this repo are not specific to that machine.

| Item | State |
|---|---|
| Omarchy installed | `4.0.2-2`, stable channel (last recorded state; migration not yet performed). |
| Migration track | Newest upstream Omarchy Mac Installer prerelease plus signed `edge` catalog. As of 2026-10-16, catalog enables `apple,j274`, requires installer `2.2.0`, and selects image [`omarchy-mac-edge-2026.10.14-79f8e36b977f`](https://github.com/omacom/omarchy-mac-installer/releases/tag/omarchy-mac-edge-2026.10.14-79f8e36b977f). Intentional bleeding-edge install; use current catalog, not a pinned dated image. |
| Kernel | `linux-asahi 7.1.13.asahi3-2` |
| Blank Super+K / Super+Space menu | Upstream [omarchy-mac#707](https://github.com/omacom/omarchy-mac/issues/707). Selected beta image is a reinstall target; verify both shortcuts after boot. No local patch. |
| Microphone unit error | Upstream [omarchy-mac#728](https://github.com/omacom/omarchy-mac/issues/728). No workaround applied. |
| Wi-Fi drops while awake | Upstream [omarchy-mac#729](https://github.com/omacom/omarchy-mac/issues/729). No workaround applied. |
| Trackpad | Personal override in `packages/hypr/.config/hypr/input.lua`. |
| Caps Lock | Repo default is keyd tap-Escape/hold-Control via `./install.sh`; `--without-keyd` skips setup. Activation on the reference machine has not been verified. |
| Remote access | Not set up. On the reference machine, `moonlight-qt` is installed as a client only. Tailscale, WayVNC, and Sunshine are not. Decision is `docs/decisions/0004-remote-access.md`. |

Last reviewed: 2026-10-16.
