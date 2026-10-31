# 0014. Offer, never force, a move to Omarchy's installers

When Omarchy has its own installer for an app this layer manages, a new install uses it. An app that is already installed another way is detected and reported. It moves only after explicit consent. Omarchy's installer wins on Omarchy, but an existing install and its data belong to the person using it.

## The rule

- **Detect.** Each run checks whether a managed app came from somewhere other than Omarchy's preferred installer, and whether that installer is available on this machine. When Omarchy has no installer for the app, nothing is offered and behavior is unchanged.
- **Report.** The notice is one line. It names the current method, Omarchy's preferred method, the migration id, and the exact command that migrates.
- **Consent.** Only two things count as consent: `y` or `yes` (in any case) at the setup prompt, or `./install.sh --migrate-to-omarchy-installers`. The prompt appears only during `./install.sh` when stdin, stdout, and stderr are all the terminal, and it defaults to no. `groundwork-update`, `update-all`, and any other run only print the notice. They never prompt and never migrate. Updates reject the flag.
- **Bounded.** Setup saves its choices and installs the update commands before any offer, so an unanswered or interrupted prompt loses nothing. The prompt waits at most 300 seconds and always leaves a yes at least 180 seconds of setup's 900-second apply deadline. With under 300 seconds left, setup prints the notice instead of asking. A timeout or end of input (Ctrl+D) keeps the app, saves nothing, and prints the notice line, so the next setup asks again. Ctrl+C stops setup with exit 130 after the choices were saved.
- **Remember.** A `n` or a plain Enter is saved with the other setup choices, as `declined_migrations` in `~/.config/groundwork/omarchy.json`. After that, setup prints one reminder line instead of asking again, and updates say nothing about it. The flag overrides a saved decline, and a completed migration clears it. Setup saves a second time only when this list changed.
- **Order.** Offers run after the rest of apply. When a migration fails, everything else is still applied and saved, the old install stays usable, and setup exits nonzero with the reason and the next command.

`offer_omarchy_migration` in `scripts/apply-layer.sh` implements this. To cover another app, detect it near the end of apply, then call the helper with a stable id (lowercase letters, digits, and hyphens), the current and preferred methods, a plan that says what is kept and how to undo it, and a function that returns nonzero, after printing why, when it fails.

### One flag, no dry run

Compass `terminal-program-ux` §3 asks for a per-migration `--migrate=<id>` and a `--dry-run`. This layer has one flag, `--migrate-to-omarchy-installers`, for every offer, and no dry run. Brave adds `brave-flatpak` and `default-browser-brave` beside `bitwarden-flatpak` ([decision 0015](0015-brave-via-omarchy-installer.md)). The same flag still migrates every pending offer in one run. The prompt and the notice name the id and state the plan. A per-id flag is not added: saying yes, or passing the flag, runs each offer that is still pending.

## Bitwarden desktop

Omarchy's Install → Service → Bitwarden runs `omarchy-install-and-launch Bitwarden 'bitwarden bitwarden-cli' bitwarden`, which adds the `bitwarden` and `bitwarden-cli` packages and then launches the app. The aarch64 feed lacked `bitwarden` when this layer adopted the Flathub app. `pacman -Si bitwarden` showed `omarchy/bitwarden 2026.10.1-1` for aarch64 on 2026-10-28. This layer runs `omarchy pkg add bitwarden` and does not launch Bitwarden.

The package counts only when it works here:

- `pacman -Qoq` reports exactly `bitwarden` as the owner of the `bitwarden` that is first on `PATH`. A user wrapper, for example one that runs the Flatpak, or a command from another package does not count.
- Every ELF file that `pacman -Qlq bitwarden` lists has LOAD alignment at least the kernel page size. This uses the same `readelf -lW` check as local inference. The reference mini uses 16 KiB pages.

When either check fails, a package that this run added is removed again with `omarchy pkg drop bitwarden`. A package that was already installed is left alone. The version that failed is remembered in `~/.local/state/groundwork/bitwarden-rejected.json`, keyed by page size like `t3-rejected.json`. The version checked is the installed one, or else the one `pacman -Si` offers. While that version is rejected, the package counts as unavailable: no new-install retry, no offer or notice, and no sudo request for it. A new version clears this. A failed `omarchy pkg add` is not remembered, because it can succeed on the next run.

- **New install.** When `pacman -Si bitwarden` finds the package, setup adds it. If the add fails, or a check fails, setup warns and installs the user Flatpak `com.bitwarden.desktop` as before, and that run does not offer the move. With no package in the feed, the Flatpak is the fallback unchanged. An existing native `bitwarden` is reused.
- **Sudo on updates.** With no Bitwarden desktop at all and the package in the feed, a routine update now needs administrator permission to add it. A run without a terminal and without cached sudo stops with the command to run, where it previously installed the user Flatpak without sudo.
- **Migration** (id `bitwarden-flatpak`). Offered when the user Flatpak is installed and the feed has the package. It is refused, with nothing changed, in three cases: `flatpak ps` fails, the app is running, or a `bitwarden` on `PATH` is not the package's. Otherwise it adds the package unless it is already installed, and checks it as above. Only then does it run `flatpak --user uninstall --noninteractive com.bitwarden.desktop`, without `--delete-data`. `~/.var/app/com.bitwarden.desktop` stays for recovery, and one launcher entry remains. Flathub and the shared runtimes stay for other apps.
- The vault stays in the Bitwarden account. Sign in again in the native app. To undo, run `flatpak --user install flathub com.bitwarden.desktop` and then `omarchy pkg drop bitwarden`.

## Brave

Brave's two offers, the page-size and launch checks, and the policy file are [decision 0015](0015-brave-via-omarchy-installer.md). The Flatpak `com.brave.Browser` and a `brave` command that `brave-bin` does not own use id `brave-flatpak`. A Brave that passed the checks and is not already the default uses id `default-browser-brave`. Migration installs `brave-bin` through `omarchy-install-browser brave`. It does not uninstall the Flatpak and does not pass `--delete-data`. `~/.var/app/com.brave.Browser` stays. Chromium stays.

## T3 Code

The bun CLI from `t3.codes/install.sh` (a `~/.local/bin/t3` link into `~/.t3/runtime/versions/`) uses id `t3-cli`. Omarchy's method is `omarchy-install-ai-t3-code nightly --no-launch` and the app's bundled `t3` ([decision 0020](0020-t3-code-through-omarchy-installer.md)). The move is refused while T3 Code or that CLI runs. It installs the app only when the app is missing, then points the link at `~/.t3/bin/t3`. `~/.t3` and `~/.config/t3code*` stay. The steps are in [the apps guide](../apps.md#t3-code).

Decision 0020 has one narrow exception to this rule: setup and `update-all` switch the link on their own when the app passed its check, its CLI exists, nothing runs, and `t3-cli` was never declined. The guards and the undo are in [decision 0020](0020-t3-code-through-omarchy-installer.md).

## Cursor: report only

Current Omarchy has no `omarchy-install-cursor`. Its Install menu adds the `cursor-bin` package, which is what this layer installs ([decision 0009](0009-install-cursor-through-omarchy.md)). A pacman-owned `cursor-bin` is Omarchy's own method there, so it is never offered a migration and never removed.

When `omarchy-install-cursor` is absent and `pacman -Si cursor-bin` finds the package, a `cursor` on `PATH` that `pacman -Qo` does not own is reported in one line. An example is the AppImage from the older Omarchy Mac `omarchy-install-cursor`. The line names that path, the `cursor-bin` package, and that the move is manual. The steps are in [the apps guide](../apps.md#cursor).

No automated removal ships, and `--migrate-to-omarchy-installers` does not touch Cursor. The older command is gone from current builds, and its file layout cannot be checked here. Cursor is not relaunched by setup or by any update.

## Not covered

Tailscale, the mise-managed AI providers, Obsidian, and Herdr stay as described in their own decisions and in `README.md`. None of them has a migration offer.
