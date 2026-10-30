# Syncthing on the mini: on-device checklist

Fixtures cannot prove [issue 60](https://github.com/jasondockery/groundwork-omarchy-mac-arm64/issues/60) item 6. This is the run to do on the Mac mini and one second machine. The contract is [groundwork-core `docs/syncthing.md`](https://github.com/jasondockery/groundwork-core/blob/main/docs/syncthing.md); this page does not restate it. Record the output of each step (without device IDs, API keys or tailnet addresses) in the issue.

Every command is read-only except steps 1, 6 and 7. Nothing here pauses or unpauses `groundwork-shared`.

## Before you start

- Tailscale is signed in on both machines (`tailscale ip -4` prints an address).
- Run `date` and `uname -r` and note the kernel; the page size must be 16 KiB (`getconf PAGESIZE` prints `16384`).

## On the mini

1. Run `./install.sh` (or `groundwork-update`). Expect exit 0 and no "Not finished" line. If it says Syncthing is waiting for Tailscale, sign in, then rerun.
2. Binary and pages. `syncthing --version` is 2.1.2 or newer. `readelf -lW "$(command -v syncthing)" | awk '$1=="LOAD"{print $NF}'` shows only `0x4000` or larger. `systemctl --user is-active syncthing.service` prints `active`. `systemctl --user status syncthing.service` shows no crash loop.
3. Linger. `loginctl show-user "$USER" -p Linger` prints `Linger=yes`.
4. Sockets. `ss -H -tulpn | grep syncthing`. Every local address is `127.0.0.1` (GUI `8384`) or this machine's tailnet address on `22000` (tcp and udp). Nothing on `0.0.0.0`, `[::]`, `*` or a LAN address. Repeat with `sudo ufw status` active if ufw is installed, and note whether `22000` on `tailscale0` is admitted (the apply does not touch the firewall).
5. GUI override absent. `systemctl --user show syncthing.service -p Environment -p ExecStart` contains neither `STGUIADDRESS` nor `--gui-address`.
6. Options on the running daemon. Read the key without printing it, then query:

   ```bash
   key=$(sed -n 's:.*<apikey>\(.*\)</apikey>.*:\1:p' "${XDG_STATE_HOME:-$HOME/.local/state}/syncthing/config.xml")
   curl -fsS -H "X-API-Key: $key" http://127.0.0.1:8384/rest/config/options |
     python3 -c 'import json,sys; o=json.load(sys.stdin); print({k:o[k] for k in ("listenAddresses","relaysEnabled","globalAnnounceEnabled","localAnnounceEnabled","natEnabled")})'
   ```

   `listenAddresses` has only `tcp://` and `quic://` entries on the tailnet address (none is `default`), and the four flags are `False`. Also `GET /rest/system/connections` `connectionServiceStatus` keys equal those URLs with no `error`.
7. Folders. `GET /rest/config/folders/groundwork-shared` has path `~/Shared`, label `Shared`, `paused` false, no versioning. `groundwork-freetube` has path `~/Shared/FreeTube`, label `FreeTube`, `paused` false, `versioning.type` `trashcan`, `params.cleanoutDays` `0`. `~/Shared/.stignore` starts with `/FreeTube`; `~/Shared/FreeTube/.stignore` contains `*~`.
8. Idempotence. Note both folders' `paused` and `path`, run `groundwork-update` again, and confirm they are unchanged and the daemon did not restart (`GET /rest/system/status` `startTime` is the same).
9. Permissions. `stat -c '%a' "${XDG_STATE_HOME:-$HOME/.local/state}/syncthing/config.xml" "${XDG_STATE_HOME:-$HOME/.local/state}/syncthing/key.pem"` prints `600` twice, also after step 8.
10. Reboot the mini and do not log in. From the second machine, the mini's device is reachable (linger starts the service at boot).

## Pairing and convergence

11. On each machine, `groundwork syncthing-pair show` prints that machine's device ID and MagicDNS name.
12. On the mini, `groundwork syncthing-pair add --device-id <their ID> --name <name> --magicdns <their MagicDNS>`. On the second machine, add the mini the same way. Both directions are required. Run each `add` a second time and confirm it reports nothing changed.
13. Confirm both folders are shared with the other device in both GUIs, and the connection is `quic` or `tcp` on a tailnet address, not a relay.
14. Convergence. Create a file in `~/Shared` on the mini; it appears on the second machine. Create a different file there; it appears on the mini. Delete one on each side and confirm both disappear. Confirm `~/Shared/FreeTube/*` is not listed under `groundwork-shared` (`GET /rest/db/browse?folder=groundwork-shared&prefix=FreeTube` is empty).

## Not covered here

- The FreeTube wrapper, the Flatpak grant and the one-machine warning (see "Not implemented yet" in [decision 0013](decisions/0013-syncthing-shared-folders.md)). `groundwork-freetube` syncs an empty folder until they land.
- Resetting a parent that already indexed FreeTube, which needs a paused folder; if the helper stops with that message, follow it and record whether Syncthing really required the pause.
- The Asahi battery attributes on a MacBook.
