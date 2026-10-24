# 0004. Remote access and AI workbench

Use Tailscale for private recovery and Herdr/T3 Code for AI-assisted development. `./install.sh` installs Herdr and Tailscale, enables `tailscaled`, and reconciles the official T3 Code Nightly CLI. It does not sign in to the user's tailnet or enable a public endpoint.

Follow [private access between your machines](../remote-access.md) for Omarchy installation, and the [shared guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/tailscale.md) for policy, platform differences, and two-way acceptance checks. After reviewing that policy, complete these steps in the local terminal:

```bash
sudo tailscale up --timeout=5m
sudo tailscale set --ssh
tailscale status
```

Use the device's MagicDNS name from another approved tailnet device. Keep personal-device key expiry enabled, use device approval and multi-factor authentication, and use owner-scoped grants plus SSH check mode. Tailscale SSH needs both network access to port 22 and an SSH policy; a default allow-all rule is broader than this setup needs. Do not put device names, addresses, auth keys, or account identifiers in this repo. GitHub SSH remains a separate credential and continues to use the user's own `~/.ssh/config` and key.

On the Omarchy desktop, the edge image already includes Herdr and tmux. Start Herdr with Omarchy's `Super+Ctrl+Return` shortcut, or start it from a terminal. Keep Omarchy's stock `Ctrl+Space` tmux prefix, `Super+K` desktop help, and `Super+Alt+K` tmux help. T3 Code's `t3` command starts the local web interface; install and authenticate at least one coding-agent provider separately before creating agent threads.

WayVNC is now included by default for the requested Mac-to-Omarchy screen sharing. Its user service starts with the desktop, generates a local password once, and listens only on an online Tailscale IPv4 address. Apple's client needs legacy VNC authentication; Tailscale supplies transport encryption. See [automatic desktop sharing](../remote-access.md#automatic-desktop-sharing) for access policy, credentials, startup, acceptance, and removal. `--without-screen-sharing` leaves an existing setup unchanged. RustDesk, Moonlight, Sunshine, and external KVM devices remain outside the defaults. See the shared [Groundwork AI tools and remote access guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/ai-tools-and-remote-access.md) and Tailscale's [security best practices](https://tailscale.com/docs/reference/best-practices/security).

Apple Screen Sharing sends Command as Alt and Option as Super. The WayVNC process loads a private XKB option, `groundwork:apple_vnc`, through `XKB_CONFIG_EXTRA_PATH`, so Command sets Super and Option sets Alt on the virtual keyboard WayVNC publishes. Existing Super and Alt bindings then match the viewing keyboard's labels. Shift, Control, attached keyboards, and Omarchy's stock bindings stay as shipped. Right Alt remains AltGr where a layout uses it for level 3. The password file is unchanged.
