# iMessage on Omarchy

Blip (`nixfred.blip`) is the iMessage, SMS, and RCS client for this machine. Apple does not ship Messages for Linux, so the Omarchy machine is only the client. A separate Mac that stays on macOS, signed into Messages with Messages in iCloud, is the gateway. That Mac's opt-in role (Remote Login, no sleep on AC power, and the clicks macOS will not script) is [groundwork-mac#25](https://github.com/jasondockery/groundwork-mac/issues/25). This page is the client side. Decision: [0011](decisions/0011-install-blip-from-git.md).

Blip talks to the gateway over OpenSSH. Tailscale MagicDNS is the intended path. Tailscale SSH is not the transport: Full Disk Access is granted to `/usr/libexec/sshd-keygen-wrapper`, and Blip's confined key is an OpenSSH `authorized_keys` line. The shared [tailnet guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/tailscale.md) owns the owner-scoped TCP 22 grant from this machine to the gateway. [Private access](remote-access.md) covers Tailscale sign-in on Omarchy.

SIP stays on. Blip reads and sends messages, including groups and attachments, and can show tapbacks, edits, and inline replies. It cannot send edits or threaded replies. Sending tapbacks is experimental (`tapbacks=on` in `bridge.conf`): 1:1 text bubbles, an English-language Mac, and extra Accessibility and System Events grants. Leave tapbacks off.

The marketplace catalog generated 2026-10-22 has no `nixfred.blip` entry. `blip.steam` and `io.github.yogeshojha.blip` are different plugins. This layer does not install the catalog's iPhone-relay messengers. Blip is installed from git. Upstream has not stated that aarch64 or 16 KiB pages are tested. Setup refuses to enable the widget when bun cannot be mapped on this kernel.

## What differs from upstream

| Setting | Upstream default | This layer | Reason |
|---|---|---|---|
| `nixfred.blip` | not installed | installed and enabled, only with `--with-blip` | the SIP-on client for a macOS gateway |
| global `bun` via mise | absent | `bun@latest` when the preference is on | the bar resolves bun through Omarchy's mise shims |
| `socat` | absent | installed when the preference is on | Blip's persistent channel |

Blip's own defaults stay unchanged: no extra `bridge.conf` keys, tapbacks off, and the theme font (answer `n` to the Inter prompt). `ffmpeg` and `mpv` play voice messages and stay optional.

## Prerequisites

- This machine is Apple Silicon Omarchy (aarch64), with Tailscale signed in. See [remote access](remote-access.md).
- The gateway Mac is on macOS 13 or later, signed into Messages, with Messages in iCloud on, and reachable by key-based SSH. Xcode Command Line Tools supply `python3` there.
- An everyday SSH key from this machine is already authorized on the gateway (`ssh-copy-id`). `blip-setup` needs that login to enrol its own confined key. It will not invent the gateway name, and this repo does not store one.
- Run the installer from a terminal inside the Omarchy desktop. Enabling the plugin talks to the running shell. A bare SSH session is reported as pending instead.

## Install

```bash
./install.sh --with-blip
```

That is off by default. `./install.sh` without the flag, and `./install.sh --without-blip`, do not install Blip and do not change an existing checkout.

When the preference is on, setup:

1. Installs any missing command from `jq`, `openssh`, `libnotify`, `wl-clipboard`, `xdg-utils`, and `socat` with `omarchy pkg add`.
2. Installs bun with `omarchy-install-dev-env bun` when `bun` is not already on `PATH`. That is `mise use -g bun@latest`. It does not use bun's curl installer.
3. Resolves a mise shim with `mise which`, then checks that binary with the same `readelf -lW` LOAD-alignment helper that guards T3 Code. `bun --version` runs only after the check passes. If bun cannot start on this page size, setup stops and links [oven-sh/bun#17627](https://github.com/oven-sh/bun/issues/17627). The widget is not enabled.
4. In a Hyprland session, runs `omarchy plugin list --json`. If `nixfred.blip` is absent, it runs `omarchy plugin add https://github.com/nixfred/blip.git --enable --yes` once. A second run sees the id and does not add, enable, or remove it. `--enable` places the bar widget. Setup does not edit `~/.config/omarchy/shell.json`.
5. Prints the next command and stops. It does not run `blip-setup`.

Outside a Hyprland session the same preference installs the dependencies, then prints this command for the desktop:

```bash
omarchy plugin add https://github.com/nixfred/blip.git --enable --yes
```

If that reports the plugin is already installed, do not add it again.

A re-run with the preference still on is a no-op for the plugin and exits 0 when bun still passes the check.

## Manual steps

Do these yourself. The installer cannot see the gateway account, and macOS will not accept the privacy grants from a script.

1. Sign both machines into the tailnet. Add an owner-scoped TCP 22 grant from this machine to the gateway. See the shared tailnet guide.
2. Authorize an everyday SSH key from this machine on the gateway account.
3. In an Omarchy desktop terminal:

   ```bash
   ~/.config/omarchy/plugins/nixfred.blip/scripts/blip-setup GATEWAY_USER@GATEWAY_MAGICDNS_NAME
   ```

   Replace the placeholder with the gateway account and its MagicDNS name. The wizard reads Enter before the permission check and `y`/`N` for the Inter font. Answer `n` unless you want that font. Leave tapbacks off. With no terminal it stops on purpose: an unanswered Automation prompt is stored as a denial. `printf '\nn\n' | blip-setup …` is only for a later re-run, after the grants are already confirmed.

4. While the wizard waits, at the gateway Mac:
   - Full Disk Access: add `/usr/libexec/sshd-keygen-wrapper` (⌘⇧G in the file picker).
   - Click **Allow** on "sshd-keygen-wrapper wants to control Messages" within about two minutes. A timeout is recorded as a denial. Recovery is `tccutil reset AppleEvents` on the Mac, which clears Automation grants for every app, then run `blip-setup` again while you are at the Mac.
5. Optional, at the Mac: `blip-check --markread` for mark-read and tapbacks (Accessibility, and Automation for System Events), and Contacts if you want to save contacts.
6. Send the first test only to your own number.

`blip-setup` writes `~/.config/blip/bridge.conf`, appends a ControlMaster `Host` block only when that host has none, and installs shims in `~/bin`, backing up anything those names replace. It creates `~/.ssh/blip_ed25519` and enrols it on the gateway as `from="<addresses>",restrict,command="$HOME/.blip/bin/blip-dispatch"` when the Mac is reached over Tailscale: no shell, no forwarding, no pty, and only the bridge tools. Re-run `blip-setup` if this machine's Tailscale address changes.

Full Disk Access is on `sshd-keygen-wrapper`, not on one key. After it is granted, every key that can open a shell on that gateway account can read `chat.db`. The everyday key used to enrol Blip keeps that access. Keep other authorized keys few. Upstream describes this in [docs/SECURITY.md](https://github.com/nixfred/blip/blob/main/docs/SECURITY.md).

## Verify

After `omarchy-restart-shell`, the speech bubble is on the bar.

```bash
~/bin/imsg chats 1 >/dev/null
ssh GATEWAY_USER@GATEWAY_MAGICDNS_NAME 'python3 "$HOME/.blip/bin/blip-check"'
```

`blip-check` should be all green. Do not pass `--markread` unless you are at the Mac and want that prompt. Send a message to your own number and confirm it arrives.

The confined key must refuse a shell:

```bash
ssh -i ~/.ssh/blip_ed25519 -o IdentityAgent=none GATEWAY_USER@GATEWAY_MAGICDNS_NAME true
```

That command fails. That is the expected result.

Package presence, a saved `blip: true`, and a green fixture do not prove the grants, the bar, or a message round-trip. Those stay first-run acceptance. `docs/status.md` is updated only after that acceptance.

## Update

`update-all` fast-forwards Blip only in an interactive terminal:

```bash
omarchy plugin update nixfred.blip
```

It does not pass `--yes`, so the diff stays on screen. Without a terminal, `update-all` skips the plugin update and continues. `groundwork-update` does not update plugins. `omarchy update` does not either.

If the diff touches `bridge/`, `update-all` reminds you to re-run `blip-setup`. It does not run it. Then restart the shell with `omarchy-restart-shell`. A hot reload leaves the plugin's IPC on the old instance.

## Remove

`--without-blip` does not uninstall Blip. Upstream's removal steps are [Removing Blip](https://github.com/nixfred/blip#removing-blip). In short, the pieces are:

1. The plugin and bar widget: `omarchy plugin remove nixfred.blip --yes`, then `omarchy-restart-shell`. `omarchy plugin disable nixfred.blip` takes it off the bar and keeps the checkout.
2. The `~/bin` shims (`imsg`, `imsg-send`, `imsg-read`, `imsg-react`, `contacts`, `contact-save`), plus any `.pre-blip` backups you want to restore.
3. `~/.config/blip`, `~/.local/state/blip`, `~/.cache/blip`, and the runtime directory under `$XDG_RUNTIME_DIR/blip`. The attachment cache holds real media.
4. `~/.ssh/blip_ed25519` and its `.pub`. A ControlMaster `Host` block in `~/.ssh/config` is optional to delete.
5. On the gateway, `~/.blip` and the single `authorized_keys` line that runs `blip-dispatch`. The everyday key stays.
6. Privacy grants, only if you want them gone. They belong to `sshd-keygen-wrapper`, so other SSH tools may still need them. Remove that entry under Full Disk Access, Automation, Contacts, and Accessibility.

Nothing in that list writes or deletes `chat.db`.

To stop offering Blip on later installs, run `./install.sh --without-blip` after the checkout is gone. That records the preference as off.

## Troubleshooting

**Asleep.** The widget dims, and `~/bin/imsg` exits 69. Messages are still on the gateway and on the phone. On the gateway, `pmset -c sleep 0` keeps it awake on AC power and leaves battery sleep alone. The gateway issue owns that setting. A closed lid still sleeps unless an external display is attached. `pmset -c disablesleep 1` would ignore the lid and is not part of this setup: a laptop in a bag would stay awake.

**Login window.** Blip can read and cannot send. The gateway needs a logged-in desktop, lid open or an external display attached, on AC power.

**Lock screen.** Messages keep flowing. Only the per-thread read push stops, and `blip-check` can report the screen as locked. This layer does not turn the screen saver off. Unlock the Mac, or decide that yourself on the gateway.

**`OMARCHY_PATH is not set`.** The add was run outside the desktop session. The clone may exist. Re-run the printed `omarchy plugin add … --enable --yes` from a terminal Omarchy opened. If the id is already installed, do not add it again; enable it from that session only if the bar does not show it.

**bun and 16 KiB pages.** Setup prints [oven-sh/bun#17627](https://github.com/oven-sh/bun/issues/17627) and does not enable the widget. Fix or replace the bun build, then rerun `./install.sh --with-blip`.

**Automation denial.** An ignored prompt is a denial. On the Mac, `tccutil reset AppleEvents` clears every app's Automation grants. Run `blip-setup` again and click Allow within about two minutes.

**Allowlist toasts.** `~/.config/blip/allowlist.json` is strict JSON. A file that does not parse toasts nothing. Check it with `jq . ~/.config/blip/allowlist.json`.

## Paths this layer does not use

BlueBubbles can run its server with SIP on. Tapbacks, edits, effects, and replies there need its Private API, which requires SIP off. Its client is not installed here. Turning SIP off, Beeper (its iMessage connection is the Mac desktop app), OpenBubbles, and the marketplace iPhone-relay plugins are out of scope.
