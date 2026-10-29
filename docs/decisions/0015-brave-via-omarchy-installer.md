# 0015. Brave through Omarchy's browser installer

Checked 2026-10-29. The shared choice of Brave, the policy rows, and the one offered extension live in groundwork-core tag `v0.7.0` (`0395895c86ed24d942c3803c521cf2131cfa52c7`), in `browser/brave.json` and [`docs/browser.md`](https://github.com/jasondockery/groundwork-core/blob/v0.7.0/docs/browser.md). This repo pins that tag as the `core` submodule. It does not keep a second copy of the spec. This decision owns only the Apple Silicon Omarchy install.

Brave release is the browser this layer offers. Brave Origin (`omarchy-install-browser brave-origin`) is a different browser and is not installed. Chromium stays; it is part of the Omarchy base, and this layer never removes it. There is no Flatpak of Brave: Brave's Linux page says the Flatpak is not yet working as well as the native package, and this layer does not add one when `brave-bin` fails.

## Install

When `brave` is missing and `omarchy-install-browser` with no arguments prints a usage line that contains `|brave|`, setup runs `omarchy-install-browser brave`. That is Omarchy's installer for the AUR package `brave-bin`. When that command is absent, or its usage line has no `brave`, nothing is installed and nothing is offered.

A `brave` already on `PATH` that `pacman -Qoq` does not report as `brave-bin`, and the user Flatpak `com.brave.Browser`, are not replaced by a silent install. They are offers, below.

## Checks

After a `brave-bin` command is on `PATH`, setup checks it before offering it as the default:

- Every ELF file `pacman -Qlq brave-bin` lists is checked with `readelf -lW`, the same LOAD-alignment check as Bitwarden and local inference. A package with no ELF file fails this check. The reference mini uses 16 KiB pages. The known crash is [brave/brave-browser#42312](https://github.com/brave/brave-browser/issues/42312). This layer does not pass `--js-flags=--nodecommit_pooled_pages`.
- A short launch runs the `brave` on `PATH` with a private `HOME`, `TMPDIR`, and `XDG_*`, `PATH=/usr/bin:/bin`, and `--headless=new --disable-gpu --no-first-run --no-default-browser-check --disable-extensions --dump-dom about:blank`. The wait is 30 seconds. Stdout and stderr are discarded. The user's Brave profile is not the one this launch opens.

A failed alignment, a missing ELF, or a launch that dies by signal (exit above 128, or a negative code) is remembered in `~/.local/state/groundwork/brave-rejected.json`, keyed by page size, the same way as `bitwarden-rejected.json`. The version is the installed `pacman -Q brave-bin` version, or else the one `pacman -Si brave-bin` offers. A version already in that file is not installed or checked again, including by the Flatpak or foreign-command migration. That offer is not shown while the version `pacman -Si` would install is still rejected. A copy this run added is removed with `omarchy-remove-browser brave`. A copy that was already installed is left in place. Chromium stays the default, and setup prints one line saying why.

A launch timeout, or any other non-zero exit that is not a signal, is not remembered and is not removed. Setup prints the line, leaves Chromium as the default, and tries that version again on the next run. A failed `omarchy-install-browser brave` is also not remembered. If that command returns success but `brave-bin` does not own the `brave` on `PATH`, the copy this run added is removed and the version is not remembered.

A version that passes both checks is recorded in `brave-accepted.json` for that page size. A later `brave-bin` version is checked again. Passing once is not a pass for the next build.

## Default and migration

Offers use the helper from [decision 0014](0014-offer-migration-to-omarchy-installers.md). One flag, `./install.sh --migrate-to-omarchy-installers`, still runs every offer that is pending.

- `default-browser-brave`. When Brave passed both checks and `omarchy-default-browser` does not print `brave`, setup offers to run `omarchy-default-browser brave`. `./install.sh` in a terminal asks `[y/N]`. A yes is the only answer that changes the default. Updates and runs without a terminal print one line and do not ask. A no is saved in `declined_migrations`.
- `brave-flatpak`. A user Flatpak `com.brave.Browser`, or a `brave` command `brave-bin` does not own, is reported in one line. Migration runs `omarchy-install-browser brave` and the checks above. It does not uninstall the Flatpak, does not pass `--delete-data`, and leaves `~/.var/app/com.brave.Browser` in place. Import with `brave://settings/importData`.

`omarchy-remove-browser brave` removes the package this run added. It does not delete `~/.config/BraveSoftware`. This layer never removes Chromium.

After a yes, web apps opened with `omarchy-launch-webapp` follow the default browser, so they open in Brave. Sign-ins that exist only in Chromium's profile stay there.

A routine update installs a missing Brave only through the same sudo gate as other privileged changes. Without a terminal and without cached sudo, the update stops before installing it. This layer does not upgrade Brave; `omarchy update` does.

## Policies and Bitwarden

When `brave-bin` owns the `brave` on `PATH`, setup writes `core/browser/brave.json` to `/etc/brave/policies/managed/groundwork.json`. The file is the policy names and values only. Reasons stay in the spec. Directories are `0755` and the file is `0644`, both `root:root`, via `sudo install`. An unchanged file is not written again. `omarchy.json` and `color.json` in that directory are left alone. A symlink anywhere on that path is refused. Root ownership is what survives Omarchy's `browser_policy_purge_dir`, which deletes non-root files in the managed directory.

The Bitwarden extension id and update URL are read from that same spec. Setup still registers only that extension, `nngceckbapebfimnlniiiahkandclblb`, at `/usr/share/chromium/extensions`, as an external manifest. It does not add `ExtensionInstallForcelist`. Brave is not Google-branded; if it ignores that Chromium directory, the machine report says so. The fix is not a forced install.

## Not accepted yet

The fixtures do not launch a real Brave and do not prove 16 KiB pages. On the reference mini, still to check: Brave starts and browses; `omarchy-default-browser` prints `brave` after a yes; `brave://policy` shows the v0.7.0 rows; Bitwarden is offered in Brave; a web app opened with `omarchy-launch-webapp` opens in Brave. Until those are done, `docs/status.md` does not say Brave is installed.
