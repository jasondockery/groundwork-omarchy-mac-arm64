# EasyEffects: agent rules

Decision: [0024](../decisions/0024-easyeffects-equalizer.md). User guidance: [apps guide](../apps.md#easyeffects).

- EasyEffects is on by default (`omarchy pkg add easyeffects` and `lsp-plugins-lv2`). `--without-easyeffects` records `easyeffects: false` in `omarchy.json` and leaves an existing install and user unit alone.
- Do not write `~/.config/easyeffects/` (presets, autoload, or `config.json`). The empty upstream pipeline is the flat default. Per-output presets stay on the machine.
- The only override is `config/easyeffects.service`, copied to `~/.config/systemd/user/easyeffects.service`. It lists only the unit keys that differ from "no unit", each with a reason. Do not add a Hyprland bind or a second autostart file.
- Do not run `easyeffects --help` or any flag that could self-install plugins or community presets.
- Do not put a Bluetooth device name, MAC, or speaker model into this repo. Autoload mappings are local.
