# Syncthing on the mini: on-device checklist

Fixtures cannot prove [issue 60](https://github.com/jasondockery/groundwork-omarchy-mac-arm64/issues/60) item 6. This is the run to do on the Mac mini and one second machine. The contract is [groundwork-core `docs/syncthing.md`](https://github.com/jasondockery/groundwork-core/blob/main/docs/syncthing.md); this page does not restate it. Record the output of each step (without device IDs, API keys or tailnet addresses) in the issue.

Steps 1, 8, 11, 13 and 14 change state (an install run, a second apply, a device add, test files, a reboot). The rest only read. Nothing here pauses or unpauses `groundwork-shared`.

Every REST check below uses this helper, which reads the key from `config.xml` without printing it and sends it as a header on stdin so it never appears in the process list:

```bash
st() { # st PATH: GET http://127.0.0.1:8384/rest/PATH
  local key; key=$(sed -n 's:.*<apikey>\(.*\)</apikey>.*:\1:p' "${XDG_STATE_HOME:-$HOME/.local/state}/syncthing/config.xml")
  printf 'X-API-Key: %s\n' "$key" | curl -fsS -H @- "http://127.0.0.1:8384/rest/$1"
}
```

## Before you start

- Tailscale is signed in on both machines (`tailscale ip -4` prints an address).
- Run `date` and `uname -r` and note the kernel; the page size must be 16 KiB (`getconf PAGESIZE` prints `16384`).

## On the mini

1. Run `./install.sh` (or `groundwork-update`). Expect exit 0 and no "Not finished" line. If it says Syncthing is waiting for Tailscale, sign in, then rerun.
2. Binary and pages. `syncthing --version` is 2.1.2 or newer. `readelf -lW "$(command -v syncthing)" | awk '$1=="LOAD"{print $NF}'` shows only `0x4000` or larger. `systemctl --user is-active syncthing.service` prints `active`. `systemctl --user status syncthing.service` shows no crash loop.
3. Linger. `loginctl show-user "$USER" -p Linger` prints `Linger=yes`.
4. Sockets. `ss -H -tulpn | grep syncthing`. The expected sync set is: `127.0.0.1:22000` (tcp), the Tailscale IPv4 address on `22000` (tcp and udp), and, only when `tailscale ip -6` prints an address, that IPv6 address on `22000` (tcp and udp). The GUI is `127.0.0.1:8384` (tcp). Compare as a set: every Syncthing socket must be in that set, and the IPv4 pair must be present. Nothing on `0.0.0.0`, `[::]`, `*` or a LAN address. Local discovery would add udp `21027` and must be absent. If ufw is installed and active (`sudo ufw status`), note whether `22000` on `tailscale0` is admitted; the apply does not touch the firewall.
5. GUI override absent. `systemctl --user show syncthing.service -p Environment -p ExecStart` contains neither `STGUIADDRESS` nor `--gui-address`.
6. Options on the running daemon:

   ```bash
   st config/options | python3 -c 'import json,sys; o=json.load(sys.stdin); print({k:o[k] for k in ("listenAddresses","relaysEnabled","globalAnnounceEnabled","localAnnounceEnabled","natEnabled")})'
   ```

   `listenAddresses` is exactly this set, in any order: `tcp://127.0.0.1:22000`, `tcp://<ipv4>:22000`, `quic://<ipv4>:22000`, and, when `tailscale ip -6` prints one, `tcp://[<ipv6>]:22000` and `quic://[<ipv6>]:22000`. None is `default`. The four flags are `False`. `st system/connections` `connectionServiceStatus` has the same keys with no `error`.
7. Folders: `st config/folders/groundwork-shared` and `st config/folders/groundwork-freetube`. `groundwork-shared` has path `~/Shared`, label `Shared`, `paused` false, no versioning. `groundwork-freetube` has path `~/Shared/FreeTube`, label `FreeTube`, `paused` false, `versioning.type` `trashcan`, `params.cleanoutDays` `0`. `~/Shared/.stignore` starts with `/FreeTube`; `~/Shared/FreeTube/.stignore` contains `*~`.
8. Idempotence. Record both folders' `paused` and `path` and `st system/status` `startTime`, run `groundwork-update` again, and repeat the same three reads. They must be unchanged: no restart, no pause, no retarget.
9. Permissions. `stat -c '%a' "${XDG_STATE_HOME:-$HOME/.local/state}/syncthing/config.xml" "${XDG_STATE_HOME:-$HOME/.local/state}/syncthing/key.pem"` prints `600` twice, also after step 8.

## Pairing and convergence

10. On each machine, `groundwork syncthing-pair show` prints that machine's device ID and MagicDNS name.
11. On the mini, `groundwork syncthing-pair add --device-id <their ID> --name <name> --magicdns <their MagicDNS>`. On the second machine, add the mini the same way. Both directions are required. Run each `add` a second time and confirm it reports nothing changed.
12. Confirm both folders are shared with the other device in both GUIs, and the connection is `quic` or `tcp` on a tailnet address, not a relay (`st system/connections`, `type` and `address`).
13. Convergence. Create a file in `~/Shared` on the mini; it appears on the second machine. Create a different file there; it appears on the mini. Delete one on each side and confirm both disappear. Confirm `~/Shared/FreeTube/*` is not listed under `groundwork-shared`: `st 'db/browse?folder=groundwork-shared&prefix=FreeTube'` is `[]`.
14. Boot reachability, last because it needs the pairing. Reboot the mini and do not log in. From the second machine, `tailscale ping <mini MagicDNS>` succeeds and `nc -z <mini MagicDNS> 22000` succeeds (linger started the service at boot). Then log in on the mini and confirm `st system/connections` shows the second machine connected.

## Not covered here

- The FreeTube wrapper, the Flatpak grant and the one-machine warning (see "Not implemented yet" in [decision 0013](decisions/0013-syncthing-shared-folders.md)). `groundwork-freetube` syncs an empty folder until they land.
- Resetting a parent that already indexed FreeTube, which needs a paused folder; if the helper stops with that message, follow it and record whether Syncthing really required the pause.
- The Asahi battery attributes on a MacBook.
