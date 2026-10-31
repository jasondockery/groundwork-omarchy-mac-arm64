# T3 Code: agent rules

Moved from `AGENTS.md`. User-facing steps are in the [README](../../README.md).

- `./install.sh` reconciles the newest official T3 Code Nightly CLI on each run, including existing stable installations, except on kernels with pages larger than 4 KiB, where it keeps a Nightly that already starts and otherwise pins `0.1.2-nightly.20261021.3499`.
- On those kernels `update-all` checks every ELF file in a newer build with `readelf -lW` (a LOAD passes with `p_align` of at least the page, or with `p_offset` and `p_vaddr` both page-aligned; [decision 0018](../decisions/0018-t3-load-segment-rule.md)), smoke-runs that candidate under bubblewrap with a scrubbed environment, a core limit of 1 (the only limit a piped core pattern treats as do-not-dump), and a timeout (a missing bwrap skips that run with a warning and does not execute it), installs exactly the version whose `--version` matches, remembers misaligned builds and signal/`PinnedRuntimeInstallError` failures in `t3-rejected.json` with the rule marker and a reason (a file without the marker is dropped once and rechecked; decision 0018), warns without remembering timeouts or transport errors, reports a failed `t3 update` that leaves a working install as a warning, and repairs a T3 that no longer starts (pingdotgg/t3code#23433).
- Setup and `update-all` also run `scripts/groundwork-t3-launcher.py reconcile`, which points `x-scheme-handler/t3code` at the installer's `t3code.desktop` with `xdg-mime` and removes the app's self-registered duplicate `com.t3tools.T3Code.desktop` (the app re-creates it hidden with `NoDisplay=true`, which the Omarchy shell launcher honors). It changes nothing, and says so, while a T3 Code process is running or cannot be ruled out.
- Blip's bun preflight calls the T3 `load_alignments` helper (see [Blip](blip.md)).

## Omarchy-first evidence

Checked 2026-10-31: the pkgs.omarchy.org `edge/aarch64` feed (`omarchy.db`) lists `t3code-bin 0.1.5-1` and `t3code-nightly-bin 0.1.6_nightly.20261030.3940-1`. This layer still installs the official Nightly CLI. Whether to move to the feed packages belongs to the T3 decision lane, not to this file. Recheck the feed when that decision is made, and no later than 2026-11-30.
