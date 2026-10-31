# Cursor: agent rules

Moved from `AGENTS.md`. Decisions: [0009](../decisions/0009-install-cursor-through-omarchy.md) and [0014](../decisions/0014-offer-migration-to-omarchy-installers.md#cursor-report-only). Manual steps: [apps guide](../apps.md#cursor).

- `./install.sh` installs Cursor when `cursor` is absent, through `omarchy-install-cursor` on older Omarchy Mac builds and otherwise `omarchy pkg add cursor-bin`. An existing `cursor` command is kept.
- When `~/.config/Cursor/argv.json` has no `password-store`, setup adds `gnome-libsecret` and leaves every other key unchanged.
- Cursor is report-only for migration: never touch a pacman-owned `cursor-bin`, and only print a manual-move notice for a `cursor` no package owns.
