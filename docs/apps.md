# Desktop apps and personal notes

`./install.sh` ensures Obsidian, the Bitwarden CLI (`bw`), the Bitwarden desktop app, and FreeTube are available alongside T3 Code Nightly, Herdr, and Tailscale. It registers the official Bitwarden extension for installed Chromium. Omarchy (`omacom/omarchy` `quattro`) continues to own desktop bindings and application updates, including the Qt 6.12 palette fix [omarchy#14553](https://github.com/omacom/omarchy/pull/14553) on this path. Account sign-in, extension approval, and sync enrollment stay local to each user.

## Bitwarden

Keep your existing Bitwarden account. No export or password-manager migration is needed. The [Omarchy manual](https://github.com/omacom/omarchy/blob/quattro/manual/24-commercial-apps-services.md#bitwarden) includes Bitwarden as a supported service. The current Apple Silicon feed has `bitwarden-cli` but lacks the native desktop package, so this layer uses the [Flathub desktop app](https://flathub.org/apps/com.bitwarden.desktop), which lists aarch64 support. An existing native `bitwarden` executable is reused instead. Package availability was checked on 2026-10-18; reconsider the fallback when the upstream ARM feed gains a desktop package.

Launch **Bitwarden** from `Super+Space`, or run `flatpak run com.bitwarden.desktop` for the Flatpak installation. Sign in to the right region/server, finish MFA, and choose your vault timeout and lock behavior. Keep the extension's own unlock flow; desktop biometric integration is not assumed for this sandboxed build. The CLI is optional for daily use; authenticate it only when needed and never save its session token in the repo or shell startup files.

### Chromium extension

Setup gives `/usr/share/chromium` and its `extensions` directory mode `0755`, and the manifest mode `0644`, so Chromium can read the registration even when setup runs with a private umask. Rerunning setup repairs restrictive directory permissions left by an earlier run.

Setup registers the [official Bitwarden extension](https://chromewebstore.google.com/detail/bitwarden-password-manage/nngceckbapebfimnlniiiahkandclblb) using `/usr/share/chromium/extensions/nngceckbapebfimnlniiiahkandclblb.json`, following Omarchy's existing external-extension mechanism. Its ID and Google Web Store update URL match [Bitwarden's deployment guide](https://bitwarden.com/help/browserext-deploy/). Chromium downloads and updates the extension; the installer does not edit browser profile databases or sign in to accounts.

Restart Chromium after setup. In each profile, approve/enable Bitwarden if prompted, pin its toolbar icon if useful, sign in, and test autofill on one familiar site. Existing profiles, accounts, and other extensions are preserved. If you previously removed or disabled it, Chromium may remember that decision; enable it at `chrome://extensions` or install it from the official store link. No forced-install policy is imposed. For other browsers, install their official Bitwarden extension separately. If Chromium is absent, setup reports the skip; rerun after installing it through Omarchy.

A different existing manifest for this extension is backed up with a numbered suffix; unchanged reruns do not create extra backups. To stop offering it in Chromium, remove that exact manifest with `sudo rm /usr/share/chromium/extensions/nngceckbapebfimnlniiiahkandclblb.json`, then manage any already-installed copy through `chrome://extensions`. Restore the numbered backup instead if you need the former registration. Sign-out, extension removal, and vault data deletion are separate choices.

Arch packages update through `omarchy update`. User Flatpaks have a separate update step:

```bash
flatpak --user update
```

Rerunning this layer reuses an installed desktop app. If its installation fails, resolve that error and rerun; the installer stops before changing input settings. If its launcher is missing immediately after installing Flatpak for the first time, log out and back in to refresh desktop application discovery.

To remove the layer-installed Flatpak, use `flatpak --user uninstall com.bitwarden.desktop`. Keep its local data unless you deliberately choose to remove it after checking account recovery. The shared Flathub remote and Flatpak runtime may serve other apps; leave those in place. Native apps and browser extensions are removed through their own package/browser controls.

## FreeTube

FreeTube is a private YouTube desktop client with no Google sign-in. Subscriptions, history, and playlists stay on this machine. Setup installs it by default and leaves its settings at the upstream defaults.

Arch Linux, Arch Linux ARM, the Omarchy edge feed (`pkgs.omarchy.org/edge/aarch64`), and ALARM's prebuilt `aur` database have no `freetube` package (checked 2026-10-24). `omarchy pkg add` is pacman-only, so the AUR `freetube-bin` PKGBUILD is outside this installer. This layer installs the [Flathub app](https://flathub.org/apps/io.freetubeapp.FreeTube) `io.freetubeapp.FreeTube`, which publishes an aarch64 build. That build unpacks FreeTube's official `freetube-0.25.3-beta-linux-arm64-portable.zip` (SHA-256 `2416b0ce365b4bea0caf3d17b177ac134c9d9a627a4a97bac01afaca2c1c681e`). The bundle is Electron 42.5.2. Every ELF file in it (`freetube`, `chrome-sandbox`, `chrome_crashpad_handler`, and the bundled `lib*.so`) has LOAD alignment `0x10000`, so the kernel can map it on this machine's 16 KiB pages. Electron 42 includes the 16 KiB page fixes from Electron 34.3.0. The T3 and bun `load_alignments` rejection is for a build whose LOAD alignment is below the page size; this FreeTube build is above that line.

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
