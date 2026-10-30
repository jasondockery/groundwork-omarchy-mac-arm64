#!/usr/bin/env bash
# Reproduction for decision 0016: mise run through a command-wrapper symlink
# seems to move its tool and shim directories after the inherited PATH.
# Touches only a temp directory; needs mise and /usr/bin/env. Prints the PATH
# that the wrapped tool sees; "installs" listed after "/usr/bin" is the reorder.
# Observed on mise 2026.10.23 linux-arm64. Another setup (a [wrappers] entry in
# the config) did not reproduce it, so the exact trigger is not pinned down.
set -eu
mise_bin=$(command -v mise)
dir=$(mktemp -d "${TMPDIR:-/tmp}/mise-wrapper-repro.XXXXXX")
trap 'rm -rf "$dir"' EXIT
mkdir -p "$dir/command-wrappers/bin" "$dir/installs/claude/1.0.0" "$dir/shims" "$dir/home" "$dir/xdg"
ln -s "$mise_bin" "$dir/command-wrappers/bin/claude"
printf '#!/bin/sh\nprintf "%%s\\n" "$PATH"\n' >"$dir/installs/claude/1.0.0/claude"
chmod +x "$dir/installs/claude/1.0.0/claude"
ln -s 1.0.0 "$dir/installs/claude/latest"
printf '[tools]\nclaude = "latest"\n' >"$dir/config.toml"
cd "$dir"  # keep mise from walking up into the caller's project and home configs
echo "input:  wrappers : installs/claude/latest : shims : /usr/bin : /bin"
printf 'output: '
env -i HOME="$dir/home" XDG_CONFIG_HOME="$dir/xdg" MISE_DATA_DIR="$dir" \
  MISE_GLOBAL_CONFIG_FILE="$dir/config.toml" MISE_CONFIG_DIR="$dir" \
  PATH="$dir/command-wrappers/bin:$dir/installs/claude/latest:$dir/shims:/usr/bin:/bin" \
  "$dir/command-wrappers/bin/claude" | sed "s#$dir#TMP#g; s#:#\n        #g"
