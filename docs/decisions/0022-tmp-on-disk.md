# 0022. Put /tmp on disk: mask tmp.mount, keep tmpfiles aging, optional scratch

Checked 2026-11-06 against issue #138. On the 16 GB reference mini, systemd's
`tmp.mount` had `/tmp` as a RAM tmpfs capped at 50% of RAM (about 7.7 GB) and
it was full. Builds that write temp files failed. There was no `/tmp` line in
`/etc/fstab`. `systemd-tmpfiles` already aged `/tmp` through
`/usr/lib/tmpfiles.d/tmp.conf` (`q /tmp 1777 root root 10d`). The root
filesystem is btrfs with hundreds of gigabytes free.

Applying this to a live machine, including the reboot that takes `/tmp` off
tmpfs, is the owner's step after merge. This decision does not mark the
reference machine as changed.

## Decision

- **Mask `tmp.mount`.** `scripts/apply-layer.sh` runs `scripts/groundwork-tmp.py apply`
  on setup and `update-all`. That calls `sudo systemctl mask tmp.mount` when the
  unit is not already a symlink to `/dev/null`. It does not pass `--now` and
  does not unmount `/tmp`. After the next boot, `/tmp` is a directory on the
  disk root. The tmpfs is not enlarged: that would take RAM from the same builds.
- **Never write `/etc/fstab` for `/tmp`.** An fstab `/tmp` line would defeat the
  mask. Apply refuses to mask while one is present and tells the owner to remove
  it. Doctor reports the same.
- **Keep upstream `systemd-tmpfiles` aging.** Apply and doctor read
  `/usr/lib/tmpfiles.d/tmp.conf` (or `/etc/tmpfiles.d/tmp.conf` when that file
  exists and therefore replaces it). They check for a `/tmp` line that creates
  the directory with mode 1777 and age 10 days. They do not copy or write a
  tmpfiles unit.
- **Optional per-user scratch under `~/.cache`.** The directory name is set in
  the machine's `~/.config/groundwork/overrides.json`, not in this repository.
  Apply creates `~/.cache/<name>` and a `CACHEDIR.TAG` so backup tools skip it.
  The path must stay under the cache directory and must not live under a git
  clone, `~/code`, or `~/Shared` (the only sync this layer manages). A missing
  scratch entry means no scratch directory is created.
- **Record both in the override config.** That file lists only settings that
  differ from upstream defaults, each with an `id`, `value`, and `reason`.
  Apply writes `tmp.mount` = `masked` with a reason. Scratch is listed only
  when the machine already named it. `omarchy.json` is unchanged: it stays the
  saved setup choices.
- **Doctor.** `groundwork-doctor` reports an unmasked `tmp.mount`, a `/tmp`
  fstab line, missing or overridden tmpfiles aging, a masked unit whose `/tmp`
  is still tmpfs (reboot), a missing override record, and a configured scratch
  directory that is missing or in a forbidden place. `--brief` prints only
  those problems. Fixture success is not live `/tmp` or reboot acceptance.

Omarchy first: Omarchy has no installer or package that masks `tmp.mount` or
moves `/tmp` onto disk. This is a host layout this layer owns.

groundwork-mac and groundwork-windows already keep their temp dirs on disk.
groundwork-linux uses groundwork-mac's engine and has no host layer of its own.

## Not done

- Unmounting `/tmp` without a reboot.
- A default scratch name in this repository.
- Changing `docs/status.md` from package or unit presence. The live mini is
  unchanged until the owner applies and reboots.

Recheck when systemd changes `tmp.mount` or `/usr/lib/tmpfiles.d/tmp.conf`.
