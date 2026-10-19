# Status

Reference machine: Apple Mac mini (M1, 2020). Details are in `docs/machines/mac-mini-m1.md`. The files in this repo are not specific to that machine.

| Item | State |
|---|---|
| Omarchy installed | Live package `omarchy-dev 4.0.0.r7080.g60f5b49-1`, observed 2026-10-18. The older record was `4.0.2-2` stable; installer provenance and the current channel were not verified during this setup. |
| Migration track | Newest upstream Omarchy Mac Installer prerelease plus signed `edge` catalog. As of 2026-10-16, catalog enables `apple,j274`, requires installer `2.2.0`, and selects image [`omarchy-mac-edge-2026.10.14-79f8e36b977f`](https://github.com/omacom/omarchy-mac-installer/releases/tag/omarchy-mac-edge-2026.10.14-79f8e36b977f). Intentional bleeding-edge install; use current catalog, not a pinned dated image. |
| Kernel | Running `7.1.12-7-2-ARCH`; package `linux-aurora 7.1.12.aurora7-2`. |
| Blank Super+K / Super+Space menu | Upstream [omarchy-mac#707](https://github.com/omacom/omarchy-mac/issues/707). Selected beta image is a reinstall target; verify both shortcuts after boot. No local patch. |
| Microphone unit error | Upstream [omarchy-mac#728](https://github.com/omacom/omarchy-mac/issues/728). No workaround applied. |
| Wi-Fi drops while awake | Upstream [omarchy-mac#729](https://github.com/omacom/omarchy-mac/issues/729). No workaround applied. |
| Trackpad | Personal override linked; Hyprland reload succeeded with no configuration errors. Bluetooth pairing works and was confirmed by the user. |
| Caps Lock | keyd enabled and active; installed config matches the repo's tap-Escape/hold-Control default. Physical tap/hold acceptance remains to be checked by the user. |
| Remote access | Tailscale installed; daemon enabled and active. Device is logged out; interactive enrollment and two-way SSH acceptance remain pending. Decision is `docs/decisions/0004-remote-access.md`. |
| Apps | T3 Code Nightly, Cursor, Herdr, Obsidian, Bitwarden CLI and user Flatpak installed. Existing Obsidian settings preserved. Account sign-in, browser-extension approval/autofill, and note sync remain user acceptance. A new desktop session may be needed for the Flatpak launcher entry. |
| Live setup findings | [Chromium registration directory permissions](https://github.com/jasondockery/groundwork-omarchy-mac-arm64/issues/4) and [missing provider behind a mise shim](https://github.com/jasondockery/groundwork-omarchy-mac-arm64/issues/5). Package/configuration checks do not prove authenticated app use. |

Last observed locally: 2026-10-18. This records setup checks, not full desktop or cross-machine acceptance.
