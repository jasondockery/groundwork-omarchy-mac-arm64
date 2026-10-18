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

Keep T3 Code Nightly on loopback. Use the shared guide's SSH-tunnel pattern with T3's actual listening port, so you can inspect agents remotely without exposing its web interface. Physical desktop sharing is optional and separate from terminal access.

## Update and recover

`omarchy update` owns the Arch Tailscale package; Git updates only this configuration layer. Use `sudo journalctl -u tailscaled -b` for daemon errors, then check sign-in, device approval, DNS settings, key expiry, and the effective tailnet policy. Avoid changing those through your only remote connection. Disabling inbound Tailscale SSH uses `sudo tailscale set --ssh=false`; it does not disable ordinary SSH or Tailscale networking.

The [shared guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/tailscale.md) has the current security and recovery details, including sleep/reboot limitations and platform-specific destinations. Never mark this machine remotely accepted until the cross-machine SSH test has actually passed.
