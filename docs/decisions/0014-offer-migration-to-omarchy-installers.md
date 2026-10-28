# 0014. Offer, never force, a move to Omarchy's installers

When Omarchy has its own installer for an app this layer manages, a new install uses it. An app that is already installed another way is detected and reported. It moves only after explicit consent. Omarchy's installer wins on Omarchy, but an existing install and its data belong to the person using it.

## The rule

- **Detect.** Each run checks whether a managed app came from somewhere other than Omarchy's preferred installer, and whether that installer is available on this machine. When Omarchy has no installer for the app, nothing is offered and behavior is unchanged.
- **Report.** The notice is one line. It names the current method, Omarchy's preferred method, and the exact command that migrates.
- **Consent.** Only two things count as consent: `y` at the setup prompt, or `./install.sh --migrate-to-omarchy-installers`. The prompt appears only during `./install.sh` with a terminal on stdin and stdout, and it defaults to no. `groundwork-update`, `update-all`, and any run without a terminal only print the notice. They never prompt and never migrate. Updates reject the flag.
- **Remember.** A `n` or a plain Enter is saved with the other setup choices, as `declined_migrations` in `~/.config/groundwork/omarchy.json`. After that, setup prints one reminder line instead of asking again, and updates say nothing about it. End of input (Ctrl+D) keeps the app but saves nothing, so the next setup asks again. The flag overrides a saved decline, and a completed migration clears it.
- **Order.** Offers run after the rest of apply. When a migration fails, everything else is still applied and saved, the old install stays usable, and setup exits nonzero with the reason and the next command.

`offer_omarchy_migration` in `scripts/apply-layer.sh` implements this. To cover another app, detect it near the end of apply, then call the helper with a stable id (lowercase letters, digits, and hyphens), the current and preferred methods, a plan that says what is kept and how to undo it, and a function that returns nonzero, after printing why, when it fails.

## Bitwarden desktop

Omarchy's Install → Service → Bitwarden runs `omarchy-pkg-add bitwarden bitwarden-cli`. The aarch64 feed lacked `bitwarden` when this layer adopted the Flathub app. `pacman -Si bitwarden` showed `omarchy/bitwarden 2026.10.1-1` for aarch64 on 2026-10-28.

- A new install runs `omarchy pkg add bitwarden` when `pacman -Si bitwarden` finds it. If the package is missing from the sync database, or does not provide a `bitwarden` command, setup installs the user Flatpak `com.bitwarden.desktop` as before. An existing native `bitwarden` is reused.
- When both the user Flatpak and the package are available, the move is offered (id `bitwarden-flatpak`). The migration is refused while `flatpak ps` lists the app. Otherwise it installs the package unless `bitwarden` is already on `PATH`, and only then runs `flatpak --user uninstall --noninteractive com.bitwarden.desktop` without `--delete-data`. `~/.var/app/com.bitwarden.desktop` stays for recovery. One launcher entry remains. Flathub and the shared runtimes are left for other apps.
- The vault stays in the Bitwarden account. Sign in again in the native app. To undo, run `flatpak --user install flathub com.bitwarden.desktop` and then `omarchy pkg drop bitwarden`.

## Cursor: nothing to offer on current Omarchy

Current Omarchy has no `omarchy-install-cursor`. Its Install menu adds the `cursor-bin` package, which is what this layer installs ([decision 0009](0009-install-cursor-through-omarchy.md)). A pacman-owned `cursor-bin` is Omarchy's own method there, so it is never offered a migration and never removed. The only other install this layer knows is the older Omarchy Mac `omarchy-install-cursor`. That command is gone from current builds, and its file layout cannot be checked here, so no automated removal of it ships. Cursor is not relaunched by setup or by any update.

## Not covered

T3 Code, Tailscale, the mise-managed AI providers, Obsidian, and Herdr stay as described in their own decisions and in `README.md`. None of them has a migration offer.
