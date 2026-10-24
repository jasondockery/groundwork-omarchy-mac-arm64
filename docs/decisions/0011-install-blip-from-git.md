# 0011. Install Blip from git, opt-in

Apple ships no Messages client for Linux. An Omarchy Mac is the Blip client only. A separate Mac that stays on macOS is the gateway, and that role is [groundwork-mac#25](https://github.com/jasondockery/groundwork-mac/issues/25). This decision supersedes the earlier rule that an iMessage plugin would be installed only after `nixfred.blip` appeared in the Omarchy marketplace.

Checked 2026-10-24, the plugins.omarchy.org catalog (generated 2026-10-22) has no `nixfred.blip` entry. Two unrelated entries use the name Blip: `blip.steam` and `io.github.yogeshojha.blip`. The catalog's iPhone-relay plugins are not adopted. Blip itself is MIT-licensed, active, and installed by `omarchy plugin add` from `main`, not from a tag. Its manifest is schema 1, kind `bar-widget`, default section right. It is QML and TypeScript run by bun. It ships no architecture-specific binaries. Upstream does not state that aarch64, or 16 KiB pages, is tested.

`./install.sh --with-blip` installs and enables it. The flag is off by default: the client is useless without that other Mac, and it runs unsandboxed code from an unpinned `main`. `--without-blip` and a bare `./install.sh` install nothing Blip-related and leave an existing checkout alone. The choice is stored in `~/.config/groundwork/omarchy.json` as `blip`. A file written before this choice existed stays off.

This is decision **0011** because the shared Omarchy plugin list is decision 0010 and is landing separately. Do not renumber this file onto 0010.

## One opt-in block, then the shared list

The shared list does not yet support a git-URL source together with per-entry opt-in, and its update path is expected to pass `--yes`. Blip cannot join that list until both exist, and until an opted-in entry can keep an interactive diff. Until then, setup uses one self-contained block in `scripts/apply-layer.sh`. That block calls Omarchy's own plugin commands. It is not a second plugin installer.

Move Blip into the shared list once that list supports git-URL sources and per-entry opt-in. Keep the bun alignment preflight, and keep updates from passing `--yes` unless someone is there to read the diff.

## What setup changes

| Setting | Upstream default | This layer | Reason |
|---|---|---|---|
| `nixfred.blip` plugin | not installed | installed and enabled, opt-in only | iMessage on Omarchy needs Blip, the SIP-on Mac-gateway client |
| global `bun` via mise | absent | `bun@latest` when the preference is on | hard Blip dependency; Omarchy's uwsm environment exposes mise shims to the bar |
| `socat` | absent | installed when the preference is on | Blip's persistent channel, instead of a fresh SSH startup on every query |

`bun` is installed with `omarchy-install-dev-env bun` (`mise use -g bun@latest`). Bun's curl installer is not used. Missing commands from `jq`, `openssh`, `libnotify`, `wl-clipboard`, `xdg-utils`, and `socat` are added with `omarchy pkg add`. `ffmpeg` and `mpv` stay optional; `blip-setup` names them for voice messages.

Before the plugin is enabled, setup resolves the bun binary (a mise shim is resolved with `mise which`) and calls `bun_alignment_problem` in `scripts/groundwork-update.py`. That function calls `load_alignments`, the same `readelf -lW` LOAD check that guards T3 Code. A LOAD alignment below the kernel page size, or no LOAD segment, stops setup and links [oven-sh/bun#17627](https://github.com/oven-sh/bun/issues/17627). `bun --version` runs only after that check passes, so a binary this kernel cannot map is not executed. The widget is not enabled.

`omarchy plugin add` is not idempotent. When `omarchy plugin list --json` already contains `nixfred.blip`, setup does nothing to it: it does not re-add, re-enable, or remove, including when the user has disabled it. The add, when it runs, is `omarchy plugin add https://github.com/nixfred/blip.git --enable --yes`. `--enable` places the bar widget. Setup does not edit `~/.config/omarchy/shell.json`.

The add has to run inside the graphical session (`HYPRLAND_INSTANCE_SIGNATURE`), the same signal the Hyprland reload uses. Otherwise setup prints the command and leaves the plugin pending. `omarchy plugin list` talks to the running shell, so it is not called from a bare session.

`blip-setup` stays manual. The gateway name is account-specific and cannot live in this repo, and the wizard needs a person at the Mac for the permission prompts. Setup prints:

```text
~/.config/omarchy/plugins/nixfred.blip/scripts/blip-setup GATEWAY_USER@GATEWAY_MAGICDNS_NAME
```

Blip's own defaults stay as upstream ships them. No extra `bridge.conf` keys are written here. Tapbacks stay off. The Inter font prompt is answered `n` by the person running the wizard.

## Updates

`update-all` may run `omarchy plugin update nixfred.blip` only from an interactive terminal, so the diff is shown. It never passes `--yes`. Without a terminal the update is skipped and the rest of `update-all` continues. `groundwork-update` does not update plugins. `omarchy update` does not update plugins either.

When the update changes files under `bridge/`, `update-all` prints a reminder to re-run `blip-setup`. It does not run the wizard. The comparison uses the checkout git history that `omarchy plugin add` creates.

An alignment refusal exits before setup records preferences, matching other hard failures in `apply-layer.sh`. Pass `--with-blip` again after bun can start. A choice that was already saved is left as it was only when this run gets as far as `register`; a refused first opt-in does not record `blip: true`.
