# Bitwarden: agent rules

Moved from `AGENTS.md`. Decision: [0014](../decisions/0014-offer-migration-to-omarchy-installers.md#bitwarden-desktop). Sign-in, Flatpak updates and removal: [apps guide](../apps.md).

- The default ensures Bitwarden CLI (`bw`), reuses a native Bitwarden desktop or an existing user Flatpak, and otherwise installs Omarchy's `bitwarden` package when `pacman -Si bitwarden` finds it and it passes the checks in decision 0014, with its user-scoped aarch64 Flathub app as the fallback.
- Count Omarchy's `bitwarden` package only when `pacman -Qoq` names `bitwarden` as owner of the `bitwarden` on `PATH` and its ELF files pass the page-size check. Drop it only when the same run added it, and remember the failed version in `bitwarden-rejected.json` until the feed changes.
- For installed Chromium, register only the official Bitwarden extension through its external manifest under `/usr/share/chromium/extensions`, preserving different previous manifests with numbered backups. Do not impose forced-install policy, rewrite profile databases, or authenticate accounts. Browser approval and effective autofill remain first-use acceptance.
