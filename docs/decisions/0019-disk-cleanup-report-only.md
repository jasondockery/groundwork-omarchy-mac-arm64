# 0019. Disk cleanup: follow Omarchy, report the rest, delete nothing

Checked 2026-10-31 on the M1 mini (`omarchy-dev` 4.0.0.r7442). Part of #103.

## What Omarchy already does

- **Pacman cache.** `omarchy-update` runs `omarchy-update-pkg-prune`, which is `sudo paccache -rk2`. Two versions stay on purpose as the offline downgrade path. Groundwork adds no trim.
- **Free space.** `omarchy-update-requires-free-space` refuses an update under 10 GiB free. The report quotes that number and does not invent its own.
- **AUR packages.** `omarchy-update-aur-pkgs` updates them with `yay --cleanafter`. It does not clear yay's cache.
- **Disk usage tool.** Migration `1780739888.sh` ("Use dua for Disk Usage TUI") ran `omarchy-pkg-drop dust` and added `dua-cli`. Migration `1791345484.sh` (2026-10-30) then replaced `dua-cli` with `disktree-bin`, a graphical treemap that marks what to remove. `pacman -Si disktree-bin` shows `omarchy/disktree-bin 0.11.0-3`, Architecture aarch64, and `/usr/bin/disktree` is installed here. So Omarchy ships the graphical tool; Groundwork proposes no GUI app and no TUI.
- Omarchy has no journal vacuum, Flatpak unused-runtime cleanup, or yay cache cleanup (searched `/usr/share/omarchy/bin` and `install`).

## What Groundwork does

- `dust` is removed from `packages.txt`. It conflicts with Omarchy's migration. `packages.txt` is a reference list that no script installs from, so Groundwork never installed `dust` and has nothing to uninstall. If you installed it yourself, `omarchy pkg drop dust` removes it. Nothing offers or forces that (decision 0014).
- `scripts/groundwork-disk-report.py` measures the yay cache, the journal, and Flatpak installations with their unused runtimes, and prints the command to run for each. It also shows the pacman cache and free space for context. It runs only read-only commands (`journalctl --disk-usage`, `flatpak uninstall --unused --assumeno`) and never deletes.

## Not done

- Hooking the report into `update-all`: `scripts/groundwork-update.py` belongs to another lane. Follow-up.
- Any automatic deletion.

Recheck on 2026-11-30, or when Omarchy adds its own journal or Flatpak cleanup.
