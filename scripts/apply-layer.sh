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
python3 "$root/scripts/groundwork-update.py" verify-lock

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
python3 "$root/scripts/groundwork-update.py" validate "$root"
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

# Each privileged step has a check that needs no sudo. A routine update uses
# them to ask for administrator permission only when something must change.
chromium_dir=/usr/share/chromium
extension_dir=$chromium_dir/extensions
screen_share_rule=(allow in on tailscale0 to any port 5900 proto tcp comment 'Groundwork screen sharing')
screen_share_rule_marker="${XDG_STATE_HOME:-$HOME/.local/state}/groundwork/ufw-screen-share-rule"
service_ready() {
  systemctl is-enabled --quiet "$1" 2>/dev/null && systemctl is-active --quiet "$1" 2>/dev/null
}
chromium_extension_current() {
  [[ $(stat -c %a "$chromium_dir" 2>/dev/null) == 755 && $(stat -c %a "$extension_dir" 2>/dev/null) == 755 ]] &&
    cmp -s "$extension_source" "$extension_dir/$bitwarden_extension" &&
    [[ $(stat -c %a "$extension_dir/$bitwarden_extension" 2>/dev/null) == 644 ]]
}
# ufw cannot list rules without root, so remember the rule this layer added.
screen_share_rule_current() {
  [[ -f $screen_share_rule_marker && $(<"$screen_share_rule_marker") == "${screen_share_rule[*]}" ]]
}
keyd_current() {
  cmp -s "$root/keyd/default.conf" /etc/keyd/default.conf && service_ready keyd
}
privileged_changes_needed() {
  local executable
  # Missing tools are installed through omarchy pkg add or Omarchy's installers.
  for executable in cursor herdr tailscale obsidian bw; do
    command -v "$executable" >/dev/null || return 0
  done
  if ! command -v bitwarden >/dev/null && ! command -v flatpak >/dev/null; then
    return 0
  fi
  service_ready tailscaled || return 0
  if command -v chromium >/dev/null; then
    chromium_extension_current || return 0
  fi
  if [[ $with_screen_sharing -eq 1 ]]; then
    command -v wayvnc >/dev/null || return 0
    if command -v ufw >/dev/null; then
      screen_share_rule_current || return 0
    fi
  fi
  if [[ $with_keyd -eq 1 ]]; then
    command -v keyd >/dev/null || return 0
    keyd_current || return 0
  fi
  return 1
}

needs_privilege=1
if [[ $updating -eq 1 ]] && ! privileged_changes_needed; then
  needs_privilege=0
  echo "No administrator changes are needed for this update; sudo was not used."
fi

if [[ "$EUID" -ne 0 && $needs_privilege -eq 1 && $updating -eq 1 ]]; then
  # Prime sudo once in the foreground terminal before the first privileged step.
  if [[ -r /dev/tty && -w /dev/tty ]] && : </dev/tty >/dev/null; then
    echo "This update changes system settings and needs administrator permission."
    sudo_status=0
    # shellcheck disable=SC2024
    sudo /usr/bin/true </dev/tty >/dev/tty 2>/dev/tty || sudo_status=$?
    if [[ "$sudo_status" -ne 0 ]]; then
      echo "Administrator permission was not granted, so nothing was changed." >&2
      exit "$sudo_status"
    fi
  elif ! sudo -n /usr/bin/true 2>/dev/null; then
    echo "This update needs administrator permission, and no terminal is available to ask for it." >&2
    echo "Nothing was changed. Run update-all or groundwork-update from a terminal." >&2
    exit 1
  fi
fi

if [[ "$EUID" -ne 0 && $needs_privilege -eq 1 ]]; then
  if [[ $updating -eq 0 ]] && ! sudo -n true 2>/dev/null; then
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
# Omarchy Mac's installer owns Cursor on Apple Silicon, including the GNOME
# keyring password store. An existing cursor command stays put. This only fills
# a missing password-store key and never replaces a value already written.
ensure_cursor_password_store() {
  python3 - "$HOME/.config/Cursor/argv.json" <<'PY'
import json
import sys
from pathlib import Path

def strip_jsonc(text):
    out = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
        elif ch == "/" and nxt == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        elif ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)

path = Path(sys.argv[1])
if path.is_symlink() or (path.exists() and not path.is_file()):
    print(f"Refusing to edit Cursor argv.json because it is not a regular file: {path}", file=sys.stderr)
    sys.exit(1)
try:
    text = path.read_text() if path.exists() else ""
    if not text.strip():
        data = {}
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            stripped = strip_jsonc(text)
            if not stripped.strip():
                raise
            data = json.loads(stripped)
except (OSError, json.JSONDecodeError) as error:
    print(f"Cursor argv.json was left unchanged: {path} ({error})", file=sys.stderr)
    sys.exit(1)
if not isinstance(data, dict):
    print(f"Cursor argv.json must be a JSON object: {path}", file=sys.stderr)
    sys.exit(1)
current = data.get("password-store")
if current == "gnome-libsecret":
    sys.exit(0)
if current is not None:
    print(f"Cursor password-store is already {current}; argv.json was left unchanged.")
    sys.exit(0)
data["password-store"] = "gnome-libsecret"
try:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
except OSError as error:
    print(f"Cursor argv.json was left unchanged: {path} ({error})", file=sys.stderr)
    sys.exit(1)
PY
}
install_cursor() {
  if ! command -v cursor >/dev/null; then
    if ! command -v omarchy-install-cursor >/dev/null; then
      echo "Omarchy's Cursor installer, omarchy-install-cursor, is unavailable and cursor is not installed." >&2
      echo "Update Omarchy, then rerun ./install.sh." >&2
      exit 1
    fi
    omarchy-install-cursor
    hash -r
    if ! command -v cursor >/dev/null; then
      echo "omarchy-install-cursor did not provide a cursor command." >&2
      exit 1
    fi
  fi
  ensure_cursor_password_store
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
install_cursor

install_package herdr
install_package tailscale
if [[ $updating -eq 0 ]] || ! service_ready tailscaled; then
  sudo systemctl enable --now tailscaled
fi

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
  if [[ $updating -eq 0 ]] || ! chromium_extension_current; then
    # Chromium must traverse both directories even when setup uses umask 077.
    # Explicit modes also repair directories created by an earlier setup run.
    sudo install -d -m 755 "$chromium_dir" "$extension_dir"
    if ! sudo cmp -s "$extension_source" "$extension_dir/$bitwarden_extension"; then
      sudo cp --backup=numbered --remove-destination "$extension_source" "$extension_dir/$bitwarden_extension"
    fi
    sudo chmod 644 "$extension_dir/$bitwarden_extension"
  fi
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
  if command -v ufw >/dev/null && { [[ $updating -eq 0 ]] || ! screen_share_rule_current; }; then
    sudo ufw "${screen_share_rule[@]}"
    mkdir -p "$(dirname "$screen_share_rule_marker")"
    printf '%s\n' "${screen_share_rule[*]}" >"$screen_share_rule_marker"
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
  if [[ $updating -eq 0 ]] || ! keyd_current; then
    sudo mkdir -p /etc/keyd
    if ! sudo cmp -s "$root/keyd/default.conf" /etc/keyd/default.conf; then
      # GNU cp keeps a numbered backup of an existing config, including on reruns.
      sudo cp --backup=numbered --remove-destination "$root/keyd/default.conf" /etc/keyd/default.conf
    fi
    sudo systemctl enable --now keyd
    sudo keyd reload
  fi
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
