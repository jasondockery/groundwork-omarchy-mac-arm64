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

- **Mask `tmp.mount`.** Setup and `update-all` run `sudo systemctl mask tmp.mount` when `systemctl is-enabled tmp.mount` does not already say `masked`. After the next boot, `/tmp` is a plain directory on the btrfs root. Nothing stops, unmounts, resizes, or empties the live `/tmp`: the change waits for a reboot, and setup prints that a reboot moves `/tmp` to disk. `privileged_changes_needed` lists the mask, so an update asks for sudo once, and only while the mask is missing. Without a terminal it stops before changing anything, as for every other privileged step. A `tmp.mount` unit file of its own in `/etc/systemd/system` (left by `systemctl edit --full tmp.mount`, or a copy) makes `systemctl mask` fail with "already exists". Setup reports that file, does not mask, and does not ask for sudo for it; `sudo systemctl revert tmp.mount` drops it. If the mask itself fails, the rest of setup still runs, and setup then exits 1 with the reason.
- **On by default, on every machine this layer targets.** The evidence is this 16 GB mini: half of its RAM is too small a cap for builds, and with zram-only swap that half cannot leave memory. An Apple Silicon Mac with 8 GB hits the limit sooner. One with 32 to 64 GB has a larger cap but the same cost, since whatever `/tmp` holds still takes RAM from builds. So disk is the default everywhere, and a machine that prefers RAM opts out. `--without-tmp-on-disk` saves `"tmp_on_disk": false` in `~/.config/groundwork/omarchy.json`, and later runs keep that choice. Only the opt-out is saved, so the machine's config lists only what differs from this default. Turning it off leaves an existing mask alone, as `--without-keyd` does for keyd.
- **Never an `/etc/fstab` line.** Setup never writes `/etc/fstab`, and nothing it does depends on fstab. A mask in `/etc/systemd/system` outranks the fstab generator's output, so such a line would not mount `/tmp` again. The risk is different: the generator makes `local-fs.target` require `tmp.mount`, and a required unit that is masked can fail the boot. The line also says the opposite of the mask. So while such a line exists, setup does not mask. It prints the line and says to remove it by hand, and `groundwork-doctor` reports it.
- **Keep upstream ageing.** Upstream `systemd-tmpfiles` keeps cleaning the on-disk `/tmp`. It sets mode 1777 with the sticky bit, and `systemd-tmpfiles-clean.timer` removes entries older than 10 days. The root is mounted `noatime`, so a read never refreshes a file's age: a file that is read but not modified for 10 days is deleted. Keep anything long-lived out of `/tmp`. `/tmp` is also no longer emptied at reboot, because the `q` line does not clean at boot and there is no tmpfs to discard. Any files that were left in the `/tmp` directory on `@` underneath the old tmpfs show up after the first reboot and age out the same way. This layer does not copy the unit. `groundwork-doctor` checks that `/usr/lib/tmpfiles.d/tmp.conf` still gives `/tmp` mode 1777 with an age, and that no `/etc/tmpfiles.d/tmp.conf` or `/run/tmpfiles.d/tmp.conf` replaces it.
- **Optional scratch roots.** `"scratch_dirs": ["work-scratch"]` in the machine's `omarchy.json` names directories under `~/.cache`. The names are local to the machine and never come from this repository. Each name must be one plain path segment: letters, digits, `.`, `_` and `-`, starting with a letter or digit, at most 64 characters, and at most 16 names. Setup and `update-all` create each one with mode 0700 (`scripts/groundwork-tmp.py apply-scratch`). A name whose resolved path is on tmpfs or ramfs, inside a git work tree, inside `~/code` (`GROUNDWORK_CODE_DIR`), or overlapping a Syncthing folder this layer manages (`~/Shared`, read from `scripts/groundwork-syncthing.py`) is refused and not created. The rest of setup still runs, and setup then exits 1 with the reason. Register keeps the key across runs. Nothing ages the scratch roots: no tmpfiles rule covers `~/.cache`, and this layer never deletes inside them, so whatever writes there cleans up after itself.
- **`groundwork-doctor`** adds a "Temp storage" section. It reports `/tmp`'s backing (tmpfs or the disk filesystem), whether `tmp.mount` is masked (or that its state is unknown when `systemctl` gives no answer), a reboot still pending after the mask, an fstab `/tmp` line, a custom `tmp.mount` unit, a masked `systemd-tmpfiles-clean.timer` (read only, never changed), the tmpfiles ageing, and each scratch root (missing, wrong mode, or refused). Each finding gives a plain next step. `update-all` runs `--brief` as before.

## Why not a bigger tmpfs

A larger `size=` only raises the RAM that `/tmp` can take from builds. With zram as the only swap, those pages cannot leave memory. The disk has hundreds of GB free, and btrfs compression (`zstd:3`) already applies to the root.

## Why no fstab line

The mask is one reversible unit state, which `systemctl unmask` undoes. An fstab entry for a disk-backed `/tmp` is unnecessary, because the root filesystem already holds `/tmp`. An fstab `/tmp` line next to the mask would make `local-fs.target` require a masked unit, which can fail the boot, and it states the opposite intent. Editing fstab is a boot-critical change this layer does not make.

## Backups, sync, and snapshots

- **Syncthing** is the only sync this layer manages. Its folders are `~/Shared` and `~/Shared/FreeTube`. Scratch roots live under `~/.cache` and are refused if they resolve into either.
- **Snapper is Omarchy's, not this layer's.** Omarchy configures one snapper config, `root`, on `/` (subvolume `@`, timeline off, `NUMBER_LIMIT=5`), and `omarchy-update` takes a snapshot before it updates. Once `tmp.mount` is masked, `/tmp` is part of `@`, so files in `/tmp` during an update are captured in that snapshot. Their space is held until snapper's number cleanup drops the snapshot, after five more snapshots. This layer does not change snapper and adds no snapshot subsystem. Making `/tmp` a nested btrfs subvolume, which snapshots of `@` do not include, is a manual step this layer doesn't take. A snapshot is not recursive: the nested subvolume shows up in a snapshot of its parent as an empty stub (inode 2), so the files under it are not in that snapshot. One method, not run on a mounted filesystem here: after the reboot that puts `/tmp` on disk, mount subvolume `@` from a live USB at a path that is not the running system's `/`. Do not empty or `rmdir` `/tmp` from the running system or from `rescue.target` while that directory is still a mount point. `rmdir` of a mount point fails with EBUSY (Device or resource busy). A filesystem also cannot be unmounted while it is busy, which includes a filesystem that still has another mount underneath it, so unmount the nested mount first and the mount on the directory second. If `sudo btrfs subvolume show` says that directory is a subvolume which still has a child subvolume, do not `rmdir` it and do not delete the parent first. Without `-R`, `btrfs subvolume delete` of a subvolume that still has a child is an error: the kernel returns ENOTEMPTY (Directory not empty) while a child subvolume remains. `sudo btrfs subvolume delete -R` deletes the subvolumes beneath it before it deletes that subvolume. That recursive delete is not atomic and needs root. If the directory is not a subvolume, empty it and `rmdir` it only when it is not a mount point. An empty subvolume with no child can be removed with `rmdir` where `/sys/fs/btrfs/features/rmdir_subvol` is present. Then boot the installed system with that path absent. Upstream's `q /tmp 1777 root root 10d` line creates a subvolume only when the path does not exist yet and `/` is itself a subvolume; a directory or subvolume that is still there is left as it is. Check afterwards with `sudo btrfs subvolume show /tmp` and `stat -c %a /tmp` (`1777`). Trying this while a tmpfs is still mounted on `/tmp` fails with EBUSY, because the directory under `subvol=@` is the one the tmpfs is mounted on.
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
