#!/usr/bin/env bash
set -euo pipefail

# Setup and explicit preference changes share the updater's apply code.
for arg in "$@"; do
  case "$arg" in
    --with-keyd | --without-keyd | --with-screen-sharing | --without-screen-sharing | --help | -h) ;;
    *)
      echo "Unknown argument: $arg. Use ./install.sh --help; nothing changed." >&2
      exit 64
      ;;
  esac
done
root=$(cd "$(dirname "$0")" && pwd)
exec python3 "$root/scripts/groundwork-update.py" setup "$root" "$@"
