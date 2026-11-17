# 0026. Take ffmpeg from Arch Linux ARM on Apple Silicon

Checked on the reference M1 Mac mini on 2026-11-17. Issue #156.

## Problem

`update-all` stopped in the Omarchy stage with:

```
:: installing libplacebo (7.387.0-1) breaks dependency 'libplacebo.so=360-64' required by ffmpeg
error: failed to prepare transaction (could not satisfy dependencies)
```

The installed `ffmpeg 2:9.0.2-2.1` comes from the `omarchy` repo, which is listed before `[extra]`. pacman resolves a package name to the first repository that has it, even when a later repository has a newer version. Arch Linux ARM `[extra]` has `ffmpeg 2:9.0.2-5` and `libplacebo 7.387.0-1`. The `omarchy` build was made on 2026-11-03 against `libplacebo.so=360`, and the feed had not rebuilt it on 2026-11-17. `libplacebo` and `mpv` are installed here because `ffmpeg`, `mpv` and several other packages depend on them.

## Why the Omarchy build exists

The `ffmpeg` PKGBUILD in `omacom/omarchy-pkgs` (`pkgbuilds/ffmpeg/PKGBUILD`, commit "Ship ffmpeg with NVENC and NVDEC on aarch64", 2026-11-03) is Arch Linux ARM's `2:9.0.2-2` with NVIDIA's encoders and decoders added back for the NVIDIA GB10 and N1x. Its comment says the carry should follow ALARM's version and that "a newer ALARM build replaces this one without them". Nothing on an Apple Silicon Mac uses those encoders.

## Decision

- **On Apple Silicon, install `ffmpeg` from `[extra]`:** `sudo pacman -S extra/ffmpeg`. This repo does not run it. It needs administrator permission and changes a package Omarchy ships, so Jason runs it. After it, the installed `2:9.0.2-5` is newer than the feed's `2:9.0.2-2.1`, so later updates keep it. When the feed publishes a newer build, pacman installs that one.
- **`--ignore libplacebo` alone does not work.** A dry run against fresh sync databases on 2026-11-17 failed with `unable to satisfy dependency 'libplacebo.so=387-64' required by mpv`. `--ignore libplacebo,mpv` does resolve, but it holds back two packages and the same failure returns on the next run.
- **No `IgnorePkg`.** `/etc/pacman.conf` is Omarchy's file, and a held package would drift behind `omarchy update`.
- **No change to `update-all`.** It would have to install or hold a package on a policy Omarchy owns. The failure message already tells the reader to repair the stage and rerun, and the README names the command.
- **Upstream.** A report to `omacom/omarchy-pkgs` would ask for the carry to be limited to the machines that need NVENC, or rebuilt when ALARM's `libplacebo` moves. It is not filed from this repo; it needs Jason's approval of the text first.

## Not done

- The `sudo pacman -S extra/ffmpeg` step has not been run by this change. Record `pacman -Q ffmpeg` and `pacman -Qi ffmpeg` (repository `extra`) in [status](../status.md) after it is run.
- Recheck by 2026-12-15. Remove the status row and the README paragraph when the feed's `ffmpeg` is rebuilt against the current `libplacebo`, or no longer shadows ALARM's.
