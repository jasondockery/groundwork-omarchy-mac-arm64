# 0013. Syncthing shared folders on the always-on peer

The contract is [groundwork-core `docs/syncthing.md`](https://github.com/jasondockery/groundwork-core/blob/main/docs/syncthing.md), and its rationale is [the decision record](https://github.com/jasondockery/groundwork-core/blob/main/docs/decisions/syncthing-shared-folders.md). This page records only the Omarchy-side choices. It does not copy the contract.

This Mac mini is the always-on peer, so Syncthing is a default here, not an opt-in. `./install.sh`, `groundwork-update` and `update-all` converge on the same result.

## What setup does

| Step | Where |
|---|---|
| `omarchy pkg add syncthing`, then `readelf` LOAD alignment check on the binary (16 KiB pages) | `scripts/apply-layer.sh` |
| `sudo loginctl enable-linger` once, on a machine with no system battery (a desktop such as the Mac mini), so the user service starts at boot | `scripts/apply-layer.sh` |
| Refuse a binary older than 2.1.2, `syncthing generate` with `STNODEFAULTFOLDER=1` when `config.xml` is missing, user drop-in `~/.config/systemd/user/syncthing.service.d/groundwork.conf` | `scripts/groundwork-syncthing.py apply` |
| Create `~/Shared` and `~/Shared/FreeTube`; `/FreeTube` is the first line of `~/Shared/.stignore` and `*~` is in `~/Shared/FreeTube/.stignore`, before either folder exists | same |
| Before the first start, write the tailnet listen list and turn off relays, global and local announce, and NAT in `config.xml`. On a serving machine, `PATCH /rest/config/options` and restart only when the list changed | same |
| `systemctl --user enable --now syncthing.service`, then confirm no `STGUIADDRESS`/`--gui-address`, the `127.0.0.1:8384` socket, and the running listeners | same |
| Create `groundwork-shared` and `groundwork-freetube` through the REST API (trash-can versioning on FreeTube only) | same |

## Choices that are this repo's

- **Tailscale is not signed in on a first install.** Setup finishes the rest, leaves the service disabled, and prints that Syncthing is waiting. `groundwork-update` after `sudo tailscale up` completes it. Syncthing never starts on the upstream `default` listen addresses while it waits. A waiting or linger-less run writes `~/.local/state/groundwork/syncthing-pending`; `groundwork-update` and `update-all` then finish as `completed_with_warnings` (exit 1) until a run clears it. A real apply failure still registers the update commands first, then exits 1.
- **Linger follows the battery, not the model.** The contract enables linger on the always-on mini only. A MacBook starts Syncthing at login. The test is a `/sys/class/power_supply/*` entry of type `Battery` whose scope is not `Device` (the Asahi SMC names its battery `macsmc-battery`; a Bluetooth keyboard is a device-scope battery). An Apple Silicon desktop lingers and a laptop does not. A machine that already lingers is left that way.
- **The binary and state directory are the service's.** The helper reads `ExecStart` and the user manager's `XDG_STATE_HOME` from `systemctl --user`, refuses a different `syncthing` earlier on `PATH`, and stops and disables a service that does not answer with the key from the config it edited. It checks `GET /rest/system/version` as well as the binary, because pacman does not restart a running user service.
- **Folders are created over REST after the first start**, not written into `config.xml` by hand. The contract's ordering invariant still holds: both ignore files exist before either folder is added, so nothing is scanned unignored. This avoids hand-writing a full folder element that Syncthing would otherwise fill with defaults.
- **No Syncthing preference.** There is no `--with-syncthing`; the contract makes it a default. Remove it by hand (`systemctl --user disable --now syncthing.service`, `sudo pacman -Rns syncthing`) and note that the next apply reinstalls it.
- **`groundwork syncthing-pair`** is a launcher at `~/.local/bin/groundwork` that handles only `syncthing-pair`; `groundwork-syncthing pair ...` is the same command. An existing `groundwork` file that is not ours is left alone with a warning. Nothing in this repo installs `groundwork-mac`.
- **Package source:** pacman `syncthing` from the Omarchy feed or Arch Linux ARM `extra`. Do not add a second copy through Flatpak or Homebrew.
- **Rebuilding a parent that already indexed FreeTube** follows the contract: remove other devices, reset, and re-browse. Syncthing may refuse to reset a folder that is not paused; the contract has the apply never pause `groundwork-shared`, so the helper stops with the one manual step (pause it in the GUI, rerun, resume). The devices are already gone by then, so nothing leaks. Whether a pause is required is unproven and is a question for the contract.

## Not implemented yet

The FreeTube wrapper (lock, `lstat` classification, symlink migration, receive-only restore, pause and resume), the Flatpak grant for `~/Shared/FreeTube`, and the one-machine-at-a-time warning on the cheatsheet are specified in the contract and are not part of this change. Until they land, the default FreeTube launcher does not touch `~/Shared/FreeTube`, and `groundwork-freetube` syncs an empty folder.

## Proof that fixtures cannot give

Tests use a fake local Syncthing. They do not show that the real 2.1.x REST API accepts these bodies, that the pacman binary maps on 16 KiB pages, that `quic://` listeners bind to the tailscale address, or that two machines converge. [The on-device checklist](../syncthing-mini-checklist.md) is the run that proves them.
