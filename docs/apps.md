# Desktop apps and personal notes

`./install.sh` ensures Obsidian, the Bitwarden CLI (`bw`), the Bitwarden desktop app, and FreeTube are available alongside T3 Code Nightly, Herdr, and Tailscale. It offers Brave through Omarchy's browser installer and registers the official Bitwarden extension for installed Chromium. Omarchy (`omacom/omarchy` `quattro`) continues to own desktop bindings and application updates, including the Qt 6.12 palette fix [omarchy#14553](https://github.com/omacom/omarchy/pull/14553) on this path. Account sign-in, extension approval, and sync enrollment stay local to each user.

## Brave

Brave is the browser this layer offers on Apple Silicon Omarchy. Chromium stays installed. The shared policy and the one offered extension are groundwork-core tag `v0.8.0` (`browser/brave.json`). This repo does not keep a second copy. The install, the 16 KiB-page check, and the launch check are [decision 0015](decisions/0015-brave-via-omarchy-installer.md).

A new install runs `omarchy-install-browser brave` when that command lists `brave` and no Brave is already present. Setup keeps it only when `brave-bin`'s ELF files load on this kernel and a short launch works: a Brave window with a throwaway profile opens on your desktop for about five seconds, with graphics on, and closes by itself with exit 0. Your own Brave profile is not used. A failed page-size check or a launch crash removes a copy that this run added, remembers that `brave-bin` version, and leaves Chromium as the default, all said in one line. A launch timeout or other non-signal failure is not remembered; the next run tries again. Over SSH or from cron there is no desktop to open the window on, so the check waits for a run from a terminal on the desktop. Setup does not install Brave Origin, and it does not install a Brave Flatpak.

The first time those checks pass and Chromium is the default, or no default is set, setup runs `omarchy default browser brave` and says so. It does that once: if you switch back to Chromium, or pick another browser, setup leaves that alone. From then on, when Brave is not the default, `./install.sh` in a terminal asks `[y/N]` before running `omarchy default browser brave` (migration id `default-browser-brave`). Updates and runs without a terminal print one line. A no is saved, and a saved no also stops the first automatic switch. To do it later:

```bash
./install.sh --migrate-to-omarchy-installers
```

That flag also runs any other pending offer. Once Brave is the default, web apps opened with `omarchy-launch-webapp` open in Brave. A sign-in that exists only in Chromium's profile stays there. To switch back, run `omarchy default browser chromium`.

A user Flatpak `com.brave.Browser`, or a `brave` command that `brave-bin` does not own, is reported and not replaced until you say yes (migration id `brave-flatpak`). Migration installs `brave-bin` and leaves the Flatpak and `~/.var/app/com.brave.Browser` in place. It does not pass `--delete-data`. Import bookmarks and passwords at `brave://settings/importData`.

Setup writes the v0.8.0 policy values to `/etc/brave/policies/managed/groundwork.json` as `root:root` mode `0644`. Rewards, Wallet, VPN, Brave AI (Leo and local AI), product analytics, the usage ping, and the built-in password manager are off. An unchanged file is not written again. `omarchy.json` and `color.json` in that directory are left alone. Check the result at `brave://policy`.

Bitwarden (`nngceckbapebfimnlniiiahkandclblb`) is the only extension offered. The same external manifest Chromium uses is registered under `/usr/share/chromium/extensions`, and `brave-bin` reads it too, so setup says the extension is registered for Chromium and Brave. Nothing is force-installed. If Brave does not offer Bitwarden from that file, install it from the Chrome Web Store link in the Chromium section and say so; do not add a forced-install policy. Restart Brave, approve the extension if prompted, and test autofill on one site.

`omarchy update` updates `brave-bin`. This layer does not. To remove a copy setup added, run `omarchy-remove-browser brave`. That does not delete `~/.config/BraveSoftware`. On the reference mini (16 KiB pages), `brave-bin` 1:1.98.62-1 opens, renders with GPU compositing, and passes the launch check. Two crashes there on 2026-10-30 happened while dragging a tab, not at start (issue #76).

## Bitwarden

Keep your existing Bitwarden account. No export or password-manager migration is needed. The [Omarchy manual](https://github.com/omacom/omarchy/blob/quattro/manual/24-commercial-apps-services.md#bitwarden) includes Bitwarden as a supported service, and Omarchy's Install → Service → Bitwarden adds the `bitwarden` and `bitwarden-cli` packages. The Apple Silicon feed lacked the desktop package on 2026-10-18. On 2026-10-28, `pacman -Si bitwarden` showed `omarchy/bitwarden 2026.10.1-1` for aarch64.

A new install runs `omarchy pkg add bitwarden` when `pacman -Si bitwarden` finds the package. Setup keeps the package only when its own `bitwarden` command is first on `PATH` and every ELF file in it loads on this kernel's pages; otherwise it removes the package again and remembers that version, so it is not tried again until the feed has a newer one. In that case, or when the feed has no package, setup installs the [Flathub desktop app](https://flathub.org/apps/com.bitwarden.desktop), which lists aarch64 support. An existing native `bitwarden` executable is reused. The package has not yet been launched on the 16 KiB-page reference mini.

### Moving from the Flatpak to Omarchy's package

If the user Flatpak `com.bitwarden.desktop` is installed and the package is available, setup offers the move and never forces it ([decision 0014](decisions/0014-offer-migration-to-omarchy-installers.md)). `./install.sh` in a terminal asks `[y/N]` and waits up to five minutes. When less than five minutes of the setup run are left, it prints the notice instead. `groundwork-update`, `update-all`, and runs without a terminal print one line instead, with the migration id `bitwarden-flatpak`. A no is saved and not asked again. No answer saves nothing. To move later, or after a no, run:

```bash
./install.sh --migrate-to-omarchy-installers
```

Quit Bitwarden first. The move changes nothing when the Flatpak is running, when `flatpak ps` fails, or when a `bitwarden` command that is not the package's comes first on `PATH`, such as your own wrapper or one from another package. It installs Omarchy's `bitwarden` package unless that package is already installed, and checks its command and ELF files as above. A package that does not pass is removed again if this run added it, and the Flatpak stays. Only then does it run `flatpak --user uninstall com.bitwarden.desktop` without `--delete-data`. The Flatpak's local data in `~/.var/app/com.bitwarden.desktop` stays, and only one launcher entry remains. Your vault is in your Bitwarden account: sign in again in the native app, choose the same region or server, and set your vault timeout again. Desktop settings do not carry over. To go back, run `flatpak --user install flathub com.bitwarden.desktop` and then `omarchy pkg drop bitwarden`. Remove the old data directory only after the native app works.

Launch **Bitwarden** from `Super+Space`. For a Flatpak installation, `flatpak run com.bitwarden.desktop` also works. Sign in to the right region/server, finish MFA, and choose your vault timeout and lock behavior. Keep the extension's own unlock flow; desktop biometric integration is not assumed for either build. The CLI is optional for daily use; authenticate it only when needed and never save its session token in the repo or shell startup files.

### Chromium extension

Setup gives `/usr/share/chromium` and its `extensions` directory mode `0755`, and the manifest mode `0644`, so Chromium can read the registration even when setup runs with a private umask. Rerunning setup repairs restrictive directory permissions left by an earlier run.

Setup registers the [official Bitwarden extension](https://chromewebstore.google.com/detail/bitwarden-password-manage/nngceckbapebfimnlniiiahkandclblb) using `/usr/share/chromium/extensions/nngceckbapebfimnlniiiahkandclblb.json`, following Omarchy's existing external-extension mechanism. The extension id and update URL are read from groundwork-core `v0.8.0` `browser/brave.json`, not from a copy in this repo. They match [Bitwarden's deployment guide](https://bitwarden.com/help/browserext-deploy/). Chromium downloads and updates the extension; the installer does not edit browser profile databases or sign in to accounts. Brave is offered the same manifest; see [Brave](#brave).

Restart Chromium after setup. In each profile, approve/enable Bitwarden if prompted, pin its toolbar icon if useful, sign in, and test autofill on one familiar site. Existing profiles, accounts, and other extensions are preserved. If you previously removed or disabled it, Chromium may remember that decision; enable it at `chrome://extensions` or install it from the official store link. No forced-install policy is imposed. For other browsers, install their official Bitwarden extension separately. If Chromium is absent, setup reports the skip; rerun after installing it through Omarchy.

A different existing manifest for this extension is backed up with a numbered suffix; unchanged reruns do not create extra backups. To stop offering it in Chromium, remove that exact manifest with `sudo rm /usr/share/chromium/extensions/nngceckbapebfimnlniiiahkandclblb.json`, then manage any already-installed copy through `chrome://extensions`. Restore the numbered backup instead if you need the former registration. Sign-out, extension removal, and vault data deletion are separate choices.

Arch packages update through `omarchy update`. User Flatpaks have a separate update step:

```bash
flatpak --user update
```

Rerunning this layer reuses an installed desktop app. If its installation fails, resolve that error and rerun; the installer stops before changing input settings. If its launcher is missing immediately after installing Flatpak for the first time, log out and back in to refresh desktop application discovery.

To remove a layer-installed Bitwarden Flatpak, use `flatpak --user uninstall com.bitwarden.desktop`. Keep its local data unless you deliberately choose to remove it after checking account recovery. The shared Flathub remote and Flatpak runtime may serve other apps; leave those in place. Native apps and browser extensions are removed through their own package/browser controls.

## Cursor

Setup installs Cursor with the `cursor-bin` package, as Omarchy's Install menu does, and adds the GNOME keyring setting to `~/.config/Cursor/argv.json` ([decision 0009](decisions/0009-install-cursor-through-omarchy.md)). If the `cursor` first on `PATH` is not owned by a pacman package, setup prints one line saying so. An example is an AppImage from the older Omarchy Mac `omarchy-install-cursor`. Setup does not remove it, and `--migrate-to-omarchy-installers` does not touch it. To move it by hand:

1. Quit Cursor. Keep `~/.config/Cursor` and `~/.cursor`; they hold your settings, `argv.json`, and extensions, and the package uses the same folders.
2. Run `command -v cursor` and note the path. Remove that copy the way it was installed: its AppImage, its wrapper, and its `.desktop` launcher, often under `~/.local`.
3. Run `omarchy pkg add cursor-bin`, then `hash -r`. `command -v cursor` should now print `/usr/bin/cursor`.
4. Rerun `./install.sh`. The notice is gone and the keyring setting is checked again.

## FreeTube

FreeTube is a private YouTube desktop client with no Google sign-in. Subscriptions, history, and playlists stay on this machine. Setup installs it by default and leaves its settings at the upstream defaults.

Arch Linux, Arch Linux ARM, the Omarchy edge feed (`pkgs.omarchy.org/edge/aarch64`), and ALARM's prebuilt `aur` database have no `freetube` package (checked 2026-10-24). `omarchy pkg add` is pacman-only, so the AUR `freetube-bin` PKGBUILD is outside this installer. This layer installs the [Flathub app](https://flathub.org/apps/io.freetubeapp.FreeTube) `io.freetubeapp.FreeTube`, which publishes an aarch64 build. That build unpacks FreeTube's official `freetube-0.25.3-beta-linux-arm64-portable.zip` (SHA-256 `2416b0ce365b4bea0caf3d17b177ac134c9d9a627a4a97bac01afaca2c1c681e`). The bundle is Electron 42.5.2. Every ELF file in it (`freetube`, `chrome-sandbox`, `chrome_crashpad_handler`, and the bundled `lib*.so`) has LOAD alignment `0x10000`. The deployed app's merged Electron BaseApp ELFs, and org.freedesktop.Platform 25.08 aarch64 including `libc.so.6` and `ld-linux-aarch64.so.1`, are also aligned to `0x10000`. Electron 42 includes the 16 KiB page fixes from Electron 34.3.0. This build has not been launched on the reference M1 mini yet.

Launch **FreeTube** from `Super+Space`, or run `flatpak run io.freetubeapp.FreeTube`. Its data directory is:

```text
~/.var/app/io.freetubeapp.FreeTube/config/FreeTube
```

That is the Flatpak path from [FreeTube's data-location guide](https://docs.freetubeapp.io/usage/data-location/). `profiles.db`, `settings.db`, `playlists.db`, and `history.db` live there. Setup leaves that directory to FreeTube. Cross-machine sync of it is a separate task.

`update-all` updates this user Flatpak when it is installed. To remove the app, run `flatpak --user uninstall io.freetubeapp.FreeTube`. Leave the data directory in place unless you mean to delete subscriptions and history.

## Obsidian and the Markdown folder

Obsidian is already in [Omarchy's base packages](https://github.com/omacom/omarchy/blob/quattro/install/omarchy-base.packages). This layer ensures its executable is available. Use stock **Super+Shift+O** to open it, then **Open folder as vault** and select:

```text
~/Documents/Obsidian/my_obsidian_vault
```

This keeps the former Groundwork vault name and Vim-mode preference, using a local folder accessible to Obsidian, Neovim, and agent tools on Linux. The helper seeds `.obsidian/app.json` only when absent; existing app settings and notes win. To use another local folder, close Obsidian and select it explicitly when running setup:

```bash
GROUNDWORK_OBSIDIAN_VAULT="$HOME/Documents/Obsidian/Notes" ./install.sh
```

The helper refuses symlinks in the destination path; pass the actual folder path instead. It does not register or switch vaults in Obsidian's global database. Select the folder in-app. For theme integration, select the **Omarchy** theme in Obsidian's Appearance settings as described in the [Omarchy guide](https://github.com/omacom/omarchy/blob/quattro/manual/22-guis.md#obsidian).

Groundwork's former iCloud-only path is still appropriate for Apple-only use, but iCloud Drive has no native Linux client. Existing Mac notes stay where they are until you deliberately migrate them. Do not point two different sync tools at the same vault.

### Plugins from the previous setup

The [existing Groundwork plugin list](https://github.com/jasondockery/groundwork-mac/blob/v2.0.0/home/dot_local/share/groundwork/obsidian-plugins.txt) remains the reference for your previous setup. This layer carries over Vim mode automatically and leaves community plugin installation to Obsidian's **Settings → Community plugins → Browse**. Start with the plugins your notes use: Dataview, Templater, Tasks, Calendar/Periodic Notes, Linter, Omnisearch, and Advanced Tables. Add Excalidraw or Kanban for existing drawings/boards. The linked list includes the other former choices if you need them.

This is a deliberate smaller starting point than automatically enabling the entire old plugin set. Community plugins execute with access to the vault. Smart Connections, Whisper, and other AI integrations remain optional; configure their providers and data access locally. Plugin credentials and `data.json` files do not belong in public Git history. Obsidian Git is optional version history, not the default mobile sync engine.

### Free sync across operating systems

For a vault shared with iPhone/iPad as well as Linux, macOS, Windows, or Android, use the free/basic [Remotely Save](https://github.com/remotely-save/remotely-save) community plugin with a supported personal storage account. Its [free tier](https://remotelysave.com/) supports **OneDrive for personal (App Folder)**, Dropbox, and WebDAV. Storage quotas still apply; Google Drive and OneDrive full-drive access are not the same free connector. This is a community sync method; [Obsidian Sync](https://obsidian.md/sync) is the paid first-party alternative.

1. Close Obsidian on every device. Make a separate backup of the complete original vault, including attachments and hidden settings. Keep it until all devices are verified. Sync is not a backup.
2. On one desktop, copy the existing notes into the chosen local vault outside iCloud or another sync root. Work from this copy; keep the old vault as recovery. Open it in Obsidian and check a few notes, links, and attachments.
3. Install **Remotely Save** through Obsidian. Choose **OneDrive for personal (App Folder)** for the baseline free path, authorize your own personal account, and set an end-to-end encryption password before the first upload. Save that password in Bitwarden; losing it prevents recovery of the encrypted remote. Do not sync an unencrypted copy first and then assume encryption removes old provider versions.
4. Leave hidden configuration/plugin syncing disabled initially. Sync the notes and attachments to completion. Exclude `.git` and device-specific workspace files if you later broaden the paths. Store sync credentials only in the local plugin settings; never commit or paste them into this repo.
5. On each other device, create an empty local vault with the same name, install the plugin, select the same service/account and encryption password, and download before editing. On iOS, use **On My iPhone/iPad**, rather than an iCloud-backed vault, for this setup.
6. Verify a disposable note, rename, and attachment in both directions. Sync before and after editing, especially when switching devices; avoid concurrent edits to the same note. On iOS, keep Obsidian open until sync completes—continuous background sync is not promised. Check conflicts instead of automatically discarding one copy.

If something looks wrong, stop sync on all devices and compare with the backup before propagating more edits or deletions. Keep the original iCloud copy until you have accepted the new setup. Disabling the plugin stops this sync path; remote data and provider authorization are managed separately in the storage account. Never run this alongside iCloud, Syncthing, or Obsidian Sync on the same vault.

For desktop-only file trees, Syncthing is another free option. Its iOS path needs additional third-party software and platform compromises, so it is not this guide's default. For occasional transfers, Omarchy already includes **LocalSend**; it does not provide continuous vault sync. **Omawrite** is also included for simple Markdown editing without a vault. These are useful starting apps without adding another service.

## iMessage and session restoration

iMessage on Omarchy is the opt-in [Blip](imessage.md) client (`nixfred.blip`). It stays off unless you pass `./install.sh --with-blip`. A default run and `--without-blip` leave an existing Blip install unchanged. The macOS gateway is a separate computer; its role is tracked in [groundwork-mac#25](https://github.com/jasondockery/groundwork-mac/issues/25). Follow [iMessage setup](imessage.md) for install, the manual grants, and removal.

Desktop session restoration remains at Omarchy's native defaults. Community restoration plugins are not included. Save documents before rebooting; running terminal/agent processes do not survive a reboot.
