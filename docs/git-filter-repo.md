# git-filter-repo for history purges (opt-in)

When a credential or private reference has to leave a repository's history, the procedure is the shared runbook, not this page: [groundwork-core `docs/history-purge.md`](https://github.com/jasondockery/groundwork-core/blob/v0.8.1/docs/history-purge.md). Read it before any rewrite. This page records only the Omarchy install: what this layer installs, when, and how you remove it.

`git-filter-repo` is not a default. `./install.sh` and `groundwork-update` do not install it unless you opt in. Setup never runs a history rewrite, a force-push, or a tag change.

## Turn it on

```bash
./install.sh --with-git-filter-repo
```

That yes is for this machine and is saved with your other setup choices, so later runs and `groundwork-update` reuse it. The helper `scripts/install-git-filter-repo.sh` then runs `mise use -g pipx:git-filter-repo`, the runbook's per-user line for mise. It installs into your home directory, with no `sudo` and no pacman. mise's pipx backend needs `uv` or `pipx` already present; if neither is, the helper says so and installs nothing (`mise use -g uv` provides `uv`). Rerunning changes nothing once the proof below passes.

## Prove it

```bash
git filter-repo -h | grep -- --sensitive-data-removal
```

The runbook requires that flag. The helper runs the same check and reports a failure without failing setup. Distro packages can be older than 2.47 and lack the flag, so the help text decides, not the package name.

## Decline or remove it

- `./install.sh --without-git-filter-repo` records the preference as off. It leaves an installed copy alone.
- To remove an installed copy: `mise unuse -g pipx:git-filter-repo`, then `mise uninstall pipx:git-filter-repo`.
