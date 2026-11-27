# Antigravity: agent rules

User guidance: [apps guide](../apps.md#antigravity). Issue: [#162](https://github.com/jasondockery/groundwork-omarchy-mac-arm64/issues/162).

Antigravity is on by default. Setup installs the `agy` CLI and the Antigravity desktop app through Omarchy, checks both on this kernel's pages, and pins T3 Code's Antigravity provider at the pacman-owned `agy`. It does not launch either program and it does not sign in.

## Omarchy first

Checked 2026-11-27 against Omarchy `quattro` and the aarch64 edge feed:

- `omarchy-install-ai-cli agy` installs the curated package `antigravity-cli` (Install, AI, CLI, Antigravity). When `agy` is already on `PATH`, that installer leaves the existing command in place.
- The desktop menu runs `omarchy-install-and-launch` for the `antigravity` package, which starts the app. This layer does not use that command. It installs the package with `omarchy-pkg-add-curated antigravity` when that command exists, otherwise `omarchy pkg add antigravity`, and it does not launch it.
- The feed carries `antigravity` and `antigravity-cli` for aarch64. Do not install the AUR copy while the feed has the package.
- If `omarchy-mise-default-tools declares agy` succeeds, install with that mise declaration instead of the package installer. Do not add `agy` to the required provider list (`claude`, `codex`, `opencode`, `grok`).
- When `omarchy-install-ai-cli` is absent, install `antigravity-cli` with `omarchy pkg add`.
- An `agy` or `antigravity` command already on `PATH` from any source is kept and reported. It is not replaced.
- Undo, when someone asks to remove them: `omarchy-remove-ai-cli agy` and `omarchy-remove-ai-antigravity`. The desktop remover keeps `~/.gemini` and removes only the app's config directory and updater cache. `--without-antigravity` does not run either remover.

Recheck this list when the install method or the feed changes. `omarchy-feed-watch.json` lists Antigravity under `not_watched` because the install path is Omarchy's.

## Page size

Apple Silicon kernels use 16 KiB pages. There is no separate arm64 build to fall back to, and setup does not launch the app, so both packages are checked with decision 0018's `t3_load_segment_mappable` on every `EM_AARCH64` ELF `pacman -Qlq` lists. Other architectures in the package are skipped. A package with no aarch64 ELF is refused.

The strict `p_align >= page` rule stays in `package_alignment_problem` for Bitwarden, Brave, and local inference. These two packages use the 0018 predicate because a segment whose alignment is below the page still maps when its offset and virtual address both start on a page.

A failure is remembered by page size, the same way Bitwarden is. A package this run added is removed with `omarchy pkg drop`. A package that was already installed is kept and reported, and setup does not try that version again until the feed has another. The CLI and the desktop app are independent: a desktop failure does not remove a passing `agy` and does not skip the T3 Code pin. The key binding is skipped when the desktop app is missing or fails its check.

## T3 Code provider

After a pacman-owned `agy` passes the check, setup merges one instance into `${T3CODE_HOME:-~/.t3}/userdata/settings.json` only when `providerInstances.antigravity` is absent:

```json
{
  "driver": "antigravity",
  "enabled": true,
  "config": {
    "binaryPath": "/usr/bin/agy"
  }
}
```

`binaryPath` is the absolute path of that pacman-owned `agy`. An empty `binaryPath` makes T3 download its own runtime, which skips this page-size check, so the instance is not added unless the package binary passed. An `agy` that is not the package is reported and not pinned.

Leave every other field at T3's default. In particular leave `authMethod` unset so it stays `oauth-personal` (a personal account with Ultra needs that). Do not write `apiKey`, models, or account ids. Do not print the file or any value from it. Invalid JSON, a root that is not an object, and a `providerInstances` value that is not an object are left untouched. An existing `antigravity` instance is left byte for byte, including `enabled: false` or a different `binaryPath`. A missing file is created mode `0600`. A symlink settings file is not followed. `T3CODE_HOME` empty or unset means `~/.t3`; a relative `T3CODE_HOME` is refused. The write is atomic and keeps the existing file mode. T3 reads the file while it is open; do not restart or quit T3 for this merge.

## Launcher and key binding

Use the desktop entry the `antigravity` package installs. Write `~/.local/share/applications/antigravity.desktop` only when `pacman -Qlq antigravity` succeeds and lists no `.desktop` file, and do not replace a file this layer did not write.

The only Hyprland binding is `config/groundwork-antigravity.lua`: `Super+Shift+V` launches `antigravity` and focuses its window class. Checked 2026-11-27 against `omacom/omarchy` `quattro` `default/hypr`: `Super+Shift+A` is ChatGPT and `Super+Shift+G` is Signal in the global region, and `Super+Shift+O` is Obsidian. `Super+V` in a terminal is paste; this chord is `Super+Shift+V`. The focus pattern is `StartupWMClass` from the package's desktop entry when that class is a plain name, otherwise `antigravity`.

Setup copies that file to `~/.config/hypr/groundwork-antigravity.lua`. The copy is a regular file, not a link into this checkout. It appends one `dofile` line to `~/.config/hypr/bindings.lua` when that file is a regular file and the line is absent. It does not rewrite the rest of the file. It does not edit `bindings.lua` when that path is a symlink: a stowed Hypr file edited through the link dirties this checkout and blocks `update-all`. Do not stow this binding and do not put it in `packages/hypr`.

When the desktop app is missing or fails its check, remove only that `dofile` line. `--without-antigravity` removes nothing.

## What setup must not do

- Do not run `agy`, `antigravity`, `omarchy-install-and-launch`, or `agy --help`. The doctor is the one place that runs `agy --version`.
- Do not sign in, open a browser, or store a Google account, token, or Ultra entitlement.
- Do not pass `--delete-data`.
- `--without-antigravity` saves `"antigravity": false` and leaves an existing install, the settings entry, and the binding alone. `--with-antigravity` removes that key.
