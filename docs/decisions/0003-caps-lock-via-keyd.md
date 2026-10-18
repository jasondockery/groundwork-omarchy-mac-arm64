# 0003. Caps Lock tap is Escape, hold is Control, via keyd

Make this the default for every user of this opinionated configuration, matching the Karabiner tap/hold habit on macOS. The foundation is learning Omarchy; this deliberate keyboard change keeps Escape and Control comfortable in editors, terminals, and agent tools.

Use [keyd](https://github.com/rvaiya/keyd#quickstart), a Linux input remapper that works below Wayland and terminal apps. It is [packaged for aarch64](https://archlinuxarm.org/packages/aarch64/keyd). The [Omarchy community discussion](https://github.com/omacom/omarchy/discussions/1383) uses the same mapping. XKB `kb_options` can change a key's role but does not implement this tap/hold behavior.

The existing `keyd/default.conf` contains:

```ini
capslock = overload(control, esc)
```

`./install.sh` installs keyd through `omarchy pkg add`, applies the config to `/etc/keyd/default.conf`, enables the service, and runs `sudo keyd reload`. A different existing config gets a numbered backup before replacement; identical reruns do not create backups. The former `--with-keyd` flag remains compatible. `--without-keyd` skips setup and leaves an existing keyd configuration and service alone.

## Compose and keyboard scope

This is a repo default, not Omarchy's upstream default. On [2026-10-11 DHH changed Omarchy](https://github.com/omacom/omarchy/pull/14939) to normal Caps Lock and Shift+Caps Lock for Compose. Older versions used Caps Lock alone for Compose. keyd consumes the physical Caps Lock before XKB sees it, replacing both behaviors. Physical Escape and Control are unchanged.

If you need Compose, put `kb_options = "compose:ralt"` inside the existing `hl.config` input table in `packages/hypr/.config/hypr/input.lua`. Preserve any other layout options you use, and do not combine it with another option that assigns Caps Lock. This makes Right Alt Compose, so choose another supported Compose key if you use Right Alt as AltGr. After applying the file, run `hyprctl reload` and `hyprctl configerrors`, then test a Compose sequence.

The `[ids]` wildcard handles built-in and external keyboards. Some mice also emit keyboard events and can match it. If enabling keyd disrupts a mouse, use `sudo keyd monitor` to identify its device ID, add an exclusion such as `-vvvv:pppp` under `[ids]` in the local config (replace the placeholder with the actual ID), and run `sudo keyd reload`. Keep machine-specific IDs outside this shared repo. Other `/etc/keyd/*.conf` files can select devices more specifically; inspect those if a keyboard does not use this default. See the [keyd manual](https://github.com/rvaiya/keyd/blob/master/docs/keyd.scdoc).

## Verify and recover

Tap Caps Lock to leave Neovim insert mode. Hold Caps Lock with `L` to send `Ctrl+L`; hold it with Space to send the tmux/Herdr prefix. Check service status with `systemctl status keyd` and errors with `sudo journalctl -u keyd -b`.

To undo this mapping when keyd is used only for this repo, run `sudo systemctl disable --now keyd`. If keyd previously managed other mappings, restore the numbered `/etc/keyd/default.conf.~N~` backup and run `sudo keyd reload` instead. Restore the backup by copying it over `/etc/keyd/default.conf`; retain other keyd files. keyd's emergency `Backspace+Escape+Enter` chord terminates the daemon if a bad mapping prevents normal input.
