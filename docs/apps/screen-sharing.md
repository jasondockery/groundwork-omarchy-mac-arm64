# Desktop sharing (WayVNC): agent rules

Moved from `AGENTS.md`. User guidance: [remote access](../remote-access.md). Decision: [0004](../decisions/0004-remote-access.md).

- Default desktop sharing uses WayVNC via the Groundwork-owned user service/helper. Generate and preserve local credentials, wait for an online Tailscale IPv4 address, and bind only that address.
- When its unit, helper, or WayVNC config changed and `WAYLAND_DISPLAY` is unset (SSH or cron), an inactive unit stays stopped and the restart record is cleared. When `WAYLAND_DISPLAY` is set, the unit restarts even if it is inactive. Print the session-drop warning only when a restart actually runs.
- Never expose legacy Apple-compatible VNC authentication on LAN/all interfaces, publish credentials, disable the lock screen, or claim pre-login/headless recovery.
- `--without-screen-sharing` leaves an existing setup alone; shared port policy stays in Core.
- WayVNC's private keymap makes Apple Screen Sharing's Command follow existing Super bindings and Option follow existing Alt bindings. Leave Omarchy's global keymap and attached keyboards unchanged.
