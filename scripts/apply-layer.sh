#!/bin/bash
# Link the personal Hyprland override into $HOME.
# For any Apple Silicon Omarchy Mac (aarch64). Does not touch Git, SSH,
# groundwork-core, groundwork-mac, or /usr/share/omarchy.
set -euo pipefail

usage() {
  echo "Usage: ./install.sh [--with-keyd|--without-keyd] [--with-screen-sharing|--without-screen-sharing]"
  echo "Caps Lock defaults to Escape on tap and Control while held."
  echo "Saved setup choices are reused; explicit options change those choices."
  echo "--without-keyd leaves any existing keyd configuration and service unchanged."
  echo "--without-screen-sharing leaves any existing desktop-sharing service unchanged."
}

with_keyd=1
with_screen_sharing=1
keyd_choice=""
sharing_choice=""
updating=0
for arg in "$@"; do
  case "$arg" in
    --with-keyd | --without-keyd)
      if [[ -n $keyd_choice && $keyd_choice != "$arg" ]]; then
        echo "Choose only one of --with-keyd and --without-keyd." >&2
        exit 1
      fi
      keyd_choice=$arg
      [[ $arg == --with-keyd ]] && with_keyd=1 || with_keyd=0
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    --with-screen-sharing | --without-screen-sharing)
      if [[ -n $sharing_choice && $sharing_choice != "$arg" ]]; then
        echo "Choose only one screen-sharing option." >&2
        exit 64
      fi
      sharing_choice=$arg
      [[ $arg == --with-screen-sharing ]] && with_screen_sharing=1 || with_screen_sharing=0
      ;;
    --update) updating=1 ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

if [[ $(uname -m) != aarch64 ]]; then
  echo "This layer is for Apple Silicon Omarchy (aarch64). This machine is $(uname -m)." >&2
  exit 1
fi

if ! command -v omarchy >/dev/null; then
  echo "omarchy is not installed. This layer is for Omarchy Mac." >&2
  exit 1
fi

if ! command -v stow >/dev/null; then
  echo "stow is not installed. Install it with: omarchy pkg add stow" >&2
  exit 1
fi

preference_mode=setup
[[ $updating -eq 0 ]] || preference_mode=update
stored=$(python3 "$root/scripts/groundwork-update.py" preferences "$preference_mode")
if [[ -n $stored ]]; then
  mapfile -t preferences <<<"$stored"
  [[ -n $keyd_choice ]] || with_keyd=${preferences[0]}
  [[ -n $sharing_choice ]] || with_screen_sharing=${preferences[1]}
  export GROUNDWORK_OBSIDIAN_VAULT="${GROUNDWORK_OBSIDIAN_VAULT:-${preferences[2]}}"
fi

# Fail before installing packages or moving files when the checkout is incomplete.
bitwarden_extension=nngceckbapebfimnlniiiahkandclblb.json
extension_source="$root/chromium/extensions/$bitwarden_extension"
[[ -f "$root/keyd/default.conf" && -f "$root/packages/hypr/.config/hypr/input.lua" && -f "$root/scripts/seed-obsidian.py" && -f "$root/scripts/groundwork-screen-share.py" && -f "$extension_source" ]] || {
  echo "The checkout is incomplete: keyd config, Hyprland input override, Obsidian helper, or Bitwarden extension manifest is missing." >&2
  exit 1
}

# Ask once, before the first install or privileged write. The timestamp is
# refreshed until this script exits so Tailscale, the browser extension, and
# keyd do not each stop for a password. Nothing is installed if this fails.
t3_installer=""
sudo_keepalive_pid=""
stop_sudo_keepalive() {
  if [[ -n "${sudo_keepalive_pid}" ]]; then
    kill "$sudo_keepalive_pid" 2>/dev/null || true
    wait "$sudo_keepalive_pid" 2>/dev/null || true
    sudo_keepalive_pid=""
  fi
}
cleanup() {
  stop_sudo_keepalive
  if [[ -n "${t3_installer}" ]]; then
    rm -f "$t3_installer"
  fi
}
trap cleanup EXIT

if [[ "$EUID" -ne 0 ]]; then
  if ! sudo -n true 2>/dev/null; then
    if [[ -r /dev/tty && -w /dev/tty ]] && : </dev/tty >/dev/null; then
      echo "This install needs an administrator password for Tailscale, the browser extension, and keyd."
      echo "Enter your password when asked. This approval covers the privileged steps in this run."
      sudo_status=0
      # sudo -v reads the password on its own standard input. Point that prompt
      # at the terminal. These redirects are not a command run under sudo.
      # shellcheck disable=SC2024
      sudo -v </dev/tty >/dev/tty 2>/dev/tty || sudo_status=$?
      if [[ "$sudo_status" -ne 0 ]]; then
        echo "Administrator permission was not granted, so nothing was changed." >&2
        echo "Run ./install.sh from a terminal and enter your password when asked." >&2
        exit "$sudo_status"
      fi
    else
      echo "This install needs an administrator password, and no terminal is available to ask for it." >&2
      echo "Nothing was changed. Run ./install.sh from a terminal and enter your password when asked." >&2
      exit 1
    fi
  fi
  sudo_keepalive_pid=""
  # Own the sleeper so the exit trap can stop the refresh immediately.
  (
    sleep_pid=""
    trap 'if [[ -n "$sleep_pid" ]]; then kill "$sleep_pid" 2>/dev/null || true; fi; exit 0' TERM INT
    while kill -0 "$$" 2>/dev/null; do
      sleep 50 &
      sleep_pid=$!
      wait "$sleep_pid" || exit 0
      sleep_pid=""
      sudo -n true 2>/dev/null || exit 0
    done
  ) &
  sudo_keepalive_pid=$!
fi

# Keep the AI workbench and private recovery path available on every Apple
# Silicon Omarchy install. Authentication stays manual; no tailnet identity or
# reusable auth key belongs in this repo.
install_package() {
  local package=$1 executable=${2:-$1}
  if [[ $updating -eq 1 ]] && command -v "$executable" >/dev/null; then
    return 0
  fi
  omarchy pkg add "$package"
  if ! command -v "$executable" >/dev/null; then
    echo "$package is still unavailable after omarchy pkg add. The helper may skip packages absent from the current feed." >&2
    echo "Update Omarchy and its package feed, resolve the package error, then rerun ./install.sh." >&2
    exit 1
  fi
}
# Omarchy owns provider versions and configuration through its default mise
# tools. Install those declarations without choosing a default agent or writing
# a competing user config. Existing provider commands retain their owner.
provider_ready() {
  local provider=$1 executable underlying
  executable=$(command -v "$provider") || return 1
  case "$executable" in
    "${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}"/shims/* | \
      "${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}"/command-wrappers/bin/*)
      # A dispatcher can exist before its configured tool is installed.
      underlying=$(timeout --kill-after=2s 15 mise which "$provider") || return 1
      [[ -x $underlying ]]
      ;;
    *) return 0 ;;
  esac
}
for provider in claude codex opencode grok; do
  if ! provider_ready "$provider"; then
    if ! command -v mise >/dev/null; then
      echo "Omarchy's mise tool manager is unavailable. Update Omarchy, then rerun ./install.sh." >&2
      exit 1
    fi
    if ! command -v omarchy-mise-default-tools >/dev/null || ! omarchy-mise-default-tools declares "$provider"; then
      echo "$provider is unavailable and is not an enabled Omarchy default tool. Reconcile your chosen provider setup, then rerun ./install.sh." >&2
      exit 1
    fi
    timeout --kill-after=5s 300 mise install "$provider"
    hash -r
    if ! provider_ready "$provider"; then
      echo "$provider is still unavailable. Reconcile Omarchy's default mise tools, start a fresh shell, then rerun ./install.sh." >&2
      exit 1
    fi
  fi
done
install_package cursor-bin cursor

install_package herdr
install_package tailscale
sudo systemctl enable --now tailscaled

# Omarchy owns Obsidian's package, theme, and launch shortcut. Bitwarden's
# desktop package is absent from the current ARM feed; use its ARM64 Flathub
# distribution unless a native desktop app is already available. Sign-in
# remains user-owned, with no vault export in this repo.
install_package obsidian
install_package bitwarden-cli bw
if ! command -v bitwarden >/dev/null; then
  install_package flatpak
  flatpak --user remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  if ! flatpak --user info com.bitwarden.desktop >/dev/null 2>&1; then
    flatpak --user install --noninteractive flathub com.bitwarden.desktop
  fi
  flatpak --user info com.bitwarden.desktop >/dev/null
fi

# Use the same external-extension mechanism as Omarchy's 1Password service.
# Chromium fetches and updates the official Web Store extension per profile;
# no account state or browser preference database is written here.
if command -v chromium >/dev/null; then
  extension_dir=/usr/share/chromium/extensions
  # Chromium must traverse both directories even when setup uses umask 077.
  # Explicit modes also repair directories created by an earlier setup run.
  sudo install -d -m 755 /usr/share/chromium "$extension_dir"
  if ! sudo cmp -s "$extension_source" "$extension_dir/$bitwarden_extension"; then
    sudo cp --backup=numbered --remove-destination "$extension_source" "$extension_dir/$bitwarden_extension"
  fi
  sudo chmod 644 "$extension_dir/$bitwarden_extension"
  echo "Bitwarden's official extension is registered. Restart Chromium, approve it if prompted, and sign in in each profile."
else
  echo "Chromium is unavailable; skipped its Bitwarden extension. Rerun setup after installing Chromium."
fi

# Nightly is upstream's fast-moving train. Reconcile an existing stable CLI too;
# executable presence is not evidence that the selected channel is installed.
if [[ $updating -eq 0 ]] || ! command -v t3 >/dev/null; then
  t3_installer="$(mktemp)"
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 --max-time 60 \
    -fsSL https://t3.codes/install.sh -o "$t3_installer"
  env -u T3CODE_VERSION T3CODE_CHANNEL=nightly timeout --kill-after=5s 300 sh "$t3_installer"
  rm -f "$t3_installer"
  t3_installer=""
fi

# The upstream installer owns its symlink, but does not change shell startup
# files. Detect an earlier npm/system t3 rather than claiming it was replaced.
t3_bin_dir="${T3CODE_INSTALL_BIN_DIR:-$HOME/.local/bin}"
installed_t3="$t3_bin_dir/t3"
hash -r
effective_t3="$(command -v t3 || true)"
if [[ ! -x $installed_t3 || -z $effective_t3 || ! $effective_t3 -ef $installed_t3 ]]; then
  printf 'T3 command shadowing or missing PATH entry: effective=%s, installed=%s\n' "${effective_t3:-none}" "$installed_t3" >&2
  printf 'Prepend %q to PATH in your user shell configuration, start a fresh shell, and rerun ./install.sh.\n' "$t3_bin_dir" >&2
  echo "The existing npm/system installation was preserved; remove it only through its original package manager if desired." >&2
  exit 1
fi
t3_version="$(timeout --kill-after=5s 15 "$installed_t3" --version)"
if [[ $t3_version != *-nightly.* ]]; then
  printf 'Expected T3 Code Nightly, received: %s\n' "$t3_version" >&2
  exit 1
fi

python3 "$root/scripts/seed-obsidian.py" "${GROUNDWORK_OBSIDIAN_VAULT:-$HOME/Documents/Obsidian/my_obsidian_vault}"

if [[ $with_screen_sharing -eq 1 ]]; then
  install_package wayvnc
  python3 "$root/scripts/groundwork-screen-share.py" configure
  if command -v ufw >/dev/null; then
    sudo ufw allow in on tailscale0 to any port 5900 proto tcp comment 'Groundwork screen sharing'
  fi
fi

# Stow will not replace a real file. Move conflicting files aside so this
# works on a fresh Omarchy install and on a machine that already has them.
pkg="$root/packages/hypr"
while IFS= read -r -d '' file; do
  rel=${file#"$pkg/"}
  target="$HOME/$rel"
  if [[ ! -e $target && ! -L $target ]]; then
    continue
  fi
  if [[ -L $target ]]; then
    current=$(readlink -f "$target" || true)
    want=$(readlink -f "$file")
    if [[ $current == "$want" ]]; then
      continue
    fi
  fi
  # Numbered backups preserve every previous override, even on rapid reruns.
  mv --backup=numbered "$target" "${target}.bak-omarchy-groundwork"
  echo "Backed up $target as ${target}.bak-omarchy-groundwork (or a numbered predecessor)."
done < <(find "$pkg" -type f -print0)

stow --dir "$root/packages" --target "$HOME" --no-folding hypr

if [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]]; then
  hyprctl reload
  config_errors=$(hyprctl configerrors)
  if [[ -n $config_errors ]]; then
    printf 'Hyprland reports configuration errors after reload:\n%s\n' "$config_errors" >&2
    echo "Resolve them before continuing. The previous input file is backed up beside input.lua." >&2
    exit 1
  fi
else
  echo "No Hyprland session detected; validate the input override on the desktop with:"
  echo "  hyprctl reload && hyprctl configerrors"
fi

if [[ $with_keyd -eq 1 ]]; then
  install_package keyd
  sudo mkdir -p /etc/keyd
  if ! sudo cmp -s "$root/keyd/default.conf" /etc/keyd/default.conf; then
    # GNU cp keeps a numbered backup of an existing config, including on reruns.
    sudo cp --backup=numbered --remove-destination "$root/keyd/default.conf" /etc/keyd/default.conf
  fi
  sudo systemctl enable --now keyd
  sudo keyd reload
  echo "keyd is running. A tap of Caps Lock is Escape. Holding it is Control."
else
  echo "Skipped keyd setup; any existing keyd configuration and service were left alone."
fi
python3 "$root/scripts/groundwork-update.py" register "$root" "$with_keyd" "$with_screen_sharing" \
  "${GROUNDWORK_OBSIDIAN_VAULT:-$HOME/Documents/Obsidian/my_obsidian_vault}"
if [[ $updating -eq 1 ]]; then
  echo "Groundwork settings applied. Saved keyd, screen-sharing, and vault choices were preserved."
  exit 0
fi
echo "Trackpad configuration, T3 Code, Herdr, Tailscale, Obsidian, and Bitwarden are installed."
echo "Next time, run update-all for maintenance or groundwork-update for configuration only."
echo "Open Obsidian with Super+Shift+O; finish vault, sync, and Bitwarden sign-in using docs/apps.md."
echo "Remote access still requires your sign-in and tailnet policy; see docs/remote-access.md."
echo "Complete tailnet sign-in with: sudo tailscale up --timeout=5m"
echo "After reviewing the tailnet policy, enable private SSH with: sudo tailscale set --ssh"
echo "Verify the setup using the first-run checklist in README.md."
echo "Git, SSH account settings, and Omarchy desktop bindings were left alone."
