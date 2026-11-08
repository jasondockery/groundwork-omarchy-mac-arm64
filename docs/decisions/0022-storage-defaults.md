# 0022. TRIM through LUKS, a smaller locate index, and lighter compression on build directories

Checked against the reference machine's reported layout on 2026-11-08. Issue #145. This apply has not been run on the machine; the owner runs `./install.sh` or `update-all`. A package check is not that proof.

## Problem

The root filesystem is LUKS, `/dev/mapper/root`, with btrfs on top. The device-mapper discard granularity is 0B, so TRIM stops at the crypt device, and `fstrim.timer` is disabled. `plocate-updatedb.timer` runs daily and indexes the whole home directory, including every worktree's `node_modules`. The filesystem is mounted `compress=zstd:3`. Build directories do not need that level for the files they create.

zram, swappiness, page-cluster, the CPU governor, and snapper are already fine. This decision does not change them.

## Decision

- **TRIM through LUKS uses the LUKS2 header, not Limine or crypttab.** This repo does not manage the boot chain. Limine and the crypttab that opens `root` stay in the `omarchy-mac` and `omarchy-mac-boot` packages. `cryptsetup refresh --allow-discards --persistent root` stores allow-discards in the open LUKS2 header and turns it on for the running mapping. A crypttab `discard` option would be a second copy of boot configuration this repo does not own. Discard reveals which encrypted blocks are free. That tradeoff is accepted so TRIM can run. The refresh runs only when `findmnt` shows `/` on `/dev/mapper/root` (a btrfs subvolume suffix is the same mapping), `lsblk` reports type `crypt`, and the discard granularity is `0B`.
- **`fstrim.timer` is enabled** with `systemctl enable --now fstrim.timer` after that refresh, so the first trim sees discards. The wait is bounded at 15 minutes. A timeout is a failed apply, not a slow success.
- **plocate keeps the distribution prunes.** `/etc/updatedb.conf` is a package file and is not edited. `config/plocate-updatedb.service.d.conf` is the only override. It replaces the service `ExecStart` with `/usr/bin/updatedb` plus `--add-prunenames` and `--add-prunepaths`. Those flags add to the package file; `--prunenames` and `--prunepaths` would replace it. The added names are `node_modules`, `.turbo`, and `.next`. The added paths are the setup account's `~/.t3/worktrees` and `~/.local/share/pnpm`. The committed file has `@HOME@`; apply writes the absolute home. The tree stays free of account names (decision 0006). The drop-in lists only those two settings, each with a one-line reason. Package `Nice` and IO scheduling stay in the vendor unit.
- **New files in three directories use `zstd:1` when the home directory is on btrfs.** `btrfs property set <dir> compression zstd:1` on `~/.t3/worktrees`, `~/code`, and `~/.cache`. A missing directory is created first, so the property is in place before new files. The property applies to files created after it is set. The mount option `compress=zstd:3` is left as it is. A symlink at one of those paths is left alone. A home directory that is not btrfs is left unchanged, and that does not fail setup.
- **A plocate drop-in systemd has not loaded is installed again.** `systemctl daemon-reload` runs again when the file is already right but systemd does not list it.
- **`./install.sh` and `update-all` apply this** through `scripts/groundwork-storage.py`. A second run does not repeat a step that is already set. An update asks for administrator permission only when a privileged step is still pending. `groundwork-doctor` checks all three and changes nothing.

## Not done

- No live apply on the reference machine in this change. Doctor output from the machine, after the owner runs the apply, is the record that discards, the timer, the drop-in, and the three directory properties are in place.
- No change if plocate's vendor unit starts passing its own `updatedb` arguments. Recheck the drop-in then. The package file would still supply `PRUNEFS` and `PRUNENAMES` through `--add-*`.
- No change to zram, swappiness, page-cluster, the CPU governor, or snapper.
