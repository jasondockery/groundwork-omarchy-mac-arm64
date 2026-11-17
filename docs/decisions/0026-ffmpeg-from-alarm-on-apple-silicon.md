# 0026. Take ffmpeg from Arch Linux ARM on Apple Silicon

Checked on the reference M1 Mac mini on 2026-11-17. Issue #156.

## Problem

`update-all` stopped in the Omarchy stage with:

```
:: installing libplacebo (7.387.0-1) breaks dependency 'libplacebo.so=360-64' required by ffmpeg
error: failed to prepare transaction (could not satisfy dependencies)
```

The installed `ffmpeg 2:9.0.2-2.1` comes from the `omarchy` repo, which is listed before `[extra]`. pacman resolves a package name to the first repository that has it, even when a later repository has a newer version. Arch Linux ARM `[extra]` has `ffmpeg 2:9.0.2-5` and `libplacebo 7.387.0-1`. The `omarchy` build was made on 2026-11-03 against `libplacebo.so=360`, and the feed had not rebuilt it on 2026-11-17. `libplacebo` is required here only by `ffmpeg` and `mpv`.

## Why the Omarchy build exists

The `ffmpeg` PKGBUILD in `omacom/omarchy-pkgs` (`pkgbuilds/ffmpeg/PKGBUILD`, commit "Ship ffmpeg with NVENC and NVDEC on aarch64", 2026-11-03) is Arch Linux ARM's `2:9.0.2-2` with NVIDIA's encoders and decoders added back for the NVIDIA GB10 and N1x. Its comment says the carry should follow ALARM's version and that "a newer ALARM build replaces this one without them". Nothing on an Apple Silicon Mac uses those encoders.

## Decision

- **On Apple Silicon, install `ffmpeg`, `libplacebo` and `mpv` from `[extra]` in one transaction:** `sudo pacman -S extra/ffmpeg extra/libplacebo extra/mpv`, then run `update-all`. This repo does not run it. It needs administrator permission and changes a package Omarchy ships, so Jason runs it. A 2026-11-17 dry run against fresh sync databases resolves this set. `ffmpeg` and `mpv` are the only installed packages that require `libplacebo`, and the new `mpv` needs `libplacebo.so=387`.
- **`extra/ffmpeg` alone is not enough.** Its `PKGINFO` declares a plain `depend = libplacebo`, not `libplacebo.so=387-64`, so pacman installs it next to the old `libplacebo 7.360.1`. Its `libavfilter.so.12` needs `libplacebo.so.387`, so `ffmpeg` and `mpv` would fail to load until `libplacebo` is upgraded. Adding `extra/libplacebo` without `extra/mpv` fails, because the installed `mpv` pins `libplacebo.so=360-64`. `--ignore libplacebo` alone fails the same way on `mpv`; `--ignore libplacebo,mpv` resolves but holds back two packages and the failure returns on the next run.
- **Later `extra` builds are not picked up while the feed shadows them.** `[omarchy]` stays first, so pacman keeps resolving `ffmpeg` to the feed's `2:9.0.2-2.1`. It is older than the installed `-5`, so pacman prints a "local is newer" warning and installs nothing. A newer `extra` build needs `sudo pacman -S extra/ffmpeg` again. If the feed later publishes a version above the installed one, pacman switches back to the carry, which is fine once it is rebuilt against the current `libplacebo` and brings the shadowing back. Because `extra`'s `ffmpeg` has no pinned `libplacebo` soname, a later `libplacebo` soname bump can upgrade without pacman stopping it, and `ffmpeg` would then fail to load. When that happens, upgrade `ffmpeg`, `libplacebo` and `mpv` together again.
- **The stable channel does not have the carry.** `pkgs.omarchy.org/stable/aarch64` has no `ffmpeg`, so a Mac on stable takes Arch Linux ARM's. That is one input to the channel choice in [decision 0002](0002-edge-via-omarchy-mac-installer.md). It is not a reason to move on its own.
- **No `IgnorePkg`.** `/etc/pacman.conf` is Omarchy's file, and a held package would drift behind `omarchy update`.
- **`update-all` prints the repair and never runs it** (issue #160). `omarchy update` runs on the terminal, so its output cannot be matched. After it fails, the updater runs read-only `pacman` queries and prints the command only when all of these hold: `pacman -Si ffmpeg` resolves to the `omarchy` repository, `extra/ffmpeg` is newer by `vercmp`, and the installed `ffmpeg` depends on a `libplacebo.so=` soname that the repository `libplacebo` no longer provides. The package list is `ffmpeg`, `libplacebo`, and the installed packages that require `libplacebo` and exist in `extra`. A failing query prints nothing and leaves the original error alone. It never runs `sudo` or installs a package, because that would change a package Omarchy ships on a policy Omarchy owns.
- **Upstream.** A report to `omacom/omarchy-pkgs` would ask for the carry to be limited to the machines that need NVENC, or for its `libplacebo` dependency to be pinned and rebuilt when ALARM's `libplacebo` moves. It is not filed from this repo; it needs Jason's approval of the text first.

## Not done

- Run on the reference machine on 2026-11-17 at 06:22 EST by the owner, and recorded in [status](../status.md): `ffmpeg 2:9.0.2-5`, `libplacebo 7.387.0-1` and `mpv 1:0.42.0-2` from `[extra]`, `ffmpeg -version` and `mpv --version` start, `ldd /usr/lib/libavfilter.so.12` shows no missing library, and `update-all` finished afterwards.
- Recheck by 2026-12-15. Look at whether the feed's `ffmpeg` was rebuilt against the current `libplacebo`, whether it still shadows `extra`'s newer builds, whether `pacman -Q ffmpeg` has fallen behind `pacman -Si extra/ffmpeg`, and whether it still loads: `ffmpeg -version` and `mpv --version` start, and `ldd /usr/lib/libavfilter.so.12 | grep 'not found'` prints nothing. Remove the status row and the README paragraph when the feed no longer shadows `extra`.
