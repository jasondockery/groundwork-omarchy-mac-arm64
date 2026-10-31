# Brave: agent rules

Moved from `AGENTS.md`. Decision: [0015](../decisions/0015-brave-via-omarchy-installer.md). User guidance: [apps guide](../apps.md#brave).

## Install and core checkout

- Brave is offered through `omarchy-install-browser brave` when that installer lists `brave`. Keep Chromium.
- Setup and update check out `core` with `git submodule update --init -- core` after a fast-forward and before apply. `apply-layer.sh` runs that checkout when `core/browser/brave.json` is missing, so an `update-all` still running the updater from the previous apply can fast-forward onto this revision. A core worktree left at the previous gitlink is not a local change; edits inside `core` are.

## Policy

- Write `core/browser/brave.json` (groundwork-core, at the tag pinned by the `core` submodule) to `/etc/brave/policies/managed/groundwork.json` as `root:root` mode `0644`, and chown that tree when the bytes already match.
- Do not keep a second copy of the spec or of the extension manifest.
- Offer Bitwarden only (`nngceckbapebfimnlniiiahkandclblb`); do not add a forced-install policy.

## Launch check

- The launch check opens a real Brave window (Wayland and the GPU, no `--headless` or `--disable-gpu`) with a throwaway profile and private `HOME`/`XDG_*`, passes only the absolute Wayland socket path from the session, drives it over `--remote-debugging-pipe` (no port), and requires a clean exit 0 after `Browser.close`. With no Wayland session it is not run and is retried.
- Remember a `brave-bin` that fails the page-size check or dies by a crash signal (SEGV, TRAP, ABRT, BUS, ILL, FPE) during that launch in `brave-rejected.json`, and remove it only when this run added it.
- A launch timeout, missing session, an outside signal such as SIGKILL or SIGTERM, or other non-signal failure is not remembered and is not removed, and an update retries that launch without sudo when the policy is already current.
- Ctrl+C during the launch check exits 130 and does not remember the version.
- The launch sets `RLIMIT_CORE` to 1 so a piped core pattern does not store a crash dump.
- A failure prints one line naming the default.

## Default browser

- The first time a `brave-bin` passes on this page size and the default is Chromium or unset, setup runs `omarchy default browser brave` itself unless `default-browser-brave` was declined. After that a Brave that is not the default is only offered.
- Drop only Omarchy's "Make it the default via Setup > Defaults > Browser." line from `omarchy-install-browser` output.
- When Brave is already the default, a failed recheck says so and names `omarchy default browser chromium`.

## State files and limits

- An existing `brave-rejected.json` or `brave-accepted.json` that is not a regular file of page size to version lists stops setup before install.
- Do not install Brave Origin, a Brave Flatpak, or pass a pooled-pages workaround.
- Do not delete `~/.config/BraveSoftware` or `~/.var/app/com.brave.Browser`.
- Save a declined migration before publishing policy.
