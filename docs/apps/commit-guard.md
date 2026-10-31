# Commit identity guard: agent rules

Moved from `AGENTS.md`. Decision: [0017](../decisions/0017-commit-identity-guard.md). Shared contract: groundwork-core `docs/commit-identity.md`.

- The commit identity guard is installed from the `core` pin. `scripts/groundwork-commit-guard.py` links `core/commit-guard/commit-identity-guard.sh` into `~/.config/groundwork/git-hooks` (never a copy).
- It scopes `core.hooksPath` to repositories with an owned GitHub remote through `includeIf` in `~/.config/git/config` (never globally, git 2.36+, nothing written when a hooksPath is already set), and forwards every hook to the repository's own.
- It fills in Claude Code `attribution.commit` and `attribution.pr` as empty only where unset (numbered backup), and creates an empty `~/.config/groundwork/commit-denylist` when missing.
- Never write a name, an organization, or a denylist value into this repo.
- `groundwork-commit-guard off` (the `commit-guard-off` file) is the opt-out; `doctor` reports.
