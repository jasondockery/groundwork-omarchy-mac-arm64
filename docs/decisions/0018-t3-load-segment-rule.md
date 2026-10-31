# 0018. T3 Code LOAD segments: page-aligned offset and address, then a smoke launch

Checked 2026-10-31 on the reference M1 mini (16 KiB pages) against the linux-arm64 T3 Code nightlies 0.1.5-nightly.20261029.3873, 0.1.6-nightly.20261030.3917 and 0.1.6-nightly.20261031.3956 (issue #102).

## Problem

On kernels with pages larger than 4 KiB, `update-all` checks every ELF file in a T3 Code nightly with `readelf -lW` before it smoke-runs the build (README, "pingdotgg/t3code#23433"). Until this decision that check refused any file with a LOAD whose `p_align` was below the page size. Every nightly since 0.1.3 was refused and written to `t3-rejected.json`, and the mini stayed on 0.1.2-nightly.20261021.3457.

In those nightlies, every ELF file but one passes the old rule. The `t3` executable has one LOAD with `p_align 0x1000`: the last segment, which holds bun's embedded payload and carries the NOTE. In 3917 and 3956 it is `offset=0x8f80000 vaddr=0x10f60000 align=0x1000`; in 3873, `offset=0x8f60000 vaddr=0x10f60000 align=0x1000`. Both values are multiples of 64 KiB.

## What the kernel needs

`execve` (and `ld.so` for shared objects) maps each LOAD with mmap. mmap maps whole pages. It needs `p_offset` congruent to `p_vaddr` modulo the page size, so that one page of the file lands on one page of memory, and the mapping starts at the page boundary below `p_vaddr`. The loader does not read `p_align` for that. For a position-independent file, `p_align` only sets how far the load address is rounded, and that is never less than a page. `p_align` is what the linker promised. It is advisory.

So a LOAD with `p_align 0x1000` is mappable on 16 KiB pages when its offset and address are both 16 KiB multiples. What a 4 KiB `p_align` normally signals is that the linker packed segments into 4 KiB pages. Then two segments with different permissions share one 16 KiB page, and the later mapping replaces part of the earlier one. That is the crash in pingdotgg/t3code#23433. A segment that starts on its own page boundary cannot share its first page with the segment before it.

## Decision

`t3_load_segment_mappable(offset, vaddr, align, page)` in `scripts/groundwork-update.py` is the rule for T3 candidates (`misaligned_elf_files`):

- A LOAD with `p_align >= page` passes. That is the old rule, unchanged.
- A LOAD with a smaller `p_align` passes only when `p_offset % page == 0` and `p_vaddr % page == 0`. Being congruent alone is not enough: an offset and address that agree modulo the page but do not start on a page boundary (`0x6dad000`/`0x71ad000` with a 4 KiB `p_align`) is the packed layout above, and is still refused.

Passing this check is not acceptance. A build that passes is still smoke-run under bubblewrap with `t3 --version`, as before, and is installed only when that run prints the candidate. A signal death in that run still writes the version to `t3-rejected.json`. A timeout or another error still skips that run without remembering the version. The hold, the exact-version install, and the repair to `0.1.2-nightly.20261021.3457` are unchanged. `execve` maps every LOAD of `t3` before `--version` runs, so a mapping failure in the main binary shows up in the smoke run. The smoke run does not load native addons (`*.node`, `libfff_c.so`). Those must pass this check by themselves, and in the three nightlies above they all pass the old rule.

The relaxed rule is only for T3, which has that smoke launch. `bun_alignment_problem` (the Blip bun preflight) and `elf_alignment_problem` (Bitwarden, Brave, local inference) keep `p_align >= page`.

## Evidence on the mini, 2026-10-31

The `linux-arm64.tar.gz` of each nightly was checksum-verified against its `SHA256SUMS` and checked with the new rule. Then the updater's own `t3_candidate_check` ran with the release list and download pointed at those files, a temporary `HOME`, `XDG_*` and state directory, and the real bubblewrap smoke run.

| Nightly | ELF files | Fail the old rule | Fail the new rule | Smoke (`bwrap … t3 --version`) | Result |
|---|---|---|---|---|---|
| 0.1.5-nightly.20261029.3873 | 11 | `t3` | none | exit 0, `t3 v0.1.5-nightly.20261029.3873` | would install |
| 0.1.6-nightly.20261030.3917 | 12 | `t3` | none | exit 0, `t3 v0.1.6-nightly.20261030.3917` | would install |
| 0.1.6-nightly.20261031.3956 | 12 | `t3` | none | exit 0, `t3 v0.1.6-nightly.20261031.3956` | would install |

The desktop `arm64.AppImage` of the same nightlies is not checked or installed by the updater. Its runtime and its arm64 ELF files pass the old rule. Its one failing file is node-pty's `prebuilds/linux-x64/pty.node`, an x86-64 file that never loads on arm64.

## Versions already in t3-rejected.json

The file records versions only, not why each was rejected. Versions refused by the old rule stay refused, because the updater cannot tell them apart from smoke crashes. That covers 0.1.6-nightly.20261031.3956 on the mini. The next nightly published after this change is checked with the new rule. To check a version already in the file again, delete that version from the `"16384"` list in `~/.local/state/groundwork/t3-rejected.json` and run `update-all`.
