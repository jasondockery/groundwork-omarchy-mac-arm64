#!/bin/bash
# Link the personal Hyprland override into $HOME.
# For any Apple Silicon Omarchy Mac (aarch64). Does not touch Git, SSH,
# groundwork-core, groundwork-mac, or /usr/share/omarchy.
set -euo pipefail

usage() {
  echo "Usage: ./install.sh [--with-keyd|--without-keyd] [--with-screen-sharing|--without-screen-sharing] [--with-blip|--without-blip]"
  echo "Caps Lock defaults to Escape on tap and Control while held."
  echo "Saved setup choices are reused; explicit options change those choices."
  echo "--without-keyd leaves any existing keyd configuration and service unchanged."
  echo "--without-screen-sharing leaves any existing desktop-sharing service unchanged."
  echo "--with-blip installs the Blip iMessage client. It is off by default."
  echo "--without-blip leaves any existing Blip install unchanged."
}

with_keyd=1
with_screen_sharing=1
with_blip=0
keyd_choice=""
sharing_choice=""
blip_choice=""
updating=0
update_plugins=0
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
    --with-blip | --without-blip)
      if [[ -n $blip_choice && $blip_choice != "$arg" ]]; then
        echo "Choose only one of --with-blip and --without-blip." >&2
        exit 64
      fi
      blip_choice=$arg
      [[ $arg == --with-blip ]] && with_blip=1 || with_blip=0
      ;;
    --update) updating=1 ;;
    --update-plugins) update_plugins=1 ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

# GROUNDWORK_TEST_ROOT lets the isolated fixtures relocate system paths. It is
# honoured only together with the harness's GROUNDWORK_TESTING=1.
test_root=""
if [[ -n ${GROUNDWORK_TEST_ROOT:-} ]]; then
  if [[ ${GROUNDWORK_TESTING:-} != 1 ]]; then
    echo "GROUNDWORK_TEST_ROOT is a test-only variable. Unset it and rerun; nothing was changed." >&2
    exit 64
  fi
  test_root=$GROUNDWORK_TEST_ROOT
fi

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
  if [[ -z $blip_choice ]]; then
    case ${preferences[3]-} in
      '') ;;
      0|1) with_blip=${preferences[3]} ;;
      *)
        echo "Saved Blip choice is invalid. Preserve ~/.config/groundwork/omarchy.json and repair it before rerunning." >&2
        exit 1
        ;;
    esac
  fi
fi

# Fail before installing packages or moving files when the checkout is incomplete.
bitwarden_extension=nngceckbapebfimnlniiiahkandclblb.json
extension_source="$root/chromium/extensions/$bitwarden_extension"
[[ -f "$root/keyd/default.conf" && -f "$root/packages/hypr/.config/hypr/input.lua" && -f "$root/scripts/seed-obsidian.py" && -f "$root/scripts/groundwork-screen-share.py" && -f "$root/scripts/omarchy-plugins.py" && -f "$extension_source" ]] || {
  echo "The checkout is incomplete: keyd config, Hyprland input override, Obsidian helper, plugin helper, or Bitwarden extension manifest is missing." >&2
  exit 1
}

# Ask once, before the first install or privileged write. The timestamp is
# refreshed until this script exits so Tailscale, the browser extension, and
# keyd do not each stop for a password. Nothing is installed if this fails.
t3_installer=""
bun_preview_dir=""
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
  if [[ -n ${bun_preview_dir} ]]; then
    rm -rf -- "$bun_preview_dir"
    bun_preview_dir=""
  fi
}
trap cleanup EXIT

# Each privileged step has a check that needs no sudo. A routine update uses
# them to ask for administrator permission only when something must change.
chromium_dir=$test_root/usr/share/chromium
keyd_config=$test_root/etc/keyd/default.conf
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
  [[ ! -L $screen_share_rule_marker && -f $screen_share_rule_marker &&
    $(<"$screen_share_rule_marker") == "${screen_share_rule[*]}" ]]
}
keyd_current() {
  cmp -s "$root/keyd/default.conf" "$keyd_config" && service_ready keyd
}
privileged_changes_needed() {
  local executable
  # Missing tools are installed through omarchy pkg add or Omarchy's installers.
  # readelf (binutils) is what update-all uses to check T3 Code builds.
  for executable in cursor herdr tailscale obsidian bw readelf; do
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
  # Same executables install_blip adds. bun is a per-user mise tool and needs no sudo.
  if [[ $with_blip -eq 1 ]]; then
    for executable in jq ssh notify-send wl-copy xdg-open socat; do
      command -v "$executable" >/dev/null || return 0
    done
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

# update-all checks T3 Code builds with readelf before installing them.
install_package binutils readelf
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
# Newer linux-arm64 nightlies have 4 KiB-aligned LOAD segments and cannot
# start on kernels with larger pages (pingdotgg/t3code#23433). There, keep a
# Nightly that already starts; update-all checks every newer build before
# installing it. Pin the last known-good build only when t3 is missing, does
# not start, or is not a Nightly.
t3_known_good_large_page=0.1.2-nightly.20261021.3499
page_size=$(getconf PAGESIZE)
t3_pin=()
reinstall_t3=0
if ((page_size > 4096)); then
  if t3_reported=$(timeout --kill-after=5s 15 t3 --version 2>/dev/null) && [[ $t3_reported == *-nightly.* ]]; then
    [[ $updating -eq 1 ]] || echo "Keeping the installed T3 Code Nightly on this $((page_size / 1024)) KiB-page kernel; update-all checks newer builds first."
  else
    reinstall_t3=1
    t3_pin=(T3CODE_VERSION="$t3_known_good_large_page")
    echo "This kernel uses $((page_size / 1024)) KiB pages; installing T3 Code $t3_known_good_large_page (pingdotgg/t3code#23433)."
  fi
elif [[ $updating -eq 0 ]] || ! command -v t3 >/dev/null; then
  reinstall_t3=1
fi
if [[ $reinstall_t3 -eq 1 ]]; then
  t3_installer="$(mktemp)"
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 --max-time 60 \
    -fsSL https://t3.codes/install.sh -o "$t3_installer"
  env -u T3CODE_VERSION T3CODE_CHANNEL=nightly "${t3_pin[@]}" timeout --kill-after=5s 300 sh "$t3_installer"
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
    python3 "$root/scripts/groundwork-update.py" marker write ufw-screen-share-rule "${screen_share_rule[*]}"
  fi
else
  # The rule is left in place; forget it so re-enabling sharing re-adds it.
  python3 "$root/scripts/groundwork-update.py" marker remove ufw-screen-share-rule
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

# Shared shell plugins. A failure here stays retryable and does not stop the rest of apply.
# update-all passes --update-plugins. Configuration-only apply leaves plugin checkouts alone.
plugin_status=0
if [[ $update_plugins -eq 1 ]]; then
  python3 "$root/scripts/omarchy-plugins.py" --update || plugin_status=$?
else
  python3 "$root/scripts/omarchy-plugins.py" || plugin_status=$?
fi
if [[ $plugin_status -ne 0 ]]; then
  echo "Omarchy plugins could not be applied; rerun to retry them." >&2
fi

if [[ $with_keyd -eq 1 ]]; then
  install_package keyd
  if [[ $updating -eq 0 ]] || ! keyd_current; then
    sudo mkdir -p "$(dirname "$keyd_config")"
    if ! sudo cmp -s "$root/keyd/default.conf" "$keyd_config"; then
      # GNU cp keeps a numbered backup of an existing config, including on reruns.
      sudo cp --backup=numbered --remove-destination "$root/keyd/default.conf" "$keyd_config"
    fi
    sudo systemctl enable --now keyd
    sudo keyd reload
  fi
  echo "keyd is running. A tap of Caps Lock is Escape. Holding it is Control."
else
  echo "Skipped keyd setup; any existing keyd configuration and service were left alone."
fi

# Opt-in Blip iMessage client (decision 0011).
# One block on purpose, so it can move into the shared plugin list once that
# list supports git-URL sources and per-entry opt-in. This is not a second
# plugin installer: Omarchy's own plugin commands do the add and the enable.
# mise's bun install runs `bun -v` while it verifies the download. Fetch the
# release `mise latest` names, over https only, and refuse before that install
# when this kernel cannot map the ELF (oven-sh/bun#17627).
preview_bun_release() {
  local version base extracted preview=$1
  if [[ -z $preview || ! -d $preview ]]; then
    echo "The bun release preview directory is missing. Blip was not enabled." >&2
    return 1
  fi
  if ! command -v mise >/dev/null; then
    echo "mise is unavailable, so bun cannot be checked before install. Blip was not enabled." >&2
    return 1
  fi
  if ! version=$(timeout --kill-after=2s 60 mise latest bun); then
    echo "mise latest bun failed, so Blip was not enabled." >&2
    return 1
  fi
  version=${version//$'\r'/}
  read -r version <<<"$version"
  if [[ ! $version =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z]+)*$ ]]; then
    printf 'mise latest bun returned an unexpected version (%s). Blip was not enabled.\n' "$version" >&2
    return 1
  fi
  base="https://github.com/oven-sh/bun/releases/download/bun-v${version}"
  case "$base" in
    https://github.com/oven-sh/bun/releases/download/bun-v*) ;;
    *)
      echo "Refusing a bun download that is not the https GitHub release. Blip was not enabled." >&2
      return 1
      ;;
  esac
  if ! curl --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 --max-time 60 \
      -fsSL "${base}/SHASUMS256.txt" -o "${preview}/SHASUMS256.txt"; then
    echo "Could not download the bun checksums over https. Blip was not enabled." >&2
    return 1
  fi
  if ! curl --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 --max-time 180 \
      -fsSL "${base}/bun-linux-aarch64.zip" -o "${preview}/bun-linux-aarch64.zip"; then
    echo "Could not download bun-linux-aarch64.zip over https. Blip was not enabled." >&2
    return 1
  fi
  if ! extracted=$(python3 "$root/scripts/groundwork-update.py" verify-bun-archive "$preview"); then
    echo "The bun release failed verification. Blip was not enabled." >&2
    return 1
  fi
  if [[ ! -f $extracted ]]; then
    echo "The bun release archive has no bun executable. Blip was not enabled." >&2
    return 1
  fi
  # Same readelf -lW helper as T3. Failure already printed oven-sh/bun#17627.
  if ! python3 "$root/scripts/groundwork-update.py" check-load-alignment "$extracted"; then
    return 1
  fi
  printf '%s\n' "$version"
}
install_blip() {
  local package executable pair bun_bin resolved listed list_status added
  local checked_version installed_version installed_bun
  local blip_id=nixfred.blip
  local blip_url=https://github.com/nixfred/blip.git
  local -a packages=(jq:jq openssh:ssh libnotify:notify-send wl-clipboard:wl-copy xdg-utils:xdg-open socat:socat)
  added=0
  # ffmpeg and mpv play voice messages. blip-setup names them; they stay optional.
  for pair in "${packages[@]}"; do
    package=${pair%%:*}
    executable=${pair#*:}
    if ! command -v "$executable" >/dev/null; then
      install_package "$package" "$executable"
    fi
  done
  if ! command -v bun >/dev/null; then
    if ! command -v omarchy-install-dev-env >/dev/null; then
      echo "omarchy-install-dev-env is unavailable, so bun cannot be installed for Blip. Blip was not enabled." >&2
      exit 1
    fi
    # Omarchy's dev-env command runs `mise use -g bun@latest`, and mise then runs
    # `bun -v`. Do not use bun's curl installer. The preview above is the gate.
    # Create the directory here: preview_bun_release runs in a command substitution.
    if ! bun_preview_dir=$(mktemp -d); then
      echo "Could not create a temporary directory for the bun release. Blip was not enabled." >&2
      exit 1
    fi
    if ! checked_version=$(preview_bun_release "$bun_preview_dir"); then
      exit 1
    fi
    omarchy-install-dev-env bun
    hash -r
    if ! command -v bun >/dev/null; then
      echo "bun is still unavailable after omarchy-install-dev-env bun. Blip was not enabled." >&2
      exit 1
    fi
    if ! installed_bun=$(timeout --kill-after=2s 15 mise which bun); then
      echo "mise could not resolve the bun shim after install. Blip was not enabled." >&2
      exit 1
    fi
    if [[ -z $installed_bun || ! -x $installed_bun ]]; then
      echo "The resolved bun binary is not executable: ${installed_bun:-missing}. Blip was not enabled." >&2
      exit 1
    fi
    if ! python3 "$root/scripts/groundwork-update.py" check-load-alignment "$installed_bun"; then
      exit 1
    fi
    # Prefer the version in mise's install path. `mise current` is the fallback
    # when that path does not name a release.
    installed_version=""
    case "$installed_bun" in
      */installs/bun/*/bin/bun)
        installed_version=${installed_bun%/bin/bun}
        installed_version=${installed_version##*/}
        ;;
    esac
    if [[ ! $installed_version =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z]+)*$ ]]; then
      if ! installed_version=$(timeout --kill-after=2s 15 mise current bun); then
        installed_version=""
      fi
      installed_version=${installed_version//$'\r'/}
      read -r installed_version <<<"${installed_version}"
    fi
    if [[ $installed_version != "$checked_version" ]]; then
      if ! command -v omarchy-remove-dev-env >/dev/null || ! omarchy-remove-dev-env bun; then
        printf 'Installed bun (%s) is not the checked release %s, and it could not be removed. bun --version was not run. Blip was not enabled.\n' \
          "${installed_version:-unknown}" "$checked_version" >&2
        exit 1
      fi
      printf 'Installed bun (%s) is not the checked release %s. It was removed before bun --version. Blip was not enabled.\n' \
        "${installed_version:-unknown}" "$checked_version" >&2
      exit 1
    fi
  fi
  bun_bin=$(command -v bun)
  resolved=$bun_bin
  case "$bun_bin" in
    "${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}"/shims/* | \
      "${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}"/command-wrappers/bin/*)
      resolved=$(timeout --kill-after=2s 15 mise which bun) || {
        echo "mise could not resolve the bun shim at $bun_bin. Blip was not enabled." >&2
        exit 1
      }
      ;;
  esac
  if [[ -z $resolved || ! -x $resolved ]]; then
    echo "The resolved bun binary is not executable: ${resolved:-missing}. Blip was not enabled." >&2
    exit 1
  fi
  # Reuse the T3 readelf -lW helper. Do not execute bun when this kernel cannot map it.
  if ! python3 "$root/scripts/groundwork-update.py" check-load-alignment "$resolved"; then
    exit 1
  fi
  if ! timeout --kill-after=2s 15 bun --version >/dev/null; then
    echo "bun --version failed, so Blip was not enabled (oven-sh/bun#17627)." >&2
    echo "https://github.com/oven-sh/bun/issues/17627" >&2
    exit 1
  fi
  local add_command="omarchy plugin add ${blip_url} --enable --yes"
  # Literal tilde: the person runs this in their own home directory.
  # shellcheck disable=SC2088
  local setup_command="~/.config/omarchy/plugins/${blip_id}/scripts/blip-setup GATEWAY_USER@GATEWAY_MAGICDNS_NAME"
  if [[ -z ${HYPRLAND_INSTANCE_SIGNATURE:-} ]]; then
    echo "No Hyprland session detected; Blip stays pending until this runs in the Omarchy desktop:"
    echo "  $add_command"
    echo "If that reports the plugin is already installed, do not add it again. Then run:"
    echo "  $setup_command"
    echo "Answer n to the Inter font prompt. Leave tapbacks off. See docs/imessage.md."
    return 0
  fi
  if ! listed=$(omarchy plugin list --json); then
    echo "omarchy plugin list failed, so Blip was not enabled. On the desktop, run:" >&2
    echo "  $add_command" >&2
    exit 1
  fi
  list_status=0
  printf '%s\n' "$listed" | python3 "$root/scripts/groundwork-update.py" plugin-listed "$blip_id" || list_status=$?
  if [[ $list_status -eq 0 ]]; then
    echo "nixfred.blip is already installed; it was left unchanged."
  elif [[ $list_status -eq 1 ]]; then
    omarchy plugin add "$blip_url" --enable --yes
    added=1
  else
    echo "omarchy plugin list --json could not be read, so Blip was not enabled." >&2
    exit 1
  fi
  if [[ $updating -eq 0 || $added -eq 1 ]]; then
    echo "Next, in this desktop session, run:"
    echo "  $setup_command"
    echo "The wizard needs a person at the Mac. It is not run from this installer. See docs/imessage.md."
  fi
}
if [[ $with_blip -eq 1 ]]; then
  install_blip
fi

python3 "$root/scripts/groundwork-update.py" register "$root" "$with_keyd" "$with_screen_sharing" \
  "${GROUNDWORK_OBSIDIAN_VAULT:-$HOME/Documents/Obsidian/my_obsidian_vault}" "$with_blip"
if [[ $updating -eq 1 ]]; then
  echo "Groundwork settings applied. Saved keyd, screen-sharing, Blip, and vault choices were preserved."
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
