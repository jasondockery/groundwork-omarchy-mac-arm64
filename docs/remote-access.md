# Tailscale on Omarchy Mac

The [shared Tailscale guide in groundwork-core](https://github.com/jasondockery/groundwork-core/blob/main/docs/tailscale.md) owns tailnet policy, MFA, device approval, key expiry, MagicDNS, platform capabilities, and two-way connection checks. Read it once for all your machines. This page owns only the Omarchy setup steps.

## Install and sign in

`./install.sh` installs Tailscale from the Arch package feed and enables `tailscaled`. Complete interactive sign-in on the Omarchy machine:

```bash
sudo tailscale up --timeout=5m
```

Open the URL it prints and approve the device in your tailnet if required. If it times out waiting for approval, finish approval and retry. On an already configured device, preserve its current options if `tailscale up` asks for them; do not add `--reset` to silence that message.

Review the shared guide's owner-scoped network grant and SSH check-mode policy before enabling inbound Tailscale SSH:

```bash
sudo tailscale set --ssh
tailscale status
```

`set` changes only the named preference. Tailscale SSH handles port 22 on the Tailscale interface; a public OpenSSH daemon, router forwarding, exit node, and Funnel are unnecessary. Existing ordinary SSH on other interfaces is independent. No tailnet policy, account identifiers, reusable auth keys, or SSH keys are stored here.

## Verify and use

```bash
systemctl is-active tailscaled
tailscale status
```

From another approved machine signed in to the same personal tailnet:

```bash
tailscale ping MACHINE
ssh LOCAL_USER@MACHINE
```

Replace the uppercase placeholders with the Omarchy device's MagicDNS name and local login. Ping checks the Tailscale path, not the SSH access policy. In the remote shell, confirm `hostname`, then run `tmux attach` or `herdr`. Detach with `Ctrl+Space`, then `d`. Test access in both directions using each destination OS's supported SSH setup; the macOS app and Windows host use ordinary SSH servers rather than Tailscale SSH.

Keep T3 Code Nightly on loopback. Use the shared guide's SSH-tunnel pattern with T3's actual listening port, so you can inspect agents remotely without exposing its web interface.

## Automatic desktop sharing

The default installer also installs WayVNC and enables the user service `groundwork-screen-share.service`. It starts with your graphical desktop, waits for Tailscale to be signed in and online, and binds only that machine's current Tailscale IPv4 address on TCP 5900. The listening socket is also restricted to the `tailscale0` interface with `SO_BINDTODEVICE` before WayVNC inherits it; an unsupported or denied interface binding fails closed. This rejects LAN-routed traffic to the Tailscale address even without UFW. The socket sets `SO_REUSEADDR` before binding, so a restart can take the same address while a previous client connection is in `TIME_WAIT`. This service waits when the previous listener didn't set `SO_REUSEADDR`, then binds. The journal line `Screen sharing is waiting for TCP 5900 to be released.` means that wait is in progress. It stops the listener on logout from Tailscale and rebinds when the address changes. It never falls back to a LAN address or `0.0.0.0`. `./install.sh --without-screen-sharing` skips this setup and leaves an existing service unchanged.

In a local terminal, show the generated password:

```bash
groundwork-screen-share password
```

Save it in your password manager. Setup preserves it on rerun and stores it only in `~/.config/groundwork/remote-desktop/wayvnc.conf`, mode 0600 inside a private directory. Do not paste that file or password into GitHub, chat, or installation logs. The password command refuses captured output.

On a Mac, use Finder → Go → Connect to Server and enter `vnc://MACHINE`, replacing `MACHINE` with this Mini's MagicDNS name or its current Tailscale address. Use the generated VNC password, not the Linux login password. Apple's Screen Sharing client requires [WayVNC's legacy VNC authentication mode](https://github.com/any1/wayvnc#des-authentication-legacy), which uses only eight password characters and does not encrypt the VNC session itself. This service requires Tailscale's encrypted connection and never exposes that mode on the LAN.

Apple Screen Sharing sends Command as Alt and Option as Super, because that is how its VNC keyboard message addresses a Mac. Filling the viewer's display does not change those symbols. This service gives WayVNC a private keymap, used only by the virtual keyboard WayVNC creates, that makes Command set Super and Option set Alt. The translation is per WayVNC process, not per viewer: WayVNC applies that keymap to every client and to QEMU extended key events, so a Linux or Windows viewer gets the swapped modifiers as well. Set `GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS=0` in the user service environment to skip appending the option and leave WayVNC's own modifiers in place; any other value leaves the swap on. Existing Omarchy bindings then match the labels on the viewing keyboard: Command+1 through Command+9 select workspaces, Command+Tab goes to the next workspace, and Option+Tab goes to the next window. Command+Minus expands the window left, Command+Equal shrinks it left, and Command+Shift+Equal expands it down. Screen zoom is Command+Ctrl+Z; press it again to zoom further. Command+Ctrl+Option+Z zooms fully out. Shift and Control are unchanged. Right Alt stays AltGr on layouts that use it for level-3 characters. A keyboard attached to the Omarchy machine keeps Omarchy's keymap, and Omarchy's stock bindings are left in place. See [Learn Omarchy's keys](keybindings.md) for the same chords.

The keymap is installed when the user service starts WayVNC. `update-all` and `groundwork-update` write a restart record at `~/.local/state/groundwork/screen-share-restart-needed` as soon as the unit, helper, or WayVNC config changes, before reloading the user service manager. They restart `groundwork-screen-share.service` when `WAYLAND_DISPLAY` is set or when `ActiveState` is `active`, `reloading`, or `failed`, including an update over SSH or from cron, and print `Restarting screen sharing; an active remote session will drop.` The record is removed only after that restart succeeds. An `inactive` unit stays stopped and the record is removed; the next desktop login starts the new files. `activating` and `deactivating` leave the record in place, as does any other result from that check. When the files are unchanged and no restart is pending, the current session keeps running. To turn the Apple modifier swap off, add a drop-in and restart:

```bash
mkdir -p ~/.config/systemd/user/groundwork-screen-share.service.d
printf '%s\n' '[Service]' 'Environment=GROUNDWORK_SCREEN_SHARE_APPLE_MODIFIERS=0' > ~/.config/systemd/user/groundwork-screen-share.service.d/apple-modifiers.conf
systemctl --user daemon-reload
systemctl --user restart groundwork-screen-share.service
```

Remove that drop-in, run `daemon-reload`, and restart the service to turn the swap back on. Connect again and press Command+1. Workspace 1 should focus. Option+1 selects a tmux window or Herdr tab instead.

The [shared guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/tailscale.md#agent-tools-and-screens) owns the owner-scoped desktop port grant. TCP 5900 needs a network grant in addition to any SSH rule; an SSH check-mode rule does not authenticate VNC. Setup adds an idempotent UFW allow rule for TCP 5900 on `tailscale0` only when UFW is installed. It does not disable the firewall or open LAN/public interfaces. Another firewall needs an equivalent interface-scoped rule. No public port forwarding or Funnel is needed.

Check startup and troubleshoot locally:

```bash
systemctl --user status groundwork-screen-share.service
journalctl --user -u groundwork-screen-share.service -b --no-pager
ss -ltn 'sport = :5900'
```

After desktop login and Tailscale enrollment, the listener should show only the Tailscale IPv4 address. Then verify viewing and control from another approved computer. A running service while logged out of Tailscale is waiting, not proof of a reachable desktop. It shares your existing logged-in desktop; it does not unlock disk encryption, boot a powered-off Mac, create a headless display, or bypass the lock screen. Keep the physical display attached for this normal path.

To turn sharing off, run `systemctl --user disable --now groundwork-screen-share.service` and use `--without-screen-sharing` on later installer runs. Local credentials remain for recovery; remove only the private config if you deliberately want to discard them. The service and helper use Groundwork-specific names and do not replace an existing generic WayVNC config.

## Update and recover

`omarchy update` owns the Arch Tailscale package; Git updates only this configuration layer. Use `sudo journalctl -u tailscaled -b` for daemon errors, then check sign-in, device approval, DNS settings, key expiry, and the effective tailnet policy. Avoid changing those through your only remote connection. Disabling inbound Tailscale SSH uses `sudo tailscale set --ssh=false`; it does not disable ordinary SSH or Tailscale networking.

The [shared guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/tailscale.md) has the current security and recovery details, including sleep/reboot limitations and platform-specific destinations. Never mark this machine remotely accepted until the cross-machine SSH test has actually passed.
