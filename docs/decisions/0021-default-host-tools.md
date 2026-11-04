# 0021. Default host tools, and a doctor that names the fix

Checked 2026-11-03 on the M1 mini (Omarchy edge, 16 KiB pages). Issue #130.

## Problem

Scripts and test harnesses assume a few host binaries that macOS and Ubuntu CI runners have and Arch does not install. On the mini, `lsof` was missing, and a project's test suite that fails closed when it cannot read port ownership went red. `shellcheck` was missing too, although `packages.txt` lists it. Nothing in this layer installed either one or said so.

## What the evidence shows

- **Why `shellcheck` was never installed.** `packages.txt` is a reference list; no script installs from it (decision 0019 says the same for `dust`). Neither `./install.sh` nor `update-all` reads it.
- **Why listing it would not have helped.** `pacman -Si shellcheck` finds nothing in any repository configured here (`core`, `extra`, `alarm`, `asahi-alarm`, `aur`, `omarchy`). Arch Linux ARM does not build the Haskell package for aarch64, and the Omarchy feed does not carry it. `omarchy pkg add shellcheck`, and the `sudo pacman -S shellcheck` the issue proposed, both fail on this machine.
- **The route that works.** The AUR's `shellcheck-bin` (0.11.0-1, `arch=(x86_64 armv6h aarch64)`) repackages upstream's static release with a pinned sha512. Its `shellcheck-v0.11.0.linux.aarch64.tar.xz` was downloaded to a temporary directory, not installed: the sha512 matched the PKGBUILD, both LOAD segments of the static binary are aligned to `0x10000` (64 KiB, so 16 KiB pages map it), and `shellcheck --version` and a lint of a sample script ran.
- **`lsof`** is in Arch `extra` (4.99.7-1, aarch64), so `omarchy pkg add lsof` works.
- `ps` (procps-ng 4.0.7, which supports `-o lstart=`), `rg`, `jq`, `ssh` and `git` ship with Omarchy.

Omarchy first (AGENTS.md): Omarchy ships neither tool, has no `omarchy-install-*` for them, and its feed has neither package. Its own `omarchy pkg add` and `omarchy pkg aur add` helpers are the install path.

## Decision

- **A small default set, not `packages.txt`.** `scripts/apply-layer.sh` installs missing host tools on every setup and `update-all`: `install_package lsof` (the same helper as `binutils`), and `omarchy pkg aur add shellcheck-bin` when no `shellcheck` is on `PATH`. An existing `shellcheck` from any source is kept, since `shellcheck-bin` conflicts with other packages that provide it. `privileged_changes_needed` lists `lsof`, so an update asks for sudo when it is missing. It does not list `shellcheck`: that install is tried only when `sudo -n true` already works (setup asks up front; an update has asked for another change), so a missing `shellcheck` alone never prompts for a password or fails an update without a terminal. Otherwise it prints the install command and continues. `packages.txt` stays optional and drops `shellcheck`.
- **A missing `lsof` stops setup; a failed `shellcheck` does not.** `lsof` comes from the official repositories, as `binutils` does. `shellcheck-bin` depends on the AUR being reachable and on yay building it, so a failure prints the command to retry and setup continues; the doctor keeps reporting it. `omarchy-feed-watch.json` watches for a repository `shellcheck` package and reports the move when one appears.
- **`groundwork-doctor`.** `scripts/groundwork-doctor.py` checks `lsof`, `ps` with `-o lstart=` (it runs `ps -o lstart= -p` on itself), `rg`, `jq`, `shellcheck`, `ssh` and `git`. Each missing tool is printed with its install command: `sudo pacman -S --needed <package>` with the Arch package name (`lsof`, `procps-ng`, `ripgrep`, `jq`, `openssh`, `git`), and `omarchy pkg aur add shellcheck-bin` for `shellcheck`. It exits 0 when all are present and 1 otherwise, and it changes nothing. Setup installs it as `~/.local/bin/groundwork-doctor`, a launcher for the checkout's script with the updater's marker; a command of that name from another setup is kept, with one line saying so. This repository had no doctor before; the commit guard's `doctor` subcommand is a separate check of that guard.
- **`update-all` runs `groundwork-doctor --brief`** after the update, next to the feed-watch, PATH-shadow and disk reports. It prints one line per problem and nothing when all pass, and like them it cannot fail the update; `GROUNDWORK_SKIP_REPORTS=1` skips it.

## Not done

- groundwork-linux should get the same list. groundwork-windows has no `lsof` and would map this job to `Get-NetTCPConnection` or `netstat -ano`. groundwork-mac needs nothing for `lsof`, which macOS ships. Those are follow-ups in their own repositories.
- No check of `shellcheck-bin` LOAD alignment at install time. The static release was checked by hand for 0.11.0. Recheck on 2026-12-03, or when `shellcheck-bin` moves to a new upstream version or Arch Linux ARM or the Omarchy feed starts building `shellcheck` (then move to `omarchy pkg add shellcheck`).
