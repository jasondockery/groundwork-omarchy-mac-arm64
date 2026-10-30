#!/usr/bin/env bash
# Report the move from the public groundwork checkout to groundwork-mac.
# Never applies that repo, never deletes the old checkout, and never edits
# Omarchy's tmux, desktop, or shell config.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/migrate-from-groundwork.sh [--clone-mac]

Shows how to leave the public jasondockery/groundwork checkout for
groundwork-mac.

On Omarchy this script does not install or apply groundwork-mac.
Use ./install.sh in this repository after Omarchy itself is installed.

--clone-mac clones https://github.com/jasondockery/groundwork-mac.git into
~/code/groundwork-mac when that directory is missing. It does not run
bootstrap-mac.sh. On Omarchy, --clone-mac is refused.

The old ~/code/groundwork directory is left in place.
EOF
}

clone_mac=0
for arg in "$@"; do
  case "$arg" in
    --clone-mac) clone_mac=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

# Print a URL that is safe to show. Userinfo (user[:pass]@) before the first "/"
# is replaced with "***@" (a bare "git@" SSH user is kept), an "@" later in the
# URL is masked along with whatever follows it, GitHub-token-shaped segments
# (gh[pousr]_..., github_pat_...) are masked anywhere, and any query or
# fragment is dropped.
redact_url() {
  local url="$1" scheme="" head userinfo="" mark=""
  url="$(printf '%s' "$url" | sed -E 's/(gh[pousr]_|github_pat_)[A-Za-z0-9_]+/***/g')"
  if [[ "$url" =~ ^([A-Za-z][A-Za-z0-9+.-]*://)(.*)$ ]]; then
    scheme="${BASH_REMATCH[1]}"
    url="${BASH_REMATCH[2]}"
  fi
  head="${url%%/*}"
  if [[ "$head" == *@* ]]; then
    userinfo="${head%@*}"
    url="${url#"$userinfo"@}"
    if [[ "$userinfo" == git ]]; then
      mark="git@"
    else
      mark="***@"
    fi
  fi
  url="${url%%[?#]*}"
  if [[ "$url" == *@* ]]; then
    url="${url%%@*}@***"
  fi
  printf '%s%s%s\n' "$scheme" "$mark" "$url"
}

# Show a checkout's origin remote without ever printing credentials.
show_origin() {
  local url
  url="$(git -C "$1" remote get-url origin 2>/dev/null || true)"
  if [[ -n "$url" ]]; then
    redact_url "$url"
  fi
}

on_omarchy=0
if [[ "$(uname -s)" == Linux ]] && command -v omarchy >/dev/null 2>&1; then
  on_omarchy=1
fi

old="${GROUNDWORK_OLD_DIR:-$HOME/code/groundwork}"
new="${GROUNDWORK_MAC_DIR:-$HOME/code/groundwork-mac}"

echo "Old public checkout: $old"
if [[ -d "$old/.git" ]]; then
  show_origin "$old"
else
  echo "No git checkout there."
fi

echo "groundwork-mac checkout: $new"
if [[ -d "$new/.git" ]]; then
  show_origin "$new"
else
  echo "Not cloned yet."
fi

echo
echo "Practice these tmux chords on every Groundwork OS. They match Omarchy:"
echo "  Ctrl+Space is the prefix. Ctrl+B is the second prefix."
echo "  Alt+Enter splits below. Alt+Shift+Enter splits beside."
echo "  Alt+Escape closes a pane; stock Omarchy may close immediately without confirmation."
echo "  Ctrl+Alt+Arrow moves between panes."
echo "  Alt+Left and Alt+Right move between windows."
echo "  Inside tmux, word jumps stay on Alt+B and Alt+F."
echo "Super+Space and Super+K stay on the Omarchy desktop. Do not copy them onto a Mac."

if [[ "$on_omarchy" -eq 1 ]]; then
  echo
  echo "This machine is Omarchy."
  echo "Do not run bootstrap-mac.sh here."
  echo "Do not chezmoi apply groundwork-mac here."
  echo "Install this repository with ./install.sh after Omarchy is booted."
  if [[ "$clone_mac" -eq 1 ]]; then
    echo "Refusing --clone-mac on Omarchy." >&2
    exit 1
  fi
  exit 0
fi

if [[ "$clone_mac" -eq 1 ]]; then
  if [[ -d "$new/.git" ]]; then
    echo
    echo "groundwork-mac is already cloned. Not changing it."
  else
    mkdir -p "$(dirname "$new")"
    git clone --recurse-submodules https://github.com/jasondockery/groundwork-mac.git "$new"
  fi
  echo
  echo "Read the shared migration guide before applying an existing installation:"
  echo "  https://github.com/jasondockery/groundwork-core/blob/main/docs/migrating-from-groundwork.md"
  echo "For a fresh macOS installation only, inspect the bootstrap first:"
  echo "  less \"$new/bootstrap-mac.sh\""
  echo "  bash \"$new/bootstrap-mac.sh\""
  exit 0
fi

echo
echo "Existing installs: follow https://github.com/jasondockery/groundwork-core/blob/main/docs/migrating-from-groundwork.md"
echo "Fresh macOS installs: inspect the Mac README and bootstrap before running it."
echo "Linux and WSL2: use groundwork-linux or groundwork-windows instead."
echo "To prepare a separate Mac engine checkout over HTTPS:"
echo "  git clone --recurse-submodules https://github.com/jasondockery/groundwork-mac.git \"$new\""
echo "  less \"$new/bootstrap-mac.sh\""
echo "  bash \"$new/bootstrap-mac.sh\""
echo "Leave \"$old\" as the published snapshot."
