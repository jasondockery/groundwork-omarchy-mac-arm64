# Omarchy shell plugins: agent rules

Moved from `AGENTS.md`. Decision: [0010](../decisions/0010-omarchy-plugins-from-shared-list.md).

- Shell plugins come from the shared groundwork-core list (`omarchy/plugins.json`) and are applied once by `scripts/omarchy-plugins.py` after the Hyprland reload. `GROUNDWORK_PLUGIN_LIST` overrides that https source.
- `omarchy-plugins.exclude.json` and `~/.config/groundwork/omarchy-plugins.exclude.json` skip ids, each with a reason.
- `update-all` updates listed checkouts; `groundwork-update` leaves their revisions unchanged.
- Do not name plugin ids in this repo, do not Stow `~/.config/omarchy/shell.json`, do not re-enable a plugin whose directory already exists or a built-in already recorded, and keep plugin swaps in the list's `replaces` field rather than in code.
- Undoing a swap turns a replaced id back on only when this layer turned it off.
- A fresh checkout that enable refuses as not known gets one rescan and retry; if the shell still does not list it, the checkout stays recorded `enablePending` and one line reports it.
- Session-restoration plugins remain outside the defaults.

## Trackpad scroll precedence

- If `mouse.lua` or `omacontrol.lua` sets `touchpad.natural_scroll` differently from the stowed `input.lua`, the file loaded later wins. If `hyprland.lua` does not load that file, the stowed `input.lua` still applies.
