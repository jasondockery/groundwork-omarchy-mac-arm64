# 0020. T3 Code through Omarchy's installer, not the feed packages

Checked 2026-10-31 on the M1 mini (16 KiB pages, linux 7.2.11, `omarchy-dev 4.0.0.r7442.gbda3aaf-1`). This decision supersedes the T3 parts of issues #13 and #29: the official bun-compiled `t3` CLI from `https://t3.codes/install.sh`, its pinned known-good build, the `update-all` candidate check and smoke run for that CLI, and its repair path. Those stay in force, as described in the [README](../../README.md) and [docs/apps/t3-code.md](../apps/t3-code.md), until the implementation in issue #110 merges. That implementation follows #102.

## What there is

T3 Code ships as two separate things:

- **The desktop app.** This is an Electron AppImage. Omarchy installs it with `omarchy-install-ai-t3-code [stable|nightly] [--no-launch]` (Install > AI > T3 Code), through the hidden `omarchy-install-t3code-app`. That writes `~/.local/share/t3code/T3-Code.AppImage`, `~/.local/share/applications/t3code.desktop` (`Exec`, `TryExec`, `MimeType=x-scheme-handler/t3code;x-scheme-handler/t3code-dev;`) and the hicolor icons. It also sets the Omarchy theme, links `~/.local/bin/t3` to the app's own launcher `~/.t3/bin/t3` unless the user has a `t3` of their own there, and drops `t3code-bin` and `t3code-nightly-bin`. The app updates itself with electron-updater. It stages the next build in `~/.cache/t3code-updater/pending/` (the AppImage plus `update-info.json` with its sha512) and swaps it in when the app quits. Omarchy's menu counts T3 Code as installed when the AppImage file exists.
- **The standalone `t3` CLI.** `t3.codes/install.sh` installs only this: a bun-compiled ELF under `~/.t3/runtime/versions/<version>/t3`, linked from `~/.local/bin/t3`. It is the only artifact that pingdotgg/t3code#23433 and #102 are about. Its last LOAD (bun's payload) has `p_align 0x1000`.

On the reference mini today, the desktop app is Omarchy's AppImage layout, at 0.1.6-nightly.20261030.3917, with 3956 staged. It updated itself past the CLI gate and runs on 16 KiB pages. The `t3` on `PATH` is this layer's bun CLI, held at 0.1.2-nightly.20261021.3457. It shadows the app's own `t3`.

The pkgs.omarchy.org `edge/aarch64` feed also builds `t3code-bin 0.1.5-1` and `t3code-nightly-bin 0.1.6_nightly.20261031.3956-1`, repacked from the same AppImages (`makedepends 7zip`, no install script). Omarchy treats them as the old path. `omarchy-install-ai-t3-code` drops them. `omarchy-remove-ai-t3-code` says "Installs from before the AppImage came from these packages". Migration `1792345511` ("Replace the T3 Code package with the T3 Code app, which updates itself") runs the installer on any machine that still has one.

## Decision

- **Owner.** T3 Code is installed with `omarchy-install-ai-t3-code nightly --no-launch`. This layer does not run `omarchy pkg add t3code-nightly-bin`, and it stops running `t3.codes/install.sh` and `t3 update`. The bun CLI, `T3_KNOWN_GOOD_LARGE_PAGE`, and the CLI candidate check are removed. Kernels with 4 KiB pages take the same path and skip the gate.
- **The `t3` command** is the app's bundled CLI: `~/.t3/bin/t3`, which runs the AppImage's Electron as Node on `resources/app.asar/apps/server/dist/bin.mjs`, linked from `~/.local/bin/t3` as Omarchy's installer links it. The app writes that launcher on its first launch. Until then, setup reports "open T3 Code once" instead of failing its `t3 --version` check.
- **Desktop entry and `t3code://`.** Omarchy's installer owns `t3code.desktop`. The app still self-registers `com.t3tools.T3Code.desktop` and points `x-scheme-handler/t3code` at it (#82). `scripts/groundwork-t3-launcher.py reconcile` stays: it points the handler back at `t3code.desktop`, removes the duplicate, and changes nothing while T3 Code runs.
- **The 16 KiB gate** moves from the CLI download to the desktop app. It builds on the LOAD segment rule in [decision 0018](0018-t3-load-segment-rule.md) (#102, PR #111): a LOAD passes with `p_align >= page`, or with `p_offset` and `p_vaddr` both page multiples and a passing smoke run. Two changes apply to both rules:
  - Only ELF files built for this machine (`e_machine` EM_AARCH64) are checked. Every build checked here carries `node-pty/prebuilds/linux-x64/pty.node`, an x86-64 file with `p_align 0x1000` that is never loaded on aarch64. Without this filter, every build fails both rules.
  - The smoke run is the updater's `t3_sandbox_command` (bubblewrap, read-only `/usr` and `/etc`, no home, no network, scrubbed environment, core limit 1, 30 seconds) on the extracted `t3code` with `--version`. It must print the version under test. The AppImage is extracted for this without running the app.
- **Before an update.** `update-all` checks a staged update in `~/.cache/t3code-updater/pending/`. It verifies the file's sha512 against `update-info.json`, then runs the scan and the smoke. A build that passes stays staged, and the app applies it when it quits. A build that fails is removed from the pending directory together with `update-info.json`, and its version is written to `t3-rejected.json` as today. A timeout or a transport error is a warning and is not remembered.
- **After an update.** `update-all` runs the same scan and smoke on the installed `T3-Code.AppImage`. It keeps one copy of the last AppImage that passed, under `~/.local/state/groundwork/t3/`. When the installed build fails and T3 Code is not running, that copy is restored. With no copy, the updater downloads the newest version that is not rejected, using the release's `nightly-linux-arm64.yml` and its sha512 (the feed Omarchy's installer reads), and checks it before moving it into place. While T3 Code runs, nothing is replaced. The warning says to quit it and rerun `update-all`.
- **Hold.** There is no pacman entry to pin, so `IgnorePkg` does not apply. A rejected version is held by removing it from the pending directory on every run. The app may download it again, and the next run removes it again. If the app quits between that download and the next `update-all`, the rejected build is applied, and the after-update check restores the last good copy. A newer build clears the hold, as `t3-rejected.json` does today.
- **Migration** (decision [0014](0014-offer-migration-to-omarchy-installers.md), id `t3-cli`). Offered when `~/.local/bin/t3` is a symlink into `~/.t3/runtime/versions/`. It is refused, with nothing changed, while any T3 Code process runs. Otherwise:
  - When the AppImage is missing, it runs `omarchy-install-ai-t3-code nightly --no-launch` and then the after-update check.
  - When the AppImage exists, it does not rerun Omarchy's installer, because that would download an unchecked newest build over a working one. It only replaces the link with `~/.local/bin/t3 -> ~/.t3/bin/t3`, the link Omarchy's installer makes.
  - It keeps `~/.t3` (threads, settings, theme, and the old CLI builds under `runtime/versions`), `~/.config/t3code`, `~/.config/t3code-v2`, `~/.config/t3code-flags.conf` and `~/.cache/t3code-updater`. The notice names `~/.t3/runtime/versions` as safe to delete afterwards. Undo means pointing the link back at a build in that directory.
  - Updates only print the notice. A new install never gets the bun CLI.

## Why not `t3code-nightly-bin`

- The Omarchy-first rule in `AGENTS.md` picks Omarchy's installer over a package whenever Omarchy has one, and Omarchy has one here.
- Omarchy is moving away from these packages. Its installer, its remover, and migration `1792345511` all drop or replace them. A package installed by this layer would be removed the next time someone used the Install menu.
- `omarchy update` runs pacman outside `update-all`. A rejected nightly would be installed there before any Groundwork check. Holding it means `IgnorePkg` in `/etc/pacman.conf`, a file Omarchy owns.
- The nightly package names its commands `t3-nightly` and `t3code-nightly`. Only the stable package ships `t3`.
- What the package would add: the pacman cache as a rollback, and a version pacman can hold. Both are recorded here so the recheck can weigh them again if Omarchy reverses its direction.

## Evidence

All four packages were downloaded from `https://pkgs.omarchy.org/edge/aarch64/` into a temporary directory and not installed. Each matched the `%SHA256SUM%` in `omarchy.db` and has a good signature from `Omarchy <pkgs@omarchy.org>` (`40DFB630FF42BCFFB047046CF0134EE680CAC571`). The database itself has no `.sig` (404). Every ELF was read with `readelf -lW`. The smoke used a read-only copy of `scripts/groundwork-update.py`, called with its own `t3_sandbox_command`, `t3_smoke_environment`, `run(..., limit_core=True)` and 30-second deadline.

| Package | Electron | aarch64 ELFs, strict | aarch64 ELFs, relaxed | All 23 ELFs, either rule | `t3code --version` (smoke) | CLI `bin.mjs --version` |
|---|---|---|---|---|---|---|
| `t3code-bin 0.1.5-1` | 44.10.0 | 22/22 pass | 22/22 pass | fail: `linux-x64/pty.node` only | `0.1.5` | `t3 v0.1.5` |
| `t3code-nightly-bin 0.1.2_nightly.20261021.3499-1` | 44.9.3 | 22/22 pass | 22/22 pass | fail: `linux-x64/pty.node` only | `0.1.2-nightly.20261021.3499` | matches |
| `t3code-nightly-bin 0.1.6_nightly.20261030.3940-1` | 44.10.0 | 22/22 pass | 22/22 pass | fail: `linux-x64/pty.node` only | `0.1.6-nightly.20261030.3940` | matches |
| `t3code-nightly-bin 0.1.6_nightly.20261031.3956-1` | 44.10.0 | 22/22 pass | 22/22 pass | fail: `linux-x64/pty.node` only | `0.1.6-nightly.20261031.3956` | matches |

- Every aarch64 ELF has `p_align 0x10000` on every LOAD. That includes `t3code`, `chrome-sandbox` (mode 4755), `chrome_crashpad_handler`, the bundled libraries, and the node addons and helpers under `resources/`. `t3code` in 3956 has LOADs at offset `0x0`, `0x3320000`, `0xcb61800` and `0xd180000`, all `0x10000`, the same layout as the running 3917 AppImage.
- `linux-x64/pty.node` is `Machine: Advanced Micro Devices X86-64` with four LOADs at `p_align 0x1000`, offsets `0x0 0x4000 0xd000 0xebe0`. It fails the relaxed rule too.
- Paths: `/usr/lib/t3code{,-nightly}/` (Electron app), `/usr/bin/t3code{,-nightly}` (wrapper, adds `--ozone-platform=wayland` on Wayland, reads `~/.config/t3code{,-nightly}-flags.conf`), `/usr/bin/t3` or `/usr/bin/t3-nightly` (`ELECTRON_RUN_AS_NODE=1` CLI wrapper), `/usr/share/applications/t3code{,-nightly}.desktop` (`Exec=t3code{,-nightly} %U`, `MimeType=x-scheme-handler/t3code;x-scheme-handler/t3code-dev;`), and hicolor icons. `resources/app-update.yml` is present, with `channel: nightly` in the nightly package.
- Provides and conflicts: `t3code-bin` provides and conflicts `t3code`. `t3code-nightly-bin` provides and conflicts `t3code-nightly`. Neither has a `.INSTALL` script. pacman's own hooks (desktop database, icon cache) are the only post-install steps.
- The `--version` smoke does not open a window. Evidence that the GUI starts on 16 KiB pages comes from the running 0.1.6-nightly.20261030.3917 AppImage, which uses Electron 44.10.0 and the same LOAD layout.

## Relation to decision 0018 and #102

#102 and [decision 0018](0018-t3-load-segment-rule.md) relax the alignment check for the bun CLI. That fix holds until #110's implementation lands, and this decision applies the same rule to the desktop app. After that, T3 no longer uses the CLI check. `load_alignments` stays, because Blip's bun preflight uses it.

## Recheck

By 2026-11-30, or sooner when `omarchy-install-ai-t3-code` or `omarchy-install-t3code-app` changes, when the feed stops building the T3 packages, or when an Electron build fails the gate.
