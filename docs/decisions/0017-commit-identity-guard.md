# 0017. Install the commit identity guard from groundwork-core

The contract is groundwork-core `docs/commit-identity.md` (v0.9.0): no `Co-authored-by` trailer, and no author or committer that matches the local denylist. This layer installs the reference guard from the `core` pin.

## Decision

- **Scope is the user, through `core.hooksPath`.** `scripts/groundwork-commit-guard.py`, called by `scripts/apply-layer.sh` on setup and update, puts hooks in `~/.config/groundwork/git-hooks`. `commit-identity-guard.sh` there is a symlink to the pinned `core/commit-guard/commit-identity-guard.sh`, so the pin is the only copy. `core.hooksPath` is written to `~/.config/git/config`, which `~/.gitconfig` and `~/.gitconfig.local` override. It is written only when no `core.hooksPath` is set anywhere. When one is, setup prints one line and leaves it, and the guard hooks are not active until the user chains `commit-msg` and `pre-push` from it. A global path reaches every repository, including work repositories, so the next two points limit that.
- **Per-repo hooks keep running.** Every standard client hook name in the directory is a symlink to one dispatcher. For `commit-msg` and `pre-push` it runs the guard first (pre-push input is buffered and passed to both). Then it runs the repository's own `.git/hooks/<name>`. A repository that sets its own local `core.hooksPath` bypasses the global hooks; `doctor` says so.
- **Opt-out, recommended and implemented.** The guard is on by default, because the rule is meant to hold on every Groundwork machine. It changes global git behaviour and a work commit can legitimately carry another organization's trailer, so `groundwork-commit-guard off` creates `~/.config/groundwork/commit-guard-off`. The hooks check that file at run time, `apply` installs nothing and changes nothing while it exists, and `groundwork-commit-guard on` removes it. `GROUNDWORK_COMMIT_GUARD=off` bypasses one command. A blocked commit prints both ways out. The file is the saved choice, so `omarchy.json` is not involved.
- **Claude Code attribution.** `attribution.commit` and `attribution.pr` are set to `""` in `~/.claude/settings.json` (`CLAUDE_CONFIG_DIR` honored) only for keys that are absent. The merge keeps every other key, the file mode, and a symlink; writes a numbered `settings.json.bak.N` first; and leaves invalid JSON untouched.
- **Denylist.** An empty `~/.config/groundwork/commit-denylist` (`XDG_CONFIG_HOME` and `GROUNDWORK_COMMIT_DENYLIST` honored) is created when missing. This repository never supplies a value; names stay in that local file.
- **Doctor.** `groundwork-commit-guard doctor` (copied to `~/.local/bin` without replacing a foreign file) reports the hooks path and opt-out state, then the guard's own `doctor` output.

## Not covered

Cursor attribution is a UI setting that `doctor` cannot read. Pushing straight to a URL checks all reachable history (see the core doc).
