# groundwork-omarchy-mac-arm64

Personal settings for Omarchy on Apple Silicon. [omacom/omarchy-mac](https://github.com/omacom/omarchy-mac) owns Omarchy's desktop defaults and Apple Silicon support. The upstream [omarchy-mac-installer](https://github.com/omacom/omarchy-mac-installer) installs the Omarchy operating system. This repo only keeps personal settings that Omarchy does not provide.

The learning curriculum stays in [Groundwork](https://jasondockery.github.io/groundwork/) for now. Use [this repo's shortcut guide](docs/keybindings.md) for the Omarchy keys when an older lesson uses Groundwork-specific bindings. The `core/` submodule remains a read-only reference from the initial family split; it is not needed to install this layer. Do not copy the curriculum into this repo. [groundwork-mac](https://github.com/jasondockery/groundwork-mac) provisions a personal Mac development environment (and Linux/WSL/headless environments); it is not the Omarchy OS installer and must not be applied here.

It is for any Apple Silicon Mac running Omarchy Mac: Mac mini, MacBook Air, and MacBook Pro, M1 and later, aarch64. Machine notes live in `docs/machines/`. Nothing in `packages/` or `install.sh` is specific to one model.

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

For an existing checkout, use `git status` and review local edits before updating it; do not clone over it or reset it. Public HTTPS clones need no GitHub login. For a private copy, use an authorized account or your chosen SSH host alias/key instead. The installer does not configure GitHub credentials. A recursive submodule clone is unnecessary for setup.

The default installs T3 Code Nightly, Herdr, Tailscale, keyd, Obsidian, and Bitwarden (desktop and CLI), starts `tailscaled` and keyd, applies the trackpad preferences, and makes Caps Lock Escape on tap and Control while held. Different existing input/keyd files are backed up. `--without-keyd` skips keyd setup. Omarchy continues to own the desktop shortcuts, tmux, shell, editor, and terminal configuration. T3 Code and agent providers still need your local authentication. Follow the [desktop apps guide](docs/apps.md) for Bitwarden sign-in, the local Obsidian vault, previous plugin choices, and free cross-platform sync. Existing notes are never moved automatically.

WayVNC screen sharing is also configured automatically. It starts with the logged-in desktop and listens only on the current online Tailscale address. Show its generated password locally with `groundwork-screen-share password`, then connect from the Mac's Screen Sharing app using `vnc://MACHINE`. Follow [desktop-sharing setup](docs/remote-access.md#automatic-desktop-sharing) for the port grant, acceptance, and recovery. `--without-screen-sharing` skips it without disabling an existing service. Tailscale sign-in and OS login remain local steps.

Finish [Tailscale setup](docs/remote-access.md), including the tailnet policy review, interactive sign-in, and device approval. The local commands are:

```bash
sudo tailscale up --timeout=5m
sudo tailscale set --ssh
tailscale status
```

Only enable SSH after reviewing that guide and your effective tailnet policy. No reusable auth keys, account names, or machine addresses belong in this repo.

### First-run acceptance

- `hyprctl configerrors` prints no errors. The installer reloads and checks a detected Hyprland session; from SSH or a console, finish that check on the desktop with `hyprctl reload` and `hyprctl configerrors`.
- Trackpad scrolling, tapping, two-finger right-click, and three-finger drag behave as expected.
- Tap Caps Lock to leave Neovim insert mode; hold Caps Lock with `L` to send `Ctrl+L`, and with Space to send the multiplexer prefix. Physical Escape and Control still work.
- `systemctl is-active tailscaled keyd` reports both services active for the default setup. `tailscale status` shows the device connected after sign-in and approval. Use the separate keyd recovery instructions below if needed.
- `Super+Space` opens the Omarchy menu; `Super+K` shows desktop bindings. Launch tmux with `Super+Alt+Return` and Herdr with `Super+Ctrl+Return`. In either, use `Ctrl+Space`, then `?` for live help, and practice the [same pane keys](docs/keybindings.md).
- Check that `t3 --version` identifies a Nightly build, then run `t3`, open its local interface, authenticate a provider already installed on this machine, and complete one small task in a disposable project. Installing the UI does not authenticate Codex or Claude.
- Open Obsidian with `Super+Shift+O` and select the local vault printed by setup. A new vault has Vim mode enabled; existing settings are preserved. Verify your notes and the chosen sync path using the [apps guide](docs/apps.md).
- Launch Bitwarden from the app menu and finish sign-in/MFA. Restart Chromium to load the registered official extension, approve/enable it if prompted in each profile, and test autofill. `bw --version` checks CLI availability; it does not prove vault sign-in. User Flatpak updates use `flatpak --user update` separately from Omarchy updates.
- From another approved computer in the same tailnet, run `ssh LOCAL_USER@MACHINE` using this machine's local login and MagicDNS name. Confirm the hostname and attach to tmux or Herdr. Test the reverse direction too using the destination's supported SSH setup.
- From a Mac, connect to `vnc://MACHINE` with the generated local VNC password. Verify the screen and input, including after logout/login or reboot. Tailscale visibility alone does not prove a desktop server is running or permitted by policy.

If a step fails, keep its error and fix it before treating the setup as accepted. The automated fixtures test installer behavior; they cannot prove your physical input devices, desktop, accounts, or a second machine's connection.

## Updating this layer

Use `omarchy update` for the OS and Arch packages. For this repo, inspect `git status`, pull with `git pull --ff-only` when clean, review the diff, and rerun `./install.sh`. The installer is safe to rerun: it resolves the newest T3 Code Nightly CLI through its official installer, reuses the correct input symlink, and does not create another keyd backup when the config is unchanged. Update the T3 Code Nightly CLI with its own `t3 update` command. Nightly is the selected fast-moving train; unsupported preview artifacts are not selected. The installer reconciles an existing stable CLI as well, while preserving application data and provider authentication.

## Repository checks

Installation checks that the selected tools are actually available. If another `t3` shadows the Nightly symlink, it stops before applying input settings and prints the installed directory to prepend to your user PATH. Start a fresh shell and rerun after fixing PATH; existing npm/system installations are preserved. The effective `t3 --version` must identify Nightly. Package-feed skips also fail explicitly instead of claiming a missing tool was installed.

Run `bash -n install.sh scripts/migrate-from-groundwork.sh` and `python3 -B -m unittest discover -s tests -v`. CI runs the same shell and isolated installer checks. They do not install packages, start host services, or change the developer's home directory. The shared Renovate preset is declared in `renovate.json`; the central runner must also grant this repo access before updates can arrive.

## Where each step runs

The install crosses the places in this table. Each step below names the one place it runs. A command from a later step will fail in an earlier place: `launchctl` exists only in macOS, and `omarchy` exists only in Omarchy.

| Place | You are there when | Steps that run there |
|---|---|---|
| Omarchy, current install | The Omarchy desktop is up. The shell is Linux. `launchctl` is not a command. | Backup, then the one-shot reboot into macOS |
| macOS | The macOS desktop is up. Commands go in Terminal.app. The installer is the app you open from the DMG. | Old MX helper cleanup, download, checksum, installer app |
| recoveryOS | The installer app has rebooted the Mac into Apple's recovery screen | The boot-policy prompt the app asks for. Use the macOS password if Apple asks. |
| Omarchy, new install | The installer has booted the replacement Omarchy desktop | Edge channel, `omarchy update`, then `./install.sh` |
| Another computer | A Mac, Linux, or WSL machine that is not this Omarchy Mac | `groundwork-mac` bootstrap only |

The chosen track is the newest [Omarchy Mac Installer](https://github.com/omacom/omarchy-mac-installer) prerelease plus its signed **edge** catalog. The installer app runs in macOS and replaces this Omarchy installation. `./install.sh` in this repo runs later, on the new Omarchy desktop. `groundwork-mac` runs on another computer. `omarchy-channel-set edge` runs on the new Omarchy desktop after the installer finishes.

## Run on: Omarchy, current install

Back up this Linux home before you reboot. Copy what you cannot re-clone, including SSH keys and other account credentials, onto storage outside the internal SSD. Open the backup while this desktop is still up.

macOS volumes are still on the SSD beside the Omarchy `asahi-root` volume. This command selects macOS for the next boot only, then reboots:

```bash
sudo asahi-bless --next --set-boot-macos
sudo reboot
```

## Run on: the powered-off Mac

Use this only when `asahi-bless` reports that macOS is ambiguous. Shut the Mac down. Hold the power button until startup options appear, then choose the macOS disk. This is the hardware startup screen, before either desktop is running.

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

Still in macOS, download the newest installer prerelease from the upstream [installer releases](https://github.com/omacom/omarchy-mac-installer/releases). In Terminal.app, run `shasum -a 256` on the downloaded DMG and match it to the checksum published on that release. Open the DMG and launch the app. In the app, choose the signed **edge** catalog and confirm it enables this Mac (`apple,j274` for the reference M1 Mac mini). Read the partition plan in the app before you confirm it. The app then replaces the current Omarchy installation.

The signed catalog picks the image. The dated image below is the catalog selection recorded with this README, for checking what you were offered. A later install uses the catalog's newer selection when the catalog has moved.

The reference M1 Mac mini is `apple,j274`. As of 2026-10-17, the upstream edge catalog enables that machine, requires installer **2.2.0**, and selects image `omarchy-mac-edge-2026.10.14-79f8e36b977f`. These are pre-release artifacts.

- Installer **2.2.0** (pre-release): [installer release](https://github.com/omacom/omarchy-mac-installer/releases/tag/v2.2.0). DMG SHA-256: `749f94d729ae8f65629f7b0a51beab9c519af983d203a60134fd6562c979447b`.
- Signed rolling edge catalog: [catalog-edge](https://github.com/omacom/omarchy-mac-installer/releases/tag/catalog-edge).
- Image the catalog selects today: [image release](https://github.com/omacom/omarchy-mac-installer/releases/tag/omarchy-mac-edge-2026.10.14-79f8e36b977f). Image SHA-256: `2e7ef7e14e81ca62d6761c7a70db96d9d2e86ff07b6166530b5164c260406584`.

## Run on: recoveryOS

Stay here only when the installer app reboots the Mac into Apple's recovery screen. Follow the prompt on that screen. Enter the macOS password if Apple asks for the boot-policy step. When that screen finishes, the Mac continues into the new Omarchy install. This screen has no Omarchy shell and no `./install.sh`.

## Run on: Omarchy, new install

You are here when the replacement desktop is up. Check Wi-Fi, the keyboard, and the trackpad. Check that **Super+Space** opens the Omarchy menu and that **Super+K** works. The blank-menu report is [omarchy-mac#707](https://github.com/omacom/omarchy-mac/issues/707). Keep the backup until those shortcuts and your files are usable.

Confirm the update channel is `edge`. In an Omarchy terminal:

```bash
omarchy-channel-set edge
omarchy update
```

Reboot if `omarchy update` asks. Later system updates stay on this desktop and use `omarchy update`, which follows Omarchy Mac's ARM package policy. A bare `pacman -Syu` skips that policy.

Personal settings come from this repo, in an Omarchy terminal, after the base system is up:

```bash
git clone --recurse-submodules https://github.com/jasondockery/groundwork-omarchy-mac-arm64.git
cd groundwork-omarchy-mac-arm64
omarchy pkg add stow
./install.sh
```

`./install.sh` links the trackpad override, reloads and checks a detected Hyprland session, installs Herdr, Tailscale, keyd, Obsidian, and Bitwarden, enables the Tailscale and keyd services, and installs T3 Code Nightly's official ARM64 CLI. It seeds Vim mode only for an Obsidian vault without app settings. Caps Lock sends Escape when tapped and Control while held by default, matching the Karabiner habit on macOS. Reboot or log out if it asks, then complete the first-run acceptance above. Use `--without-keyd` to skip keyboard remapping.

Still in an Omarchy terminal, complete the [private remote-access setup](docs/remote-access.md). This repo stores neither tailnet credentials nor your GitHub SSH key.

Start T3 Code with `t3`. It opens the local web UI. Sign in to at least one provider, such as Codex or Claude, in T3 Code before starting an agent. Herdr opens with Omarchy's `Super+Ctrl+Return` shortcut and from a terminal.

The installer is a one-time OS install. Ongoing fixes come from `omarchy update` on the edge channel. Before a later reinstall, read the upstream installer releases again. The signed edge catalog may select a newer image.

This README records the chosen installer and image. The reference machine stays on its current Omarchy install until that migration has actually happened.

Notes for people and agents are in `AGENTS.md`, `docs/status.md`, and `docs/decisions/`.

## Run on: another computer (Mac, Linux, or WSL)

This section is for a machine that is not the Omarchy Mac. Choose [groundwork-mac](https://github.com/jasondockery/groundwork-mac) for macOS, [groundwork-linux](https://github.com/jasondockery/groundwork-linux) for non-Omarchy Linux, or [groundwork-windows](https://github.com/jasondockery/groundwork-windows) inside WSL2. Clone the selected entry point over HTTPS and follow its README.

If original Groundwork is already installed there, use the [shared migration guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/migrating-from-groundwork.md) before applying anything. Preserve its stored answers, checkout, remote, and recovery path. The original repo retains the learning curriculum.

`scripts/migrate-from-groundwork.sh` prints guidance without applying configuration. It refuses `--clone-mac` on Omarchy. On another computer, that option only prepares a separate `groundwork-mac` checkout; it does not run its bootstrap. Linux and WSL2 users should start with their own entry points, and existing users should preview the engine as described in the migration guide.

Omarchy keeps its own desktop, tmux file, shell, and Super keybindings. Practice its tmux chords here: `Ctrl+Space` is the prefix, `Ctrl+B` is the second prefix, `Alt+Enter` splits below, `Alt+Shift+Enter` splits beside, `Alt+Escape` closes a pane, `Ctrl+Alt+Arrow` moves between panes, and `Alt+Left` / `Alt+Right` move between windows. Omarchy's close keys may act immediately; inspect running work first. Inside tmux, word jumps stay on `Alt+B` and `Alt+F`. `Super+Space`, `Super+K`, and the other Super shortcuts are Omarchy desktop keys. The [shortcut guide](docs/keybindings.md) follows the installed defaults; cross-platform parity work is separate.

## Included

| Path | Why it is not already in Omarchy Mac |
|---|---|
| `packages/hypr/.config/hypr/input.lua` | Trackpad choices that differ from the Mac defaults: traditional scrolling, tap-to-click, three-finger drag |
| `keyd/default.conf` | Default Caps Lock behavior: tap = Escape, hold = Control, via keyd |
| `packages.txt` | Optional aarch64 packages that are not part of the Omarchy Mac install |
| `git/config.local.example` | Placeholder for a name and email. Not applied |

## Learn Omarchy's defaults

This repo deliberately leaves Omarchy's desktop navigation, tmux, terminal, editor, and shell defaults under Omarchy's control. It does not install the Groundwork Mac tmux config; on Omarchy, learn the built-in `Ctrl+Space` prefix (`Ctrl+B` also works). The intentional differences are the trackpad preferences and Caps Lock tap-Escape/hold-Control mapping. T3 Code, Herdr, and Tailscale are installed as tools.

Useful starting points from the [Omarchy Mac hotkey guide](https://github.com/omacom/omarchy-mac/blob/quattro/manual/07-hotkeys.md) and [terminal guide](https://github.com/omacom/omarchy-mac/blob/quattro/manual/15-terminal.md):

- `Super+Space` opens the Omarchy menu; `Super+K` shows desktop shortcuts; `Super+Alt+K` shows tmux shortcuts; `Super+Ctrl+K` shows Herdr shortcuts.
- `Super+Alt+Return` opens tmux. Use `Ctrl+Space` then `s` to list sessions. `Alt+Enter` and `Alt+Shift+Enter` split panes without a prefix; `Ctrl+Alt+Arrow` moves between panes.
- `tdl cx` opens an editor, Claude Code, and a terminal; `tdl c cx` adds OpenCode too. `tds` starts an editor/diff/terminal/agent layout, while `tdlm` and `tsl` cover multi-project and multi-agent layouts.
- Omarchy Mac's current edge image includes tmux, Neovim, Herdr, and the Omarchy edge desktop packages. Herdr is the agent-aware multiplexer: `Super+Ctrl+Return` opens it. Try it alongside tmux before deciding which fits your work.

These are Omarchy's documented defaults and intended workflow, not a claim that every AI developer uses the same setup. Omarchy's first-party manuals are the clearest current reference for the conventions the project ships.

## Left out on purpose

Omarchy Mac already ships these, or owning them would freeze a file that `omarchy update` is supposed to change. Account material stays on the machine.

- Stock Hyprland files: `bindings.lua`, `autostart.lua`, `monitors.lua`, `looknfeel.lua`, `hyprland.lua`
- `~/.config/git/config` aliases, pull rebase, and diff defaults
- Bash aliases for `eza`, `bat`, `starship`, `zoxide`, and `fzf`
- `~/.config/tmux/tmux.conf` (Omarchy's built-in config stays active)
- zsh as the login shell, and Linuxbrew
- Neovim, Ghostty, Karabiner, and macOS defaults
- `~/.config/omarchy/shell.json`
- SSH config, public keys, and private keys
- Git remotes, `insteadOf` rules, and account emails
- Wi-Fi names and Tailscale addresses

## Install

Run this on the Omarchy desktop, after the operating system is installed. It requires Omarchy on aarch64. It is the same `./install.sh` as the "Omarchy, new install" step above.

```bash
omarchy pkg add stow
./install.sh
```

That links the trackpad override, installs Herdr, Tailscale, keyd, Obsidian, and Bitwarden, enables `tailscaled` and `keyd`, and installs T3 Code's ARM64 CLI. If `~/.config/hypr/input.lua` already exists, the script moves it to `input.lua.bak-omarchy-groundwork`, preserving numbered predecessors if necessary, and then links the copy from this repo. It does not configure Git/SSH accounts. Complete the [Tailscale setup](docs/remote-access.md) and [desktop apps setup](docs/apps.md) after install.

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

`packages.txt` lists optional aarch64 packages. Add one with `omarchy pkg add`. Remote-desktop packages are commented there. See `docs/decisions/0004-remote-access.md`.

## Accounts

Git identity, SSH keys, and work remotes stay on each machine. See `docs/decisions/0006-no-personal-accounts-in-the-repo.md`.

## License

[MIT](LICENSE), matching the Groundwork family.

## Common developer setup and macOS containers

The family uses one developer tool set across personal and managed machines, with independent identity and optional-app preferences. See the [shared setup guide](https://github.com/jasondockery/groundwork-core/blob/main/docs/developer-setup.md) for tool and platform ownership.

For a macOS machine, Groundwork recommends Colima plus the Docker CLI. The single [Docker Desktop to Colima guide](https://github.com/jasondockery/groundwork-mac/blob/main/docs/docker-desktop-to-colima.md) covers migration scenarios, data transfer, verification, rollback, and Docker Desktop uninstallation after acceptance. This recommendation applies to macOS; this repository’s Linux, WSL, or Omarchy environment keeps its native container path.

## Coding agents and desktop apps

Setup preserves existing Codex, Claude Code, Grok Build, and OpenCode commands. A missing provider is installed through Omarchy’s declared mise tool, with a five-minute deadline; setup verifies that the command becomes available and stops on failure. Omarchy continues to own provider versions, updates, shortcuts, and default-agent selection. Run `omarchy update` through its supported workflow before setup to get current upstream declarations. Cursor uses the native `cursor-bin` package and still requires local sign-in. Package/feed availability on aarch64 is verified by the installer; an absent artifact is a failure with recovery guidance.

ChatGPT and Claude have no official native Linux desktop app in this setup. Use their web apps on Omarchy; this repo does not install unofficial replacements. Tailscale remains a required native package and service, with interactive tailnet enrollment. Authentication and live desktop acceptance are separate from package fixtures.
