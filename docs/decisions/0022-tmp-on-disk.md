# 0022. /tmp on disk, and per-user scratch roots under ~/.cache

Checked 2026-11-06 on the M1 mini (16 GB, Omarchy edge). Issue #138.

## Problem

systemd's `tmp.mount` mounts `/tmp` as a tmpfs capped at 50% of RAM. On the 16 GB mini that is about 7.7 GB, and on 2026-11-06 it was 100% full (7.7G used, 1.6M free). Builds and tests that write temporary files failed. Everything in a tmpfs `/tmp` lives in RAM, and this machine's only swap is zram, which is also RAM. A full `/tmp` therefore takes memory from the builds that filled it.

What the machine showed:

- `findmnt /tmp` was `tmpfs`, and `tmp.mount` was static and active.
- `/etc/fstab` has no `/tmp` line.
- `/usr/lib/tmpfiles.d/tmp.conf` already ages `/tmp` (`q /tmp 1777 root root 10d`), and there is no `/etc/tmpfiles.d/tmp.conf`.
- The root filesystem is btrfs (subvolume `@`) with about 261 GB free. `/home` is the separate subvolume `@home`.

## Decision

- **Mask `tmp.mount`.** Setup and `update-all` run `sudo systemctl mask tmp.mount` when `systemctl is-enabled tmp.mount` does not already say `masked`. After the next boot, `/tmp` is a plain directory on the btrfs root. Nothing stops, unmounts, resizes, or empties the live `/tmp`: the change waits for a reboot, and setup prints that a reboot moves `/tmp` to disk. `privileged_changes_needed` lists the mask, so an update asks for sudo once, and only while the mask is missing. Without a terminal it stops before changing anything, as for every other privileged step.
- **On by default, on every machine this layer targets.** Every Apple Silicon Mac Omarchy runs on has 8 to 24 GB of RAM and zram-only swap, so the same limit applies to all of them. Smaller machines hit it sooner. `--without-tmp-on-disk` saves `"tmp_on_disk": false` in `~/.config/groundwork/omarchy.json`, and later runs keep that choice. Only the opt-out is saved, so the machine's config lists only what differs from this default. Turning it off leaves an existing mask alone, as `--without-keyd` does for keyd.
- **Never an `/etc/fstab` line.** Setup never writes `/etc/fstab`, and nothing it does depends on fstab. A `/tmp` line there would defeat the mask: systemd's fstab generator would mount `/tmp` again, and masking a unit that `local-fs.target` requires could fail the boot. So while such a line exists, setup does not mask. It prints the line and says to remove it by hand, and `groundwork-doctor` reports it.
- **Keep upstream ageing.** Upstream `systemd-tmpfiles` keeps cleaning the on-disk `/tmp`: mode 1777 with the sticky bit, and files unused for 10 days are removed. This layer does not copy the unit. `groundwork-doctor` checks that `/usr/lib/tmpfiles.d/tmp.conf` still gives `/tmp` mode 1777 with an age, and that no `/etc/tmpfiles.d/tmp.conf` or `/run/tmpfiles.d/tmp.conf` replaces it.
- **Optional scratch roots.** `"scratch_dirs": ["work-scratch"]` in the machine's `omarchy.json` names directories under `~/.cache`. The names are local to the machine and never come from this repository. Each name must be one plain path segment: letters, digits, `.`, `_` and `-`, starting with a letter or digit, at most 64 characters, and at most 16 names. Setup and `update-all` create each one with mode 0700 (`scripts/groundwork-tmp.py apply-scratch`). A name whose resolved path is on tmpfs or ramfs, inside a git work tree, inside `~/code` (`GROUNDWORK_CODE_DIR`), or overlapping a Syncthing folder this layer manages (`~/Shared`, read from `scripts/groundwork-syncthing.py`) is refused and not created. The rest of setup still runs, and setup then exits 1 with the reason. Register keeps the key across runs.
- **`groundwork-doctor`** adds a "Temp storage" section. It reports `/tmp`'s backing (tmpfs or the disk filesystem), whether `tmp.mount` is masked, a reboot still pending after the mask, an fstab `/tmp` line, the tmpfiles ageing, and each scratch root (missing, wrong mode, or refused). Each finding gives a plain next step. `update-all` runs `--brief` as before.

## Why not a bigger tmpfs

A larger `size=` only raises the RAM that `/tmp` can take from builds. With zram as the only swap, those pages cannot leave memory. The disk has hundreds of GB free, and btrfs compression (`zstd:3`) already applies to the root.

## Why no fstab line

The mask is one reversible unit state, which `systemctl unmask` undoes. An fstab entry for a disk-backed `/tmp` is unnecessary, because the root filesystem already holds `/tmp`. Any fstab `/tmp` line competes with the mask, and editing fstab is a boot-critical change this layer does not make.

## Backups, sync, and snapshots

- **Syncthing** is the only sync this layer manages. Its folders are `~/Shared` and `~/Shared/FreeTube`. Scratch roots live under `~/.cache` and are refused if they resolve into either.
- **Snapper is Omarchy's, not this layer's.** Omarchy configures one snapper config, `root`, on `/` (subvolume `@`, timeline off, `NUMBER_LIMIT=5`), and `omarchy-update` takes a snapshot before it updates. Once `tmp.mount` is masked, `/tmp` is part of `@`, so files in `/tmp` during an update are captured in that snapshot. Their space is held until snapper's number cleanup drops the snapshot, after five more snapshots. This layer does not change snapper and adds no snapshot subsystem. If that cost matters, the owner can make `/tmp` a nested btrfs subvolume, which snapshots of `@` do not include. That is a manual step, done before the reboot, with `/tmp` mode 1777. This layer does not do it.
- **Scratch roots** are on `@home`, which has no snapper config, so no snapshot holds them.

## Applying and undoing (owner steps)

Applying this to a live machine is the owner's step. Run `update-all` (or `./install.sh`), approve sudo, then reboot. Check that `findmnt -T /tmp` shows the btrfs root (`/dev/mapper/root[/@]`, `btrfs`), not `tmpfs`; plain `findmnt /tmp` prints nothing, because `/tmp` is no longer a mount point. Then run `groundwork-doctor`.

To undo:
1. Run `./install.sh --without-tmp-on-disk` so updates stop masking.
2. Run `sudo systemctl unmask tmp.mount`.
3. Reboot.

Scratch roots are plain directories. Removing a name from `scratch_dirs` stops checking it, and the directory stays until you delete it.

## Not done

- No resizing, remounting, or cleaning of the live `/tmp`, and no `/etc/fstab` or snapper edits.
- groundwork-mac and groundwork-windows already keep their temp directories on disk. groundwork-linux uses groundwork-mac's engine and has no host layer of its own.

Recheck on 2027-02-06, or when systemd changes `tmp.mount` or `tmp.conf` upstream, or when Omarchy starts managing `/tmp` itself.
