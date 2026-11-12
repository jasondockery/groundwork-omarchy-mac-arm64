# EasyEffects: agent rules

Decision: [0024](../decisions/0024-easyeffects-equalizer.md). User guidance: [apps guide](../apps.md#easyeffects).

- EasyEffects is on by default (`omarchy pkg add easyeffects` and `lsp-plugins-lv2`). `--without-easyeffects` records `easyeffects: false` in `omarchy.json` and leaves an existing install and user unit alone.
- Do not write `~/.config/easyeffects/` (presets, autoload, or `config.json`). The empty upstream pipeline is the flat default. Per-output presets stay on the machine.
- The only override is `config/groundwork-easyeffects.service`, copied to `~/.config/systemd/user/groundwork-easyeffects.service`. It lists only the unit keys that differ from "no unit", each with a reason, plus `ExecStop=easyeffects --quit` so the process stops with the session. Setup enables that unit for the next desktop login. It does not `start`, `enable --now`, or `try-restart` it. A pre-existing `easyeffects.service` is left alone. A file already at the Groundwork unit path is replaced only when it contains the ownership marker; otherwise it is left alone and setup stops. A failed `daemon-reload` or `enable` is recorded under `~/.local/state/groundwork/easyeffects-enable-needed` so the next run finishes the enable. Do not add a Hyprland bind or a second autostart file.
- Do not run `easyeffects --help` or any flag that could self-install plugins or community presets.
- Do not put a Bluetooth device name, MAC, or speaker model into this repo. Autoload mappings are local.
