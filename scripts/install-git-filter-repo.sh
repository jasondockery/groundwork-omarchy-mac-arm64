#!/usr/bin/env bash
# Opt-in, per-user git-filter-repo for history purges. Called by apply-layer.sh
# only after --with-git-filter-repo (or a saved yes). The contract is
# groundwork-core docs/history-purge.md: install into the user's home with no
# root, and prove it with `git filter-repo -h` listing --sensitive-data-removal.
# This script never runs a history rewrite, a force-push, or a tag change.
set -euo pipefail

# mise's shims are not always on PATH inside the installer.
mise_data=${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}
export PATH="$mise_data/shims:$PATH"

has_proof() {
  git filter-repo -h 2>&1 | grep -q -- '--sensitive-data-removal'
}

# Idempotent: a copy that already shows the flag is left alone.
if has_proof; then
  echo "git-filter-repo is installed (proof: git filter-repo -h lists --sensitive-data-removal)."
  exit 0
fi

if ! command -v mise >/dev/null; then
  echo "mise is unavailable, so git-filter-repo was not installed. Rerun ./install.sh after mise is available." >&2
  exit 0
fi
# mise's pipx backend runs through uv or pipx; neither is installed by this script.
if ! command -v uv >/dev/null && ! command -v pipx >/dev/null; then
  echo "Neither uv nor pipx is available for mise's pipx backend, so git-filter-repo was not installed. Run: mise use -g uv" >&2
  exit 0
fi

timeout --kill-after=5s 300 mise use -g pipx:git-filter-repo </dev/null || true

if has_proof; then
  echo "git-filter-repo installed for this user (proof: git filter-repo -h lists --sensitive-data-removal)."
else
  echo "git-filter-repo is not usable: 'git filter-repo -h' does not list --sensitive-data-removal. Rerun ./install.sh to retry." >&2
fi
exit 0
