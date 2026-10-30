# 0016. Desktop-launched apps put mise's shims ahead of system binaries

Checked 2026-10-30 on Omarchy edge with `nodejs-lts-jod` 22 installed.

## Problem

Omarchy's `/usr/share/omarchy/default/bash/env-bootstrap`, which `/usr/share/uwsm/env.d/10-omarchy` sources for the Hyprland session, appends `~/.local/share/mise/shims` after system binaries "so system binaries keep precedence". Interactive shells run `mise activate`, which puts mise first, but nothing does that for apps started from the launcher or a keybinding: T3 Code, Claude, Codex and the agents they spawn. They resolve `/usr/bin/node` (Node 22, a dependency of `bitwarden-cli` that cannot be removed) instead of the Node a repository's `.node-version` names. A repository that requires Node 26 then fails only under agents, as Compass's readiness check did.

## Decision

`scripts/groundwork-session-env.py`, called by `scripts/apply-layer.sh` on setup and on `groundwork-update`/`update-all`, does two things.

**Session path.** It writes `~/.config/uwsm/env.d/50-groundwork-mise`, a file this layer owns whole. Omarchy's own comment names `~/.config/uwsm/env.d/*` as the preferred place for user session changes, uwsm sources it after `10-omarchy`, and a whole-file drop-in needs no merge into a file the user may keep by hand. The shell fragment moves the shims directory ahead of system binaries and removes any later duplicate. It keeps `~/.local/share/mise/command-wrappers/bin` first when that directory is on `PATH`: Omarchy puts it first on purpose so the account-switching wrappers for `claude`, `codex` and `grok` win over mise's shims of the same names. A plain "prepend the shims" line would bypass them. The fragment honors `MISE_DATA_DIR` and `XDG_DATA_HOME`, uses POSIX `sh` only, and leaves no variables behind. It takes effect at the next login.

All shims, not only `node`, `npm`, `npx`, `corepack` and `pnpm`. Interactive shells already put every mise tool first, so agents and terminals then agree on what `gh`, `claude` or `bun` is. A narrower set would need a second directory of links to keep in step with mise, and would still leave the same split for every other tool.

**Node pin.** Omarchy declares `node = { version = "latest", lazy = true }` in `/etc/mise/conf.d/omarchy-tools.toml`, which moves to the next major on its own. The layer adds `node = "26"` (the first Node line that is LTS, 26.13.0 on 2026-10-28) to `[tools]` in `~/.config/mise/config.toml`, creating the file if it is absent. It writes only when neither that file nor `~/.config/mise/conf.d/*.toml` sets `node` at all, so a user's `"24"`, `"lts"`, `"latest"` or table entry is kept. Edits are text insertions checked by parsing the result: only `tools.node` may differ. An existing file gets a numbered backup (`config.toml.~N~`). An invalid or symlinked file is left alone and reported. After a new pin it runs `mise install node` with a deadline. Moving to the next line is a deliberate edit of `NODE_PIN` in the helper, in the same change that moves Groundwork's repositories.

## Boundaries

- Nothing is written to `/usr`, `/etc` or the Omarchy checkout, and nothing is stowed.
- Delete the `Managed by Groundwork` header line from the drop-in to take it over; the layer then leaves the file alone. Delete the file only together with that edit, or the next apply writes it again.
- A hand-written `~/.config/uwsm/env` that prepends the shims is not edited. It still works. The drop-in runs after it and puts the wrappers back in front.
- `/usr/bin/bw` is `#!/usr/bin/env node`, so it now runs on mise's Node. `bw` 2026.2.0 starts and reports its version under Node 26.13.0.
- This is a workaround for an Omarchy ordering choice. The upstream report is drafted in the pull request that introduced this decision; the file here can be removed if Omarchy changes the order.
- Fixture tests (`tests/test_session_env.py`) source the real fragment in `sh`, `dash` and `bash` and run the helper against throwaway homes. They do not prove a real login: after the next login, check `systemctl --user show-environment | grep ^PATH` and that a desktop-launched terminal's `command -v node` is under `~/.local/share/mise/shims`.
