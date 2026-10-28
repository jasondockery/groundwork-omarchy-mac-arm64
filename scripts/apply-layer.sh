#!/bin/bash
# Link the personal Hyprland override into $HOME.
# For any Apple Silicon Omarchy Mac (aarch64). Does not touch Git, SSH,
# groundwork-core, groundwork-mac, or /usr/share/omarchy.
set -euo pipefail

usage() {
  echo "Usage: ./install.sh [--with-keyd|--without-keyd] [--with-screen-sharing|--without-screen-sharing] [--with-blip|--without-blip] [--with-local-inference|--without-local-inference] [--migrate-to-omarchy-installers]"
  echo "Caps Lock defaults to Escape on tap and Control while held."
  echo "Saved setup choices are reused; explicit options change those choices."
  echo "--without-keyd leaves any existing keyd configuration and service unchanged."
  echo "--without-screen-sharing leaves any existing desktop-sharing service unchanged."
  echo "--with-blip installs the Blip iMessage client. It is off by default."
  echo "--without-blip leaves any existing Blip install unchanged."
  echo "--with-local-inference installs ollama and ollama-vulkan. It is off by default."
  echo "--without-local-inference records that preference as off. See docs/local-inference.md."
  echo "--migrate-to-omarchy-installers moves apps installed another way to Omarchy's own installer,"
  echo "  even after an earlier no. Without it, setup asks first and updates only report."
}

with_keyd=1
with_screen_sharing=1
with_blip=0
with_local_inference=0
keyd_choice=""
sharing_choice=""
blip_choice=""
local_inference_choice=""
updating=0
update_plugins=0
migrate_requested=0
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
    --with-local-inference | --without-local-inference)
      if [[ -n $local_inference_choice && $local_inference_choice != "$arg" ]]; then
        echo "Choose only one of --with-local-inference and --without-local-inference." >&2
        exit 64
      fi
      local_inference_choice=$arg
      [[ $arg == --with-local-inference ]] && with_local_inference=1 || with_local_inference=0
      ;;
    --update) updating=1 ;;
    --update-plugins) update_plugins=1 ;;
    --migrate-to-omarchy-installers) migrate_requested=1 ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done
if [[ $updating -eq 1 && $migrate_requested -eq 1 ]]; then
  echo "--migrate-to-omarchy-installers is a setup option. Run ./install.sh --migrate-to-omarchy-installers; nothing was changed." >&2
  exit 64
fi

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
# Comma-separated migration offers the person declined (decision 0014).
declined_migrations=""
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
  if [[ -z $local_inference_choice ]]; then
    case ${preferences[4]-} in
      '') ;;
      0|1) with_local_inference=${preferences[4]} ;;
      *)
        echo "Saved local inference choice is invalid. Preserve ~/.config/groundwork/omarchy.json and repair it before rerunning." >&2
        exit 1
        ;;
    esac
  fi
  declined_migrations=${preferences[5]-}
fi

# Fail before installing packages or moving files when the checkout is incomplete.
bitwarden_extension=nngceckbapebfimnlniiiahkandclblb.json
extension_source="$root/chromium/extensions/$bitwarden_extension"
[[ -f "$root/keyd/default.conf" && -f "$root/packages/hypr/.config/hypr/input.lua" && -f "$root/scripts/seed-obsidian.py" && -f "$root/scripts/groundwork-screen-share.py" && -f "$root/scripts/groundwork-syncthing.py" && -f "$root/scripts/omarchy-plugins.py" && -f "$extension_source" ]] || {
  echo "The checkout is incomplete: keyd config, Hyprland input override, Obsidian helper, Syncthing helper, plugin helper, or Bitwarden extension manifest is missing." >&2
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
# The desktop ICD is installed by Omarchy (vulkan-aurora provides vulkan-asahi).
# A symlink is refused so a private ICD cannot satisfy this check.
honeykrisp_icd_present() {
  local icd
  for icd in "$test_root"/usr/share/vulkan/icd.d/*asahi*.json; do
    [[ -f $icd && ! -L $icd ]] && return 0
  done
  return 1
}
# Omarchy's Install > Service > Bitwarden adds the bitwarden package. The
# aarch64 feed first had it on 2026-10-28; before that only the Flatpak worked.
# pacman -Si reads the local sync database and needs no sudo.
bitwarden_flatpak=com.bitwarden.desktop
bitwarden_native_available() {
  command -v pacman >/dev/null && pacman -Si bitwarden >/dev/null 2>&1
}
bitwarden_flatpak_installed() {
  command -v flatpak >/dev/null && flatpak --user info "$bitwarden_flatpak" >/dev/null 2>&1
}
local_inference_ready() {
  local vulkan_dir=$test_root/usr/lib/ollama/vulkan
  command -v ollama >/dev/null || return 1
  command -v vulkaninfo >/dev/null || return 1
  [[ -d $vulkan_dir ]] || return 1
  compgen -G "$vulkan_dir/*" >/dev/null
}
# Lingering lets the user service start at boot without a graphical login. Only
# a machine with no system battery (a desktop such as the Mac mini) is the
# always-on peer; a MacBook (the Asahi SMC registers macsmc-battery) starts Syncthing at
# login like every other machine.
syncthing_user=$(id -un)
syncthing_always_on() {
  local supply
  for supply in "$test_root"/sys/class/power_supply/*; do
    [[ -r $supply/type && $(<"$supply/type") == Battery ]] || continue
    # A Bluetooth keyboard or mouse is a Battery with device scope, not this machine's.
    [[ -r $supply/scope && $(<"$supply/scope") == Device ]] && continue
    return 1
  done
  return 0
}
syncthing_linger_enabled() {
  [[ -e $test_root/var/lib/systemd/linger/$syncthing_user ]]
}
syncthing_linger_needed() {
  syncthing_always_on && ! syncthing_linger_enabled
}
privileged_changes_needed() {
  local executable
  # Missing tools are installed through omarchy pkg add or Omarchy's installers.
  # readelf (binutils) is what update-all uses to check T3 Code builds.
  for executable in cursor herdr tailscale syncthing obsidian bw readelf; do
    command -v "$executable" >/dev/null || return 0
  done
  # FreeTube is a user Flatpak, so a missing flatpak package still needs
  # administrator permission even when a native Bitwarden desktop is present.
  if ! command -v flatpak >/dev/null; then
    return 0
  fi
  # A missing Bitwarden desktop becomes Omarchy's package when the feed has it.
  if ! command -v bitwarden >/dev/null && ! bitwarden_flatpak_installed && bitwarden_native_available; then
    return 0
  fi
  service_ready tailscaled || return 0
  ! syncthing_linger_needed || return 0
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
  if [[ $with_local_inference -eq 1 ]] && ! local_inference_ready; then
    return 0
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
      echo "This install needs an administrator password for Tailscale, the browser extension, keyd, and lingering."
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
# Omarchy owns Cursor. Older Omarchy Mac builds ship omarchy-install-cursor;
# current Omarchy installs the cursor-bin package. An existing cursor command
# stays put, and no migration is offered for it (decision 0014). This only fills
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
      # Since Omarchy Mac converged into upstream (omacom/omarchy#14431), the
      # Install menu adds cursor-bin; the password store is filled in below.
      install_package cursor-bin cursor
    else
      omarchy-install-cursor
      hash -r
      if ! command -v cursor >/dev/null; then
        echo "omarchy-install-cursor did not provide a cursor command." >&2
        exit 1
      fi
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

# Omarchy owns Obsidian's package, theme, and launch shortcut. A new Bitwarden
# desktop install uses Omarchy's bitwarden package when the feed has it, and
# its ARM64 Flathub distribution otherwise. An existing native app or user
# Flatpak is kept; moving the Flatpak to the package is only offered (decision
# 0014). Sign-in remains user-owned, with no vault export in this repo.
# FreeTube is the same kind of gap. Checked 2026-10-24: freetube is absent
# from Arch Linux, Arch Linux ARM, pkgs.omarchy.org/edge/aarch64, and ALARM's
# prebuilt aur database. AUR freetube-bin is a PKGBUILD, and omarchy pkg add
# is pacman-only. Flathub io.freetubeapp.FreeTube unpacks upstream's
# linux-arm64 portable zip (FreeTube 0.25.3-beta, Electron 42.5.2). Every ELF
# in that zip has LOAD alignment 0x10000. The deployed app's merged Electron
# BaseApp ELFs, and org.freedesktop.Platform 25.08 aarch64 including libc.so.6
# and ld-linux-aarch64.so.1, are also aligned to 0x10000. This build has not
# been launched on the reference M1 mini yet. Settings stay upstream.
# A missing flatpak package still stops setup inside install_package. A
# FreeTube remote or install failure is non-critical and must not skip the
# rest of setup.
flathub_ready=0
ensure_user_flatpak() {
  local app=$1
  if command -v flatpak >/dev/null && flatpak --user info "$app" >/dev/null 2>&1; then
    return 0
  fi
  if [[ $flathub_ready -eq 0 ]]; then
    install_package flatpak
    if ! flatpak --user remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo; then
      return 1
    fi
    flathub_ready=1
  fi
  if ! flatpak --user info "$app" >/dev/null 2>&1; then
    if ! flatpak --user install --noninteractive flathub "$app"; then
      return 1
    fi
  fi
  flatpak --user info "$app" >/dev/null
}
install_native_bitwarden() {
  omarchy pkg add bitwarden || return 1
  hash -r
  command -v bitwarden >/dev/null
}
install_package obsidian
install_package bitwarden-cli bw
if ! command -v bitwarden >/dev/null && ! bitwarden_flatpak_installed; then
  if bitwarden_native_available; then
    if ! install_native_bitwarden; then
      echo "Omarchy's bitwarden package did not install; using the Bitwarden Flatpak instead." >&2
      ensure_user_flatpak "$bitwarden_flatpak"
    fi
  else
    ensure_user_flatpak "$bitwarden_flatpak"
  fi
fi
freetube_ready=1
if ! ensure_user_flatpak io.freetubeapp.FreeTube; then
  freetube_ready=0
  echo "FreeTube could not be installed. Setup continues. Resolve the Flatpak error and rerun ./install.sh." >&2
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
  if ! reload_output=$(hyprctl reload); then
    printf '%s\n' "$reload_output" >&2
    exit 1
  fi
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

check_elf_alignment() {
  local elf=$1 status=0
  python3 "$root/scripts/groundwork-update.py" check-elf-alignment "$elf" || status=$?
  if [[ $status -eq 0 ]]; then
    return 0
  fi
  if [[ $status -eq 2 ]]; then
    return 2
  fi
  echo "Refusing to treat local inference as installed. ollama.service was not enabled." >&2
  echo "Remove the packages with: sudo pacman -Rns ollama-vulkan ollama" >&2
  exit 1
}
install_local_inference() {
  local vulkan_dir=$test_root/usr/lib/ollama/vulkan elf bin status checked=0
  echo "Local inference is opt-in. This repository has no completed M1 GPU run."
  echo "See docs/local-inference.md. No model is downloaded and ollama.service is not enabled."
  if [[ $updating -eq 1 ]] && local_inference_ready; then
    return 0
  fi
  if ! honeykrisp_icd_present; then
    echo "No Asahi Vulkan ICD in ${test_root}/usr/share/vulkan/icd.d." >&2
    echo "Omarchy's desktop driver is vulkan-aurora. It provides vulkan-asahi and conflicts with the ALARM vulkan-asahi package." >&2
    echo "This layer does not install a second Mesa. Local inference was not installed." >&2
    exit 1
  fi
  omarchy pkg add ollama
  omarchy pkg add ollama-vulkan
  if ! command -v ollama >/dev/null; then
    echo "ollama is still unavailable after omarchy pkg add. The helper may skip packages absent from the current feed." >&2
    exit 1
  fi
  if [[ ! -d $vulkan_dir ]] || ! compgen -G "$vulkan_dir/*" >/dev/null; then
    echo "ollama-vulkan did not install $vulkan_dir. The base ollama package strips its Vulkan libraries, so inference would use the CPU." >&2
    echo "ollama.service was not enabled." >&2
    exit 1
  fi
  while IFS= read -r -d '' elf; do
    status=0
    check_elf_alignment "$elf" || status=$?
    if [[ $status -eq 0 ]]; then
      checked=$((checked + 1))
    fi
  done < <(find "$vulkan_dir" -type f ! -type l -print0)
  if [[ $checked -lt 1 ]]; then
    echo "ollama-vulkan installed no ELF under $vulkan_dir, so the Vulkan backend is not present." >&2
    exit 1
  fi
  bin=$(command -v ollama)
  status=0
  check_elf_alignment "$bin" || status=$?
  if [[ $status -ne 0 ]]; then
    echo "The ollama executable is not an ELF this kernel can map: $bin" >&2
    exit 1
  fi
  if ! command -v vulkaninfo >/dev/null; then
    omarchy pkg add vulkan-tools
  fi
  if ! command -v vulkaninfo >/dev/null; then
    echo "vulkaninfo is still unavailable after omarchy pkg add vulkan-tools." >&2
    exit 1
  fi
  echo "ollama and ollama-vulkan are installed. GPU use is unproven until you run, on this Mac:"
  echo "  python3 scripts/verify-local-inference.py preflight"
  echo "  python3 scripts/verify-local-inference.py download"
  echo "  python3 scripts/verify-local-inference.py run"
}
if [[ $with_local_inference -eq 1 ]]; then
  install_local_inference
fi

# Syncthing is a default on every Groundwork machine (groundwork-core/docs/syncthing.md).
# This Mac mini is the always-on peer, so lingering starts the user service at boot.
# The helper holds the contract: tailnet-only listen addresses, the two shared
# folders, and groundwork syncthing-pair. It waits, rather than failing setup,
# when Tailscale has no address yet.
syncthing_problem=""
syncthing_pending_note=""
install_syncthing() {
  local bin status=0 apply_status=0 pending=""
  syncthing_pending_note=""
  install_package syncthing
  bin=$(command -v syncthing)
  # Apple Silicon kernels use 16 KiB pages. A non-ELF wrapper is skipped here;
  # the helper refuses a PATH copy that is not the one the service runs.
  # Only an ELF needs the check; a wrapper script skips the interpreter start.
  if [[ $(head -c 4 "$bin") == $'\x7f'ELF ]]; then
    python3 "$root/scripts/groundwork-update.py" check-elf-alignment "$bin" || status=$?
  fi
  if [[ $status -ne 0 && $status -ne 2 ]]; then
    echo "Refusing to treat Syncthing as installed. syncthing.service was not enabled." >&2
    echo "If the reason above is the package itself, remove it with: sudo pacman -Rns syncthing" >&2
    exit 1
  fi
  if syncthing_linger_needed; then
    if ! sudo loginctl enable-linger "$syncthing_user"; then
      pending="Lingering is not enabled, so Syncthing starts at login instead of boot."
      echo "Could not enable lingering for $syncthing_user. $pending Rerun groundwork-update." >&2
    fi
  fi
  python3 "$root/scripts/groundwork-syncthing.py" apply || apply_status=$?
  case $apply_status in
    0) ;;
    75)
      pending="${pending:+$pending }Syncthing is waiting for Tailscale to have an address."
      echo "Syncthing is waiting for Tailscale to have an address. Rerun groundwork-update after tailnet sign-in." >&2
      ;;
    *)
      # Register first so the update commands exist for the retry.
      syncthing_problem="Syncthing could not be applied (exit $apply_status). Fix the message above and rerun."
      pending="${pending:+$pending }$syncthing_problem"
      ;;
  esac
  syncthing_pending_note=$pending
  # An update reads this to report a warning instead of a clean success. The
  # updater validates the path, so only start it when the marker must change.
  local marker=${XDG_STATE_HOME:-$HOME/.local/state}/groundwork/syncthing-pending
  if [[ -n $pending ]]; then
    if [[ -L $marker || ! -f $marker || $(<"$marker") != "$pending" ]]; then
      python3 "$root/scripts/groundwork-update.py" marker write syncthing-pending "$pending"
    fi
  elif [[ -e $marker || -L $marker ]]; then
    python3 "$root/scripts/groundwork-update.py" marker remove syncthing-pending
  fi
}
install_syncthing

# Detect and offer, never force (decision 0014). An app installed another way
# than Omarchy's own installer is reported. It moves only after consent: a yes
# at the setup prompt, or --migrate-to-omarchy-installers. Updates and runs
# without a terminal only print one line. A no is saved with the setup choices.
# Offers run last so a failed migration never skips the rest of setup, and the
# choices below are still saved. To add an app, detect it and call
# offer_omarchy_migration with an id, the current and preferred methods, the
# plan, and a function that returns nonzero, after printing why, on failure.
migration_offered=0
migration_failed=0
migration_declined() {
  [[ ,$declined_migrations, == *",$1,"* ]]
}
remember_migration_decline() {
  migration_declined "$1" || declined_migrations=${declined_migrations:+$declined_migrations,}$1
}
forget_migration_decline() {
  local id kept="" ids=()
  IFS=, read -r -a ids <<<"$declined_migrations"
  for id in "${ids[@]}"; do
    [[ -z $id || $id == "$1" ]] || kept=${kept:+$kept,}$id
  done
  declined_migrations=$kept
}
offer_omarchy_migration() {
  local id=$1 app=$2 current=$3 preferred=$4 plan=$5 migrate=$6 answer command
  migration_offered=1
  printf -v command 'cd %q && ./install.sh --migrate-to-omarchy-installers' "$root"
  if [[ $migrate_requested -eq 0 ]]; then
    if migration_declined "$id"; then
      [[ $updating -eq 1 ]] || echo "$app stays on $current, as you chose. To move it to $preferred, run: $command"
      return 0
    fi
    # Same terminal test as the Blip update: stdin and stdout.
    if [[ $updating -eq 1 || ! -t 0 || ! -t 1 ]]; then
      echo "$app is installed as $current; Omarchy's preferred method is $preferred. To migrate, run: $command"
      return 0
    fi
    printf '%s\n' "$app is installed as $current. Omarchy's preferred method is $preferred." "$plan"
    while true; do
      if ! read -r -p "Migrate $app now? [y/N] " answer; then
        echo
        echo "No answer, so $app was left as it is. Setup asks again next time."
        return 0
      fi
      case ${answer,,} in
        y | yes) break ;;
        '' | n | no)
          remember_migration_decline "$id"
          echo "Kept $app as $current. Setup will not ask again. To migrate later, run: $command"
          return 0
          ;;
        *) echo "Answer y or n." ;;
      esac
    done
  fi
  echo "Migrating $app to $preferred."
  if ! "$migrate"; then
    migration_failed=1
    return 0
  fi
  forget_migration_decline "$id"
}

migrate_bitwarden_to_native() {
  local running
  running=$(flatpak ps --columns=application 2>/dev/null) || running=""
  if [[ $'\n'$running$'\n' == *$'\n'"$bitwarden_flatpak"$'\n'* ]]; then
    echo "The Bitwarden Flatpak is running. Quit Bitwarden, then rerun ./install.sh --migrate-to-omarchy-installers. Nothing was changed." >&2
    return 1
  fi
  if ! command -v bitwarden >/dev/null && ! install_native_bitwarden; then
    echo "Omarchy's bitwarden package did not install, so the Bitwarden Flatpak was kept unchanged." >&2
    return 1
  fi
  # No --delete-data: the Flatpak's ~/.var/app directory stays for recovery.
  if ! flatpak --user uninstall --noninteractive "$bitwarden_flatpak" || bitwarden_flatpak_installed; then
    echo "The bitwarden package is installed, but the Flatpak is still there. Finish with: flatpak --user uninstall $bitwarden_flatpak" >&2
    return 1
  fi
  echo "Bitwarden now uses Omarchy's bitwarden package. Sign in again in the app. The Flatpak's data in ~/.var/app/$bitwarden_flatpak was kept."
}
if bitwarden_flatpak_installed && bitwarden_native_available; then
  offer_omarchy_migration bitwarden-flatpak "Bitwarden desktop" "the user Flatpak $bitwarden_flatpak" \
    "Omarchy's bitwarden package" \
    "Migrating installs bitwarden with omarchy pkg add, as Omarchy's Install > Service > Bitwarden does, unless it is already installed. Then it runs flatpak --user uninstall $bitwarden_flatpak. Your vault stays in your Bitwarden account; sign in again in the new app. The Flatpak's local data in ~/.var/app/$bitwarden_flatpak is kept. To undo, run flatpak --user install flathub $bitwarden_flatpak and omarchy pkg drop bitwarden. Quit Bitwarden first." \
    migrate_bitwarden_to_native
fi
if [[ $migrate_requested -eq 1 && $migration_offered -eq 0 ]]; then
  echo "Every app this layer manages already uses Omarchy's installer, or Omarchy has none for it. Nothing was migrated."
fi

python3 "$root/scripts/groundwork-update.py" register "$root" "$with_keyd" "$with_screen_sharing" \
  "${GROUNDWORK_OBSIDIAN_VAULT:-$HOME/Documents/Obsidian/my_obsidian_vault}" "$with_blip" "$with_local_inference" \
  "$declined_migrations"
if [[ $migration_failed -eq 1 ]]; then
  echo "A migration to Omarchy's installer did not finish. The rest of setup was applied and saved. Fix the error above, then rerun." >&2
  exit 1
fi
if [[ -n $syncthing_problem ]]; then
  echo "$syncthing_problem" >&2
  exit 1
fi
if [[ $updating -eq 0 && -n $syncthing_pending_note ]]; then
  echo "Not finished: $syncthing_pending_note" >&2
fi
if [[ $updating -eq 1 ]]; then
  echo "Groundwork settings applied. Saved keyd, screen-sharing, Blip, local inference, and vault choices were preserved."
  exit 0
fi
if [[ $freetube_ready -eq 1 ]]; then
  echo "Trackpad configuration, T3 Code, Herdr, Tailscale, Obsidian, Bitwarden, and FreeTube are installed."
else
  echo "Trackpad configuration, T3 Code, Herdr, Tailscale, Obsidian, and Bitwarden are installed."
  echo "FreeTube was not installed."
fi
echo "Next time, run update-all for maintenance or groundwork-update for configuration only."
echo "Open Obsidian with Super+Shift+O; finish vault, sync, and Bitwarden sign-in using docs/apps.md."
echo "Remote access still requires your sign-in and tailnet policy; see docs/remote-access.md."
echo "Complete tailnet sign-in with: sudo tailscale up --timeout=5m"
echo "After reviewing the tailnet policy, enable private SSH with: sudo tailscale set --ssh"
echo "Verify the setup using the first-run checklist in README.md."
echo "Git, SSH account settings, and Omarchy desktop bindings were left alone."
