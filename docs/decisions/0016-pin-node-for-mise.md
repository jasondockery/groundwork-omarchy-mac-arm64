# 0016. Pin Node for mise; the agent PATH problem is upstream

Checked 2026-10-30 on Omarchy edge (`omarchy-settings-dev` 4.0.0.r7437, installed 2026-10-30 00:39 local time), mise 2026.10.23, `nodejs-lts-jod` 22 (a dependency of `bitwarden-cli`, so it stays).

## Problem

Agents started from T3 Code (Claude, Codex, Grok) run `node` as `/usr/bin/node` 22 instead of mise's Node. A repository whose `.node-version` needs 26 fails only there, as Compass's readiness check did.

## What the evidence shows

This decision first assumed the desktop session put mise's shims after system binaries. On current Omarchy that is false.

- `/usr/share/uwsm/env.d/10-omarchy` ends with `omarchy-cmd-present mise && eval "$(mise activate bash --shims)"`. The session `PATH` is already `command-wrappers/bin : shims : … : /usr/bin` (checked with `systemctl --user show-environment` and the compositor's environ). An earlier version of this change wrote a `~/.config/uwsm/env.d` drop-in that only repeated this, and it was removed.
- T3 Code does not lose it either. The Electron main process and the backend both run the user's login shell with `-ilc` (the `installPosixEnvironment` and `hydratePosixPath` code in the app) and merge that `PATH` ahead of the inherited one. The running backend's `PATH` on this machine starts `command-wrappers/bin : mise/installs/… : mise/shims : …`, with the tool and shim directories before `/usr/bin`.
- The `PATH` changes in the last step, when T3 starts the agent. T3 runs `systemd-run --user --scope … ~/.local/share/mise/command-wrappers/bin/claude`. That file is a symlink to `/usr/bin/mise`. `systemd-run` passes `PATH` through unchanged (checked). The agent process (and the shells it starts) has `wrappers : … : /usr/bin : … : mise installs : shims`, while the backend that started it had the mise directories first. So the reorder happens between `systemd-run` and the agent, in the mise wrapper process.

Reproduction: `tests/fixtures/mise-wrapper-path-repro.sh` (a throwaway `MISE_DATA_DIR`, a wrapper symlink named `claude` to mise, a stub tool that prints its `PATH`, a config that declares the tool; it touches only a temp directory). On mise 2026.10.23 it prints `wrappers : /usr/bin : /bin : installs : shims` for the input `wrappers : installs : shims : /usr/bin : /bin`. **The exact trigger is not pinned down.** A reviewer's variant with a `[wrappers]` entry in the config kept the install directory ahead of `/usr/bin` and did not reproduce it. What is established is the live evidence above: the T3 backend's `PATH` has the mise directories before `/usr/bin`, and the agent process started through `command-wrappers/bin/claude` has them after. Why mise orders them that way in some setups is for the mise report to settle. Because the rewrite happens inside the wrapper process, a session `PATH` cannot undo it.

## Decision

- **No session `PATH` change.** Omarchy already does it, and it cannot fix the agent case.
- **Pin Node.** `scripts/groundwork-node-pin.py`, called by `scripts/apply-layer.sh` on setup and update when `mise` is installed, adds `node = "26"` (the first Node line that is LTS; 26.13.0 on 2026-10-28) under `[tools]` in the user's global mise config. Omarchy's system config declares `node` as `latest`, which would move to the next major without notice. The pin is not what makes agents see Node 26; it keeps `mise` from moving off it. It writes only when none of these set `node`: the global config (`MISE_GLOBAL_CONFIG_FILE`, else `config.toml` in `MISE_CONFIG_DIR`, `$XDG_CONFIG_HOME/mise` or `~/.config/mise`), `config.local.toml`, `config.<env>.toml`, `conf.d/*.toml` beside it, `~/.tool-versions`, `~/.node-version`, or a home-level file that overrides global config (`~/.mise.toml`, `~/mise.toml`, `~/.mise.local.toml`, `~/mise.local.toml`, `~/.config/mise.toml`). An entry of any kind (`"24"`, `"lts"`, `"latest"`, a table) is kept. The edit is a text insertion that is parsed and compared with the original before it is written; only `tools.node` may differ. An existing file gets a numbered backup (`config.toml.~N~`). An invalid, non-TOML or symlinked file is left alone and reported.
- **Install it and say so.** After a new pin the installer runs `mise install node@26` (the helper's `version` output) with a deadline. If that fails it prints that Node is pinned but not installed and gives the command. If `mise` is absent it prints that the pin was skipped.
- Moving to another line is a deliberate edit of `NODE_PIN`, in the change that moves Groundwork's repositories.

## What remains broken

Agents started through the claude/codex/grok wrappers still get `/usr/bin/node` first. This looks like mise behavior; the upstream report is drafted in the pull request, not filed. Until it is fixed, a repository check that needs a specific Node should resolve it through mise (`mise exec -- node`, or `mise x node@26 -- …`) rather than a bare `node`. Compass's readiness check is the place for that; this repository does not change it.

`/usr/bin/bw` is `#!/usr/bin/env node`; `bw` 2026.2.0 starts and prints its version under Node 26.13.0.

## Undo

Remove the `node` line from `[tools]` in your mise config, or restore `config.toml.~N~`. The layer then adds it again on the next apply unless another mise config of yours sets `node`; set it there (for example `node = "latest"`) to opt out.
