# groundwork-omarchy-mac-arm64

Personal settings for Omarchy on Apple Silicon. The desktop comes from [omacom/omarchy](https://github.com/omacom/omarchy) (`quattro`), packaged as `omarchy-dev` from `pkgs.omarchy.org/edge`. Apple Silicon support is the `omarchy-mac` and `omarchy-mac-boot` packages from [omacom/omarchy-mac-pkgs](https://github.com/omacom/omarchy-mac-pkgs). [omacom/omarchy-mac](https://github.com/omacom/omarchy-mac) is the legacy fork, and its `quattro` branch is not what the installer uses; shared Mac hardware support has lived in [omacom/omarchy](https://github.com/omacom/omarchy) since [omacom/omarchy#14431](https://github.com/omacom/omarchy/pull/14431). The upstream [omarchy-mac-installer](https://github.com/omacom/omarchy-mac-installer) installs the OS. This repo only keeps personal settings that Omarchy does not provide.

The learning curriculum stays in [Groundwork](https://jasondockery.github.io/groundwork/) for now. Use [this repo's shortcut guide](docs/keybindings.md) for the Omarchy keys when an older lesson uses Groundwork-specific bindings. The `core/` submodule is the read-only groundwork-core checkout this layer reads, including the Brave spec. `./install.sh` and `update-all` run `git submodule update --init -- core` so that checkout matches the pinned gitlink. `scripts/apply-layer.sh` runs that checkout when the Brave spec is not in the worktree yet, so an `update-all` installed before this revision can still apply it. Do not copy the curriculum, or `core/browser/brave.json`, into this repo. [groundwork-mac](https://github.com/jasondockery/groundwork-mac) provisions a personal Mac development environment (and Linux/WSL/headless environments); it is not the Omarchy OS installer and must not be applied here.

It is for any Apple Silicon Mac running Omarchy: Mac mini, MacBook Air, and MacBook Pro, M1 and later, aarch64. Machine notes live in `docs/machines/`. Nothing in `packages/` or `install.sh` is specific to one model.

`groundwork-mac` stays the setup for Mac, WSL, and containers. Do not `chezmoi apply` it here. This repo does not stow `core/`.

## First use on a working Omarchy desktop

Start here when Omarchy is already installed and the desktop works. These steps apply this repo's settings; they do not reinstall the OS or change its update channel. The longer OS reinstall sequence later in this README is for a deliberate reinstall only.

In an Omarchy terminal, clone this repo if you do not already have it, then inspect and run the installer:

```bash
git clone https://github.com/jasondockery/groundwork-omarchy-mac-arm64.git ~/code/groundwork-omarchy-mac-arm64
cd ~/code/groundwork-omarchy-mac-arm64
omarchy pkg add stow
less install.sh
./install.sh
```

For an existing checkout, use `git status` and review local edits before updating it; do not clone over it or reset it. Public HTTPS clones need no GitHub login. For a private copy, use an authorized account or your chosen SSH host alias/key instead. The installer does not configure GitHub credentials. `./install.sh` checks out the pinned `core` submodule; a separate recursive clone is unnecessary.

The default installs T3 Code Nightly (through Omarchy's own installer), Herdr, Tailscale, keyd, Obsidian, Bitwarden (desktop and CLI), and FreeTube, starts `tailscaled` and keyd, applies the trackpad preferences, and makes Caps Lock Escape on tap and Control while held. Different existing input/keyd files are backed up. `--without-keyd` skips keyd setup. Omarchy continues to own the desktop shortcuts, tmux, shell, editor, and terminal configuration. T3 Code and agent providers still need your local authentication. Follow the [desktop apps guide](docs/apps.md) for Bitwarden sign-in, the local Obsidian vault, previous plugin choices, and free cross-platform sync. Existing notes are never moved automatically.

WayVNC screen sharing is also configured automatically. It starts with the logged-in desktop and listens only on the current online Tailscale address. Show its generated password locally with `groundwork-screen-share password`, then connect from the Mac's Screen Sharing app using `vnc://MACHINE`. On that virtual keyboard, Command follows Omarchy's Super bindings and Option follows the Alt bindings. Follow [desktop-sharing setup](docs/remote-access.md#automatic-desktop-sharing) for the port grant, modifier check, acceptance, and recovery. `--without-screen-sharing` skips it without disabling an existing service. Tailscale sign-in and OS login remain local steps.

Blip, an iMessage client, stays off unless you pass `--with-blip`. It installs `nixfred.blip` from git through Omarchy's plugin manager and does not run the bridge wizard. A separate Mac on macOS is the gateway. `--without-blip` leaves an existing Blip install unchanged. Follow [iMessage setup](docs/imessage.md).

Local inference stays off unless you pass `--with-local-inference`. That installs Omarchy's `ollama` and `ollama-vulkan` packages and downloads no model. A package install is not a GPU result. Follow [local inference](docs/local-inference.md) and run `scripts/verify-local-inference.py` on the Mac. `--without-local-inference` records the preference as off.

The commit identity guard from groundwork-core is on by default: user-level `commit-msg` and `pre-push` hooks that call the pinned script, Claude Code attribution set to empty where you have not set it, and an empty `~/.config/groundwork/commit-denylist` for names you keep local. The hooks switch on only in repositories with a remote under your own GitHub account (`includeIf`, git 2.36+), so work repositories are untouched; it writes nothing when you already set `core.hooksPath`, and each repository's own hooks keep running. Check it with `groundwork-commit-guard doctor`; turn it off with `groundwork-commit-guard off` (see [decision 0017](docs/decisions/0017-commit-identity-guard.md)).

git-filter-repo stays off unless you pass `--with-git-filter-repo`. It installs per user with mise, needs no root, and is proven by `git filter-repo -h` listing `--sensitive-data-removal`. `--without-git-filter-repo` records a no. The procedure is the shared [history purge runbook](https://github.com/jasondockery/groundwork-core/blob/v0.8.1/docs/history-purge.md); this layer's install, proof, and removal are in [git-filter-repo](docs/git-filter-repo.md).

Finish [Tailscale setup](docs/remote-access.md), including the tailnet policy review, interactive sign-in, and device approval. The local commands are:

```bash
sudo tailscale up --timeout=5m
sudo tailscale set --ssh
tailscale status
```

Only enable SSH after reviewing that guide and your effective tailnet policy. No reusable auth keys, account names, or machine addresses belong in this repo.

### First-run acceptance

- `hyprctl configerrors` prints no errors. The installer reloads and checks a detected Hyprland session; from SSH or a console, finish that check on the desktop with `hyprctl reload` and `hyprctl configerrors`.
- Trackpad traditional scrolling, tapping, two-finger right-click, and three-finger drag behave as expected. Cursor speed on a large display and browser page scrolling should feel usable; tune `input.sensitivity` and `input.touchpad.scroll_factor` with `hyprctl reload` if not.
- Tap Caps Lock to leave Neovim insert mode; hold Caps Lock with `L` to send `Ctrl+L`, and with Space to send the multiplexer prefix. Physical Escape and Control still work.
- `systemctl is-active tailscaled keyd` reports both services active for the default setup. `tailscale status` shows the device connected after sign-in and approval. Use the separate keyd recovery instructions below if needed.
- `Super+Space` opens the Omarchy menu; `Super+K` shows desktop bindings. Launch tmux with `Super+Alt+Return` and Herdr with `Super+Ctrl+Return`. In either, use `Ctrl+Space`, then `?` for live help, and practice the [same pane keys](docs/keybindings.md).
- Open T3 Code from the app launcher (`Super+Space`, T3 Code). The first launch writes its `t3` command, `~/.t3/bin/t3`, which `~/.local/bin/t3` points at. Then `t3 --version` works in a new terminal. Authenticate a provider already installed on this machine and complete one small task in a disposable project. Installing the app does not authenticate Codex or Claude. `xdg-open t3code://` opens T3 Code, and the launcher shows one T3 Code entry.
- Open Obsidian with `Super+Shift+O` and select the local vault printed by setup. A new vault has Vim mode enabled; existing settings are preserved. Verify your notes and the chosen sync path using the [apps guide](docs/apps.md).
- Launch Bitwarden from the app menu and finish sign-in/MFA. Restart Chromium to load the registered official extension, approve/enable it if prompted in each profile, and test autofill. `bw --version` checks CLI availability; it does not prove vault sign-in. A new install uses Omarchy's `bitwarden` package when the feed has it and it passes setup's checks, and otherwise the user Flatpak. User Flatpak updates use `flatpak --user update` separately from Omarchy updates.
- When setup installed Brave and its launch check passed, setup made it the default (it opens a Brave window with a throwaway profile for a few seconds to check), and `omarchy default browser` prints `brave`. Open one page, then `brave://policy`, and confirm the groundwork-core rows. Approve Bitwarden if Brave offers it. Open one web app with `omarchy-launch-webapp` and confirm it opens in Brave; a sign-in that exists only in Chromium stays in Chromium. Chromium itself stays installed. If Brave failed the page-size or launch check, Chromium remains the default and setup says so. See the [apps guide](docs/apps.md#brave).
- Launch FreeTube from the app menu. It opens with upstream defaults. Subscriptions and history stay in `~/.var/app/io.freetubeapp.FreeTube/config/FreeTube`. See the [apps guide](docs/apps.md).
- From another approved computer in the same tailnet, run `ssh LOCAL_USER@MACHINE` using this machine's local login and MagicDNS name. Confirm the hostname and attach to tmux or Herdr. Test the reverse direction too using the destination's supported SSH setup.
- From a Mac, connect to `vnc://MACHINE` with the generated local VNC password. Verify the screen and input, including after logout/login or reboot. Command+1 selects a workspace, and Option+1 selects a multiplexer window. Tailscale visibility alone does not prove a desktop server is running or permitted by policy.
- If you passed `--with-blip`, finish on the desktop with the printed `blip-setup` command and the checks in [iMessage setup](docs/imessage.md). Send the first test message only to your own number.
- If you passed `--with-local-inference`, the packages are installed and the GPU is still unproven. Run the three commands in [local inference](docs/local-inference.md) on this Mac and keep the report they write.

If a step fails, keep its error and fix it before treating the setup as accepted. The automated fixtures test installer behavior; they cannot prove your physical input devices, desktop, accounts, or a second machine's connection.

## Updating this layer

After setup, run this from any directory for routine maintenance:

```bash
update-all
```

It checks this repo, runs the supported `omarchy update`, fast-forwards and
applies your personal settings, updates listed Omarchy shell plugins, checks
T3 Code's builds (below), and updates the installed Bitwarden and FreeTube user Flatpaks. `omarchy update`
does not update those plugin checkouts. When screen
sharing's unit, helper, or WayVNC config changed and `WAYLAND_DISPLAY` is unset (SSH or cron),
an inactive unit stays stopped and the restart record is cleared. When `WAYLAND_DISPLAY` is set,
the unit restarts even if it is inactive. The drop warning prints only when a restart actually runs.
See [desktop sharing](docs/remote-access.md#automatic-desktop-sharing). Other Flatpaks and
application sign-in remain outside this command. Omarchy owns the OS, Arch
packages, default tools, and desktop; no second system updater is installed.

T3 Code is Omarchy's self-updating AppImage
([decision 0020](docs/decisions/0020-t3-code-through-omarchy-installer.md)). Setup installs it with
`omarchy-install-ai-t3-code nightly --no-launch`, which needs administrator permission once (it adds
`fuse3`). The app downloads its next build into `~/.cache/t3code-updater/pending/` and applies it when
it quits. `update-all` runs no T3 update command.

On a kernel with pages larger than 4 KiB (Apple Silicon uses 16 KiB), `update-all` checks T3 Code's
builds ([pingdotgg/t3code#23433](https://github.com/pingdotgg/t3code/issues/23433)):

- **The staged update.** It verifies the staged AppImage against the sha512 in
  `update-info.json`, then runs the check below. A build that passes stays staged. A build that fails
  is removed from the pending directory (that file and `update-info.json`, nothing else) and is
  remembered in `~/.local/state/groundwork/t3-rejected.json` with its reason. When the app stages a
  rejected version again, the next run removes it again without checking it. A newer build clears this.
- **The installed AppImage.** It runs the same check on `~/.local/share/t3code/T3-Code.AppImage` and
  keeps one copy of the last build that passed under `~/.local/state/groundwork/t3/` (about the size
  of the AppImage). When the installed build fails and T3 Code is not running, that copy is put back.
  With no copy, it downloads the newest nightly that is not rejected, using the release's
  `nightly-linux-arm64.yml` and its sha512, checks it, and puts it in place. While T3 Code runs,
  nothing is replaced, and the warning says to quit it and rerun `update-all`.

The check copies the AppImage into a temporary directory under `~/.cache/groundwork` (on disk, removed afterwards) and unpacks it there with
`--appimage-extract`, inside `bwrap --die-with-parent --unshare-pid --unshare-net` with system
directories read-only, the temporary directory writable, no home directory, and a fresh environment
(`HOME`, `TMPDIR`, and `XDG_*` inside it, `PATH=/usr/bin:/bin`, core limit 1). The app is not started.
Every ELF file built for aarch64 is read with `readelf -lW` (from `binutils`, which setup installs).
Files built for another machine, such as node-pty's `prebuilds/linux-x64/pty.node`, are skipped,
because they never load here. A LOAD passes when its `p_align` is at least the page size, or when its
`p_offset` and `p_vaddr` are both page multiples
([decision 0018](docs/decisions/0018-t3-load-segment-rule.md)). Then the unpacked `t3code --version`
runs in the same sandbox for at most 30 seconds and must print the version under test. Omarchy ships
`bwrap` with Flatpak. A missing `bwrap` skips the check with a warning and runs nothing. A misaligned
LOAD or a signal death is remembered. A timeout, a transport error, or a wrong version warns, is not
remembered, and is checked again on the next `update-all`, as is a SIGKILL (usually low memory). Warnings finish the other stages, record
`completed_with_warnings`, and exit non-zero.

Setup checks the installed AppImage too, whenever it does not match the kept copy, including one
that a failed `omarchy-install-ai-t3-code` left in place. Setup restores a kept copy but does not
download an older build; it says to run `update-all`, which does.

One race remains. The app can apply a staged build when it quits before `update-all` has checked it.
The next `update-all` then finds the installed build failing and restores the last good copy.

On 4 KiB pages there is no check. The app updates itself.

When Blip is opted in, `update-all` runs `omarchy plugin update nixfred.blip`
only in an interactive terminal, without `--yes`, so you can read the diff.
If that diff touches `bridge/`, it reminds you to re-run `blip-setup` and does
not run the wizard. `groundwork-update` does not update the plugin.

Last, `update-all` runs three report-only checks from the checkout. They run after
the update has finished and recorded its result, each with a short time limit
(60, 30, and 15 seconds). A check that fails or runs out of time prints one line
and is skipped; it never changes the exit status. Each prints only what it finds:

- `scripts/omarchy-feed-watch.py report --brief`: apps Groundwork installs itself
  that Omarchy now provides, one line per offer you have not dismissed.
- `scripts/groundwork-path-shadow.py --brief`: managed commands whose first `PATH`
  hit is another copy, or whose owner could not be asked, with the shell file
  that puts it first.
- `scripts/groundwork-disk-report.py --brief`: free space on `/` under, or within
  5 GiB of, the 10 GiB Omarchy needs to update.

When none of them finds anything, `update-all` prints one line saying so. Run a
script without `--brief` for the full report. Set `GROUNDWORK_SKIP_REPORTS=1` to
skip all three. `groundwork-update` and `update-all --check` do not run them.

For only repository configuration, run `groundwork-update`. It preserves saved
keyd, screen-sharing, Blip, and Obsidian vault choices and does not request routine
system or app upgrades. It installs and retires listed shell plugins and leaves
existing plugin checkouts at their current revisions. It can install a newly declared missing tool needed by
the settings. Use `./install.sh` with explicit `--with-keyd`, `--without-keyd`,
`--with-screen-sharing`, `--without-screen-sharing`, `--with-blip`,
`--without-blip`, `--with-local-inference`, `--without-local-inference`,
`--with-git-filter-repo`, or `--without-git-filter-repo`
when changing those preferences. Skipping an existing feature
does not disable its service or remove Blip.

When an app this layer manages was installed another way than Omarchy's own
installer, and Omarchy has one, setup offers to move it and never forces it
([decision 0014](docs/decisions/0014-offer-migration-to-omarchy-installers.md)).
That covers a Bitwarden user Flatpak once the feed has the `bitwarden`
package, a Brave Flatpak or other non-`brave-bin` Brave, and making a Brave
that passed setup's checks the default browser
([decision 0015](docs/decisions/0015-brave-via-omarchy-installer.md)).

Setup also pins `node = "26"` in `~/.config/mise/config.toml` when no mise
config of yours sets Node, so Omarchy's `node = "latest"` does not move to the
next major on its own, and installs it. An existing Node entry is kept. Agents
that Claude, Codex or Grok start through mise's command wrappers still see the
system Node first; see
[decision 0016](docs/decisions/0016-pin-node-for-mise.md) for the cause and
the workaround.

`./install.sh` in a terminal saves the other setup choices first,
then asks `[y/N]` for up to five minutes and saves a no with them.
`groundwork-update`, `update-all`, and runs without a terminal print one line
naming the current and preferred methods and the migration id. Run
`./install.sh --migrate-to-omarchy-installers` to migrate without a prompt,
including after an earlier no. The flag runs every offer that is still pending.
App data is kept; see the
[apps guide](docs/apps.md#moving-from-the-flatpak-to-omarchys-package)
[Brave](docs/apps.md#brave), and [T3 Code](docs/apps.md#t3-code).
A `cursor` that no pacman package owns is only reported, with
[manual steps](docs/apps.md#cursor).

With no Bitwarden desktop installed and the package in the feed, a routine
update now needs sudo to add it. Without a terminal or cached sudo, that
update stops with the command to run, where it used to install the Flatpak.

Routine updates ask for your password only when a system change is needed, for
example a stopped `tailscaled` or keyd service, a changed keyd config, or a
missing package. UFW cannot list its rules without root, so after setup adds the
screen-sharing rule it records it in
`~/.local/state/groundwork/ufw-screen-share-rule` and updates trust that record.
An existing install asks for sudo once on its next update to create it. If you
remove the UFW rule by hand, run `./install.sh` to add it again.

To inspect incoming repository changes first:

```bash
groundwork-update --check
```

The check fetches without advancing your checkout or changing the configuration
linked into the desktop. `update-all --check` performs the same repository check;
it does not inventory system or app updates. A normal update displays the
fetched changes before advancing. Local edits, conflicting managed input files,
detached or diverged branches, and simultaneous Groundwork updates stop with
recovery guidance. There is no automatic stash, reset, or Core branch-tip update.
Failures keep previous success receipts and report completed stages; repair the
named problem and rerun the same command, even if the source already advanced.
Receipts live under `$XDG_STATE_HOME/groundwork` (default
`~/.local/state/groundwork`). Configuration-only success never marks full
maintenance successful.

Existing installations need one transition: fetch and review this repo while
the checkout is clean, fast-forward to the reviewed commit, then run
`./install.sh` once to install both commands. Repeat any previous `--without-*`
choices on that first run because older installers did not store them. Later
runs remember choices in `$XDG_CONFIG_HOME/groundwork/omarchy.json` (default
`~/.config/groundwork/omarchy.json`). Keep the checkout at that saved path.
The updater preserves its chosen Git remote and authentication method; a public
HTTPS clone needs no GitHub login.

Setup remains `./install.sh`. It installs the commands, applies preferences, and
prints first-run acceptance instructions. Routine updates reuse the apply code
and omit onboarding. They preserve application data and provider authentication.

## Repository checks

Installation checks that the selected tools are actually available. Once T3 Code has written its `t3` command, if another `t3` shadows `~/.local/bin/t3`, setup stops before applying input settings and prints the directory to prepend to your user PATH. Start a fresh shell and rerun after fixing PATH; existing npm/system installations are preserved. `~/.local/bin/t3 --version` must then print a version. A `t3` of your own at `~/.local/bin/t3` is left alone, as Omarchy's installer leaves it. Package-feed skips also fail explicitly instead of claiming a missing tool was installed.

Run `bash -n install.sh scripts/apply-layer.sh scripts/migrate-from-groundwork.sh` and `python3 -B -m unittest discover -s tests -v`. CI runs the same shell and isolated installer checks. They do not install packages, start host services, or change the developer's home directory. The shared Renovate preset is declared in `renovate.json`; the central runner must also grant this repo access before updates can arrive.

## Where each step runs

The install crosses the places in this table. Each step below names the one place it runs. A command from a later step will fail in an earlier place: `launchctl` exists only in macOS, and `omarchy` exists only in Omarchy.

| Place | You are there when | Steps that run there |
|---|---|---|
| Omarchy, current install | The Omarchy desktop is up. The shell is Linux. `launchctl` is not a command. | Backup, then shut down and pick macOS from startup options |
| macOS | The macOS desktop is up. Commands go in Terminal.app. The installer is the app you open from the DMG. | Old MX helper cleanup, download, checksum, installer app |
| recoveryOS | The installer app has rebooted the Mac into Apple's recovery screen | The boot-policy prompt the app asks for. Use the macOS password if Apple asks. |
| Omarchy, new install | The installer has booted the new Omarchy desktop | `omarchy-channel-current`, `omarchy update`, then `./install.sh` |
| Another computer | A Mac, Linux, or WSL machine that is not this Omarchy Mac | `groundwork-mac` bootstrap only |

The chosen track is the newest [Omarchy Mac Installer](https://github.com/omacom/omarchy-mac-installer) prerelease plus its signed **edge** catalog. Apple Silicon has only an edge channel: `omarchy-channel-set` refuses `stable` on `aarch64-apple`, and the installer's stable and rc catalogs have no Mac release. The installer app runs in macOS and refuses an existing Omarchy; Remove Omarchy first, which wipes the Linux install. [PR #46](https://github.com/omacom/omarchy-mac-installer/pull/46) (reinstall over an existing install) is still open. `./install.sh` in this repo runs later, on the new Omarchy desktop. `groundwork-mac` runs on another computer. After first boot, check the channel with `omarchy-channel-current`; do not run `omarchy-channel-set edge`.

## Run on: Omarchy, current install

Back up this Linux home before you reboot. Copy what you cannot re-clone, including SSH keys and other account credentials, onto storage outside the internal SSD. Open the backup while this desktop is still up.

macOS APFS and RecoveryOS stay on the SSD beside the LUKS-encrypted Omarchy btrfs root. This Aurora install boots m1n1-aurora → U-Boot → Limine and is not an `asahi-root` volume. To boot macOS, shut the Mac down. Hold the power button until startup options appear, then choose the macOS disk. That hardware screen runs before either desktop. `asahi-bless` is present on current installer images, but the one-shot next-boot path was not checked on this machine, so do not treat it as the documented command here.

## Run on: the powered-off Mac

This is the standard route to macOS. Shut the Mac down. Hold the power button until startup options appear, then choose the macOS disk. This is the hardware startup screen, before either desktop is running.

## Run on: macOS

You are in macOS after that reboot. The cleanup and the download happen in Terminal.app. The installer itself is a graphical app.

Remove the old helper only when **Omarchy MX Mac Installer** (`com.omarchy.mx`) is installed on this macOS volume. Open that old app, finish or clean up any interrupted install or removal, and quit it. The new app leaves the old helper in place. In Terminal.app, once no operation is in progress:

```bash
sudo launchctl bootout system/com.omarchy.mx.installer.helper
sudo rm -f /Library/LaunchDaemons/com.omarchy.mx.installer.helper.plist /Library/PrivilegedHelperTools/com.omarchy.mx.installer.helper
sudo rm -rf /var/db/com.omarchy.mx.installer /var/db/com.omarchy.mx.installer.aside
sudo rm -rf "/Applications/Omarchy MX Mac Installer.app"
rm -rf ~/Library/Application\ Support/com.omarchy.mx.installer* ~/Library/Caches/com.omarchy.mx.installer
defaults delete com.omarchy.mx.installer 2>/dev/null
```

The Application Support directory can contain the image that preview downloaded. Copy it elsewhere before removing it if you still need it. Skip the whole block when that preview app is not installed.

Still in macOS, download the newest installer prerelease from the upstream [installer releases](https://github.com/omacom/omarchy-mac-installer/releases). In Terminal.app, run `shasum -a 256` on the downloaded DMG and match it to the checksum published on that release. Open the DMG and launch the app. In the app, choose the signed **edge** catalog and confirm it enables this Mac (`apple,j274` for the reference M1 Mac mini). If Omarchy is already installed, the app refuses to continue until you Remove Omarchy, which wipes that Linux install. Read the partition plan in the app before you confirm it. New images ship `linux-aurora` (Limine), not `linux-asahi` or GRUB.

The signed catalog picks the image. A later install uses the catalog's current selection, not a dated pin.

The reference M1 Mac mini is `apple,j274`. It migrated on 2026-10-17 with installer **2.2.0** and image `omarchy-mac-edge-2026.10.14-79f8e36b977f`. Those are what this machine installed, not a pin for later installs. The catalog's latest observed requirements and selected image are recorded in [status](docs/status.md). These are pre-release artifacts.

- Installer **2.2.0**, used on the mini: [installer release](https://github.com/omacom/omarchy-mac-installer/releases/tag/v2.2.0). DMG SHA-256: `749f94d729ae8f65629f7b0a51beab9c519af983d203a60134fd6562c979447b`.
- Signed rolling edge catalog: [catalog-edge](https://github.com/omacom/omarchy-mac-installer/releases/tag/catalog-edge).
- Image the mini installed: [10.14 image](https://github.com/omacom/omarchy-mac-installer/releases/tag/omarchy-mac-edge-2026.10.14-79f8e36b977f). Image SHA-256: `2e7ef7e14e81ca62d6761c7a70db96d9d2e86ff07b6166530b5164c260406584`.

## Run on: recoveryOS

Stay here only when the installer app reboots the Mac into Apple's recovery screen. Follow the prompt on that screen. Enter the macOS password if Apple asks for the boot-policy step. When that screen finishes, the Mac continues into the new Omarchy install. This screen has no Omarchy shell and no `./install.sh`.

## Run on: Omarchy, new install

You are here when the new desktop is up. Check Wi-Fi, the keyboard, and the trackpad. Check that **Super+Space** opens the Omarchy menu and that **Super+K** works. On this installer path the Qt 6.12 palette fix is [omarchy#14553](https://github.com/omacom/omarchy/pull/14553) in `quattro`. [omarchy-mac#707](https://github.com/omacom/omarchy-mac/issues/707) and [PR #710](https://github.com/omacom/omarchy-mac/pull/710) cover only the omarchy-mac `quattro` path. Keep the backup until those shortcuts and your files are usable.

Confirm the update channel is `edge`. Apple Silicon has no other channel. In an Omarchy terminal:

```bash
omarchy-channel-current
omarchy update
```

`omarchy-channel-current` should print `edge`. Do not run `omarchy-channel-set`. Reboot if `omarchy update` asks. Later system updates stay on this desktop and use `omarchy update`, which applies the `aarch64-apple` edge pacman config (`[omarchy]` at `https://pkgs.omarchy.org/edge/$arch`, then `[asahi-alarm]`, then Arch Linux ARM). A bare `pacman -Syu` skips that policy.

Personal settings come from this repo, in an Omarchy terminal, after the base system is up. `./install.sh` checks out the pinned `core` submodule, so a separate recursive clone is unnecessary:

```bash
git clone https://github.com/jasondockery/groundwork-omarchy-mac-arm64.git ~/code/groundwork-omarchy-mac-arm64
cd ~/code/groundwork-omarchy-mac-arm64
omarchy pkg add stow
./install.sh
```

`./install.sh` links the trackpad override, reloads and checks a detected Hyprland session, installs Herdr, Tailscale, keyd, Obsidian, Bitwarden, and FreeTube, enables the Tailscale and keyd services, and installs T3 Code Nightly with `omarchy-install-ai-t3-code`. It seeds Vim mode only for an Obsidian vault without app settings. Caps Lock sends Escape when tapped and Control while held by default, matching the Karabiner habit on macOS. Reboot or log out if it asks, then complete the first-run acceptance above. Use `--without-keyd` to skip keyboard remapping.

Still in an Omarchy terminal, complete the [private remote-access setup](docs/remote-access.md). This repo stores neither tailnet credentials nor your GitHub SSH key.

Open T3 Code from the app launcher; after its first launch, `t3` also starts it from a terminal. Sign in to at least one provider, such as Codex or Claude, in T3 Code before starting an agent. Herdr opens with Omarchy's `Super+Ctrl+Return` shortcut and from a terminal.

The installer is a one-time OS install. Ongoing fixes come from `omarchy update` on the edge channel. Before a later reinstall, read the upstream installer releases again. The signed edge catalog may select a newer image, and an existing install still has to be removed first.

This README records what the reference M1 Mac mini installed on 2026-10-17; the latest observed catalog selection is in [status](docs/status.md). File desktop, shell, binding, and shared-helper bugs on [omacom/omarchy](https://github.com/omacom/omarchy). File Apple Silicon package bugs, including the Mac's hardware services and boot chain, on [omacom/omarchy-mac-pkgs](https://github.com/omacom/omarchy-mac-pkgs). File package recipes, signing, and publication on [omacom/omarchy-pkgs](https://github.com/omacom/omarchy-pkgs). File installer or image bugs on [omacom/omarchy-mac-installer](https://github.com/omacom/omarchy-mac-installer). Send a hardware qualification report to [omarchy-m-testing.org](https://omarchy-m-testing.org). [omacom/omarchy-mac](https://github.com/omacom/omarchy-mac) is the legacy fork and is not where new package or hardware bugs go; reports already filed there stay there.

Notes for people and agents are in `AGENTS.md`, `docs/status.md`, and `docs/decisions/`.

## Run on: another computer (Mac, Linux, or WSL)

This section is for a machine that is not the Omarchy Mac. Choose [groundwork-mac](https://github.com/jasondockery/groundwork-mac) for macOS, [groundwork-linux](https://github.com/jasondockery/groundwork-linux) for non-Omarchy Linux, or [groundwork-windows](https://github.com/jasondockery/groundwork-windows) inside WSL2. Clone the selected entry point over HTTPS and follow its README.

If original Groundwork is already installed there, use the [shared migration guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/migrating-from-groundwork.md) before applying anything. Preserve its stored answers, checkout, remote, and recovery path. The original repo retains the learning curriculum.

`scripts/migrate-from-groundwork.sh` prints guidance without applying configuration. It refuses `--clone-mac` on Omarchy. On another computer, that option only prepares a separate `groundwork-mac` checkout; it does not run its bootstrap. Linux and WSL2 users should start with their own entry points, and existing users should preview the engine as described in the migration guide.

Omarchy keeps its own desktop, tmux file, shell, and Super keybindings. Practice its tmux chords here: `Ctrl+Space` is the prefix, `Ctrl+B` is the second prefix, `Alt+Enter` splits below, `Alt+Shift+Enter` splits beside, `Alt+Escape` closes a pane, `Ctrl+Alt+Arrow` moves between panes, and `Alt+Left` / `Alt+Right` move between windows. Omarchy's close keys may act immediately; inspect running work first. Inside tmux, word jumps stay on `Alt+B` and `Alt+F`. `Super+Space`, `Super+K`, and the other Super shortcuts are Omarchy desktop keys. The [shortcut guide](docs/keybindings.md) follows the installed defaults; cross-platform parity work is separate.

## Included

| Path | Why it is not already in Omarchy |
|---|---|
| `packages/hypr/.config/hypr/input.lua` | Trackpad choices that differ from Omarchy's defaults: tap-to-click, three-finger drag, faster pointer, slower page scrolling |
| `keyd/default.conf` | Default Caps Lock behavior: tap = Escape, hold = Control, via keyd |
| `packages.txt` | Optional aarch64 packages that are not part of the Omarchy install |
| `git/config.local.example` | Placeholder for a name and email. Not applied |
| `scripts/groundwork-disk-report.py` | Report-only sizes and cleanup commands for the yay cache, journal, and Flatpak installations. Omarchy already trims the pacman cache and ships Disktree ([decision 0019](docs/decisions/0019-disk-cleanup-report-only.md)). `--brief`, which `update-all` runs, only warns about low free space |
| `scripts/omarchy-plugins.py` | Applies the shared Omarchy shell-plugin list through `omarchy plugin` |
| `omarchy-plugins.exclude.json` | Plugin ids this layer skips, each with a reason |
| `scripts/omarchy-feed-watch.py` | Reports apps this layer installs itself that Omarchy now provides, and offers the move (`report`, `--json`, `--brief`, `dismiss <app>`). Read-only and offline unless `--online` is passed. `update-all` runs `report --brief` |
| `omarchy-feed-watch.json` | The apps that report checks, each with Groundwork's method, Omarchy's candidates, and the dated evidence for keeping the self-install |
| `scripts/groundwork-syncthing.py` | Syncthing as a user service on the tailnet, the `~/Shared` and FreeTube folders, and `groundwork syncthing-pair` |
| `scripts/groundwork-path-shadow.py` | Read-only report of PATH entries that shadow a managed command (pacman or Omarchy package, mise, Groundwork-installed `t3`), with the shell file that sets the winning entry. Run it from the checkout; `--json`, `--brief`, and `--strict` are available. `update-all` runs `--brief`, and setup's `t3` check prints its report when another `t3` wins. It never deletes or edits anything. |

## Syncthing shared folders

Setup installs pacman `syncthing`, enables lingering on a machine with no system battery (a Mac mini, iMac or Studio, the always-on peer; a MacBook starts it at login), and runs `systemctl --user enable --now syncthing.service` once Tailscale has an address. Syncthing listens only on localhost and the tailnet, with relays, discovery and NAT mapping off, and its GUI stays on `127.0.0.1:8384`. The shared folder is `~/Shared`; pairing is one command on each machine:

```bash
groundwork syncthing-pair show
groundwork syncthing-pair add --device-id DEVICE_ID --name NAME --magicdns MAGICDNS
```

If Tailscale is not signed in yet, setup says Syncthing is waiting; rerun `groundwork-update` after `sudo tailscale up`. Until then `groundwork-update` and `update-all` finish with a warning and exit 1. The contract is in [groundwork-core](https://github.com/jasondockery/groundwork-core/blob/main/docs/syncthing.md); the choices here and what is not done yet are in `docs/decisions/0013-syncthing-shared-folders.md`.

## Learn Omarchy's defaults

This repo deliberately leaves Omarchy's desktop navigation, tmux, terminal, editor, and shell defaults under Omarchy's control. It does not install the Groundwork Mac tmux config; on Omarchy, learn the built-in `Ctrl+Space` prefix (`Ctrl+B` also works). The intentional differences are the trackpad preferences and Caps Lock tap-Escape/hold-Control mapping. T3 Code, Herdr, and Tailscale are installed as tools.

Useful starting points from the [Omarchy hotkey guide](https://github.com/omacom/omarchy/blob/quattro/manual/07-hotkeys.md) and [terminal guide](https://github.com/omacom/omarchy/blob/quattro/manual/15-terminal.md):

- `Super+Space` opens the Omarchy menu; `Super+K` shows desktop shortcuts; `Super+Alt+K` shows tmux shortcuts; `Super+Ctrl+K` shows Herdr shortcuts.
- `Super+Alt+Return` opens tmux. Use `Ctrl+Space` then `s` to list sessions. `Alt+Enter` and `Alt+Shift+Enter` split panes without a prefix; `Ctrl+Alt+Arrow` moves between panes.
- `tdl cx` opens an editor, Claude Code, and a terminal; `tdl c cx` adds OpenCode too. `tds` starts an editor/diff/terminal/agent layout, while `tdlm` and `tsl` cover multi-project and multi-agent layouts.
- The Apple Silicon edge image includes tmux, Neovim, Herdr, and the Omarchy edge desktop packages. Herdr is the agent-aware multiplexer: `Super+Ctrl+Return` opens it. Try it alongside tmux before deciding which fits your work.

These are Omarchy's documented defaults and intended workflow, not a claim that every AI developer uses the same setup. Omarchy's first-party manuals are the clearest current reference for the conventions the project ships.

## Shell plugins

Shared default shell plugins live in [groundwork-core `omarchy/plugins.json`](https://github.com/jasondockery/groundwork-core/blob/main/omarchy/plugins.json). This repo applies that list and does not name plugin ids. Setup and both update commands run `scripts/omarchy-plugins.py` once, after the Hyprland reload. `GROUNDWORK_PLUGIN_LIST` may point at a local file or an https URL. Otherwise the helper downloads `https://raw.githubusercontent.com/jasondockery/groundwork-core/main/omarchy/plugins.json` over https, rejects a non-https redirect or a redirect to any host other than the starting host, `raw.githubusercontent.com`, `objects.githubusercontent.com`, or `github.com`, and bounds each read by the time left on its deadline. The last valid copy is kept in `$XDG_STATE_HOME/groundwork/omarchy-plugins.json` (default `~/.local/state/groundwork/omarchy-plugins.json`). A missing list, a rejected list, or a network or HTTP failure uses that copy. With no list and no saved copy, apply prints one line and continues.

An entry is skipped when its `arch` does not include `uname -m`, or when its id is listed in `omarchy-plugins.exclude.json` or `~/.config/groundwork/omarchy-plugins.exclude.json`. Each exclusion has an id and a reason. A plugin directory that already exists under `~/.config/omarchy/plugins/<id>` stays as it is, including a plugin you disabled. A plugin you removed stays uninstalled. A built-in plugin (`builtIn` in schema version 2) is enabled once and recorded; one already on keeps its place, and one you turn off later stays off. An entry's `replaces` ids are disabled once after this layer enables the replacement. Retiring that replacement, dropping the id from its `replaces`, removing the entry from the list, excluding it, or an `arch` that skips it turns the replaced ids this layer turned off back on if they are still off and no other listed replacement that is already on takes them over (a widget you had already turned off stays off), in the bar section and position they held before, as read from `~/.config/omarchy/shell.json` when this layer disabled them (Omarchy resets a widget's own settings across that off and on). Retired ids are removed only when this layer recorded the install. `update-all` updates listed checkouts that are already installed. `groundwork-update` leaves those revisions unchanged. New plugins are enabled while `omarchy-shell` is running; otherwise that install waits for the next run. Bar sections are `left`, `centerBegin`, `center`, `centerEnd`, `tray`, and `right`, as `omarchy plugin enable` accepts them. Any other section skips that entry, and the rest of the list still applies. A setup command runs from a terminal after install, again after `update-all` moves that checkout, and again when an earlier run left it pending. A setup that needs sudo runs as the current user after sudo is primed. `~/.config/omarchy/shell.json` stays unstowed.

If `~/.config/hypr/mouse.lua` or `~/.config/hypr/omacontrol.lua` sets `touchpad.natural_scroll` differently from the stowed `input.lua`, the file Hyprland loads later wins. If `hyprland.lua` does not load the differing file, apply says so and the stowed `input.lua` still applies. See `docs/decisions/0010-omarchy-plugins-from-shared-list.md`.

## Left out on purpose

Omarchy already ships these, or owning them would freeze a file that `omarchy update` is supposed to change. Account material stays on the machine.

- Stock Hyprland files: `bindings.lua`, `autostart.lua`, `monitors.lua`, `looknfeel.lua`, `hyprland.lua`
- `~/.config/git/config` aliases, pull rebase, and diff defaults
- Bash aliases for `eza`, `bat`, `starship`, `zoxide`, and `fzf`
- `~/.config/tmux/tmux.conf` (Omarchy's built-in config stays active)
- zsh as the login shell, and Linuxbrew
- Neovim, Ghostty, Karabiner, and macOS defaults
- `~/.config/omarchy/shell.json` and plugin checkouts under `~/.config/omarchy/plugins/`
- SSH config, public keys, and private keys
- Git remotes, `insteadOf` rules, and account emails
- Wi-Fi names and Tailscale addresses

## Install

Run this on the Omarchy desktop, after the operating system is installed. It requires Omarchy on aarch64. It is the same `./install.sh` as the "Omarchy, new install" step above.

```bash
omarchy pkg add stow
./install.sh
```

That links the trackpad override, installs Herdr, Tailscale, keyd, Obsidian, Bitwarden, and FreeTube, enables `tailscaled` and `keyd`, and installs the T3 Code app through Omarchy's installer. Pass `--with-blip` only when you want the iMessage client; the default leaves it off. Pass `--with-local-inference` only when you want the Ollama packages; the default leaves them off and downloads no model. See [local inference](docs/local-inference.md). If `~/.config/hypr/input.lua` already exists, the script moves it to `input.lua.bak-omarchy-groundwork`, preserving numbered predecessors if necessary, and then links the copy from this repo. It does not configure Git/SSH accounts. Complete the [Tailscale setup](docs/remote-access.md) and [desktop apps setup](docs/apps.md) after install.

### Caps Lock: Escape on tap, Control while held

This is an opinionated default for everyone using this repo. [keyd](https://github.com/rvaiya/keyd#quickstart) remaps keyboard events before they reach the desktop or terminal, so the same behavior works in tmux, Herdr, T3 Code, Neovim, and other apps. It is [available for aarch64](https://archlinuxarm.org/packages/aarch64/keyd) and is the solution used in the [Omarchy community's tap/hold discussion](https://github.com/omacom/omarchy/discussions/1383).

The installer applies `keyd/default.conf` to `/etc/keyd/default.conf`, enables the service, and reloads it even when it was already running. A different existing config is kept as a numbered backup, such as `/etc/keyd/default.conf.~1~`. An unchanged rerun does not create another backup. The `[ids]` wildcard applies to built-in and external keyboards; see the [keyd decision](docs/decisions/0003-caps-lock-via-keyd.md) if a mouse also exposes keyboard events.

Test the result after installation:

- Tap Caps Lock in Neovim insert mode: it returns to normal mode.
- Hold Caps Lock and press `L` in a shell: it behaves like `Ctrl+L`.
- Hold Caps Lock and press Space in tmux or Herdr, release, then press `?`: it opens prefix help.

Stock Omarchy changed on [2026-10-11](https://github.com/omacom/omarchy/pull/14939) to normal Caps Lock with Shift+Caps Lock for Compose. This mapping replaces Caps Lock before XKB sees it, so neither normal Caps Lock nor its Compose combination remains on that physical key. Physical Escape and Control keep working. The [keyd decision](docs/decisions/0003-caps-lock-via-keyd.md) explains how to move Compose to Right Alt if needed.

To install the other settings without changing keyd:

```bash
./install.sh --without-keyd
```

This skips setup; it does not disable an existing keyd service or restore an old config. The former `--with-keyd` flag still works but is unnecessary. To stop this mapping on a machine where keyd was installed solely for this repo:

```bash
sudo systemctl disable --now keyd
```

If you already used keyd for other mappings, restore your numbered config backup and run `sudo keyd reload` instead of disabling the service.

## Packages

`packages.txt` lists optional aarch64 packages. Add one with `omarchy pkg add`. `dust` is not listed: Omarchy dropped it (migration `1780739888.sh`), so remove a copy you installed yourself with `omarchy pkg drop dust`. To see what is using disk, run `python3 scripts/groundwork-disk-report.py`, which only reports, and open Disktree, which Omarchy installs. Remote-desktop choices are explained in `docs/decisions/0004-remote-access.md`.

## Accounts

Git identity, SSH keys, and work remotes stay on each machine. See `docs/decisions/0006-no-personal-accounts-in-the-repo.md`.

## License

[MIT](LICENSE), matching the Groundwork family.

## Common developer setup and macOS containers

The family uses one developer tool set across personal and managed machines, with independent identity and optional-app preferences. See the [shared setup guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/developer-setup.md) for tool and platform ownership.

For a macOS machine, Groundwork recommends Colima plus the Docker CLI. The single [Docker Desktop to Colima guide](https://github.com/jasondockery/groundwork-mac/blob/main/docs/docker-desktop-to-colima.md) covers migration scenarios, data transfer, verification, rollback, and Docker Desktop uninstallation after acceptance. This recommendation applies to macOS; this repository’s Linux, WSL, or Omarchy environment keeps its native container path.

## Coding agents and desktop apps

Setup preserves existing Codex, Claude Code, Grok Build, and OpenCode commands. A missing provider is installed through Omarchy’s declared mise tool, with a five-minute deadline; setup verifies that the command becomes available and stops on failure. Omarchy continues to own provider versions, updates, shortcuts, and default-agent selection. It also owns multiple subscriptions (Omarchy 4.5 and later; if the command is missing, update Omarchy first): add a second Claude or Codex account with `omarchy agent account add claude Work` (or `codex`), switch with `omarchy agent account use work`, and list accounts with `omarchy agent account list`. This layer adds no account aliases or separate config folders. Run `omarchy update` through its supported workflow before setup to get current upstream declarations. When `cursor` is missing, setup runs Omarchy’s `omarchy-install-cursor` on older Omarchy Mac builds, which may open Cursor once. Current Omarchy has no such command, so setup adds the `cursor-bin` package that Omarchy’s Install menu uses, without launching it. An existing `cursor` command is not replaced and is not launched again. If `~/.config/Cursor/argv.json` has no `password-store`, setup adds `gnome-libsecret` and preserves every other key; a password store that is already set is left as written. No migration is offered for an existing Cursor: on current Omarchy the `cursor-bin` package is Omarchy's own method. A `cursor` that no package owns gets a one-line notice with manual steps in the [apps guide](docs/apps.md#cursor). Sign-in stays local.

ChatGPT and Claude have no official native Linux desktop app in this setup. Use their web apps on Omarchy; this repo does not install unofficial replacements. Tailscale remains a required native package and service, with interactive tailnet enrollment. Authentication and live desktop acceptance are separate from package fixtures.
