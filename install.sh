#!/usr/bin/env bash
set -euo pipefail

# Setup and explicit preference changes share the updater's apply code.
for arg in "$@"; do
  case "$arg" in
    --with-keyd | --without-keyd | --with-screen-sharing | --without-screen-sharing | --with-blip | --without-blip | --with-local-inference | --without-local-inference | --with-git-filter-repo | --without-git-filter-repo | --migrate-to-omarchy-installers | --keep-standalone-t3 | --help | -h) ;;
    *)
      echo "Unknown argument: $arg. Use ./install.sh --help; nothing changed." >&2
      exit 64
      ;;
  esac
done
root=$(cd "$(dirname "$0")" && pwd)
# setup checks out the pinned core submodule before apply reads it.
exec python3 "$root/scripts/groundwork-update.py" setup "$root" "$@"
