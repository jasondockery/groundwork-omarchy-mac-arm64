# 0012. Opt-in local inference on Apple Silicon

Checked 2026-10-25. Shared evaluation rules (provenance, license, hardware fit, privacy, explicit downloads, and removal) stay in [groundwork-core#18](https://github.com/jasondockery/groundwork-core/issues/18), written up in [groundwork-core#19](https://github.com/jasondockery/groundwork-core/pull/19) as [`docs/local-models.md`](https://github.com/jasondockery/groundwork-core/blob/codex/local-model-guidance/docs/local-models.md). This decision owns the Omarchy-on-Apple-Silicon runtime only. M5 support is [issue #37](https://github.com/jasondockery/groundwork-omarchy-mac-arm64/issues/37) and is out of scope. macOS MLX and `groundwork-mac` stay unused here ([0001](0001-do-not-apply-groundwork-on-omarchy.md)).

No live M1 GPU measurement is recorded in this repository. `./install.sh` does not install a runtime unless `--with-local-inference` is passed, and that flag still downloads no weights. The hardware record is produced only by `scripts/verify-local-inference.py run` on the machine.

## What the Apple Silicon stack actually ships

The reference pacman order is `[omarchy]` edge, `[asahi-alarm]`, then `core`, `extra`, `alarm`, and `aur`. On 2026-10-25:

| Candidate | Where it exists | Adopted |
|---|---|---|
| `llama-cpp` 0.6.0-1 plus `ggml-vulkan` 0.26.0-1 | Arch Linux extra, x86_64 only ([llama-cpp](https://archlinux.org/packages/extra/x86_64/llama-cpp/), [ggml-vulkan](https://archlinux.org/packages/extra/x86_64/ggml-vulkan/)). ArchWiki tells you to install both. | No. `https://archlinuxarm.org/packages/aarch64/llama-cpp`, `ggml`, and `ggml-vulkan` return 404. The Omarchy edge aarch64 database (221 packages) has neither name. |
| Upstream llama.cpp "Ubuntu arm64 (Vulkan)" tarball | ggml-org/llama.cpp releases | No. It is a foreign glibc build, outside the Omarchy feed, and Apple Silicon kernels use 16 KiB pages. |
| `ollama` 0.40.10-1 and `ollama-vulkan` 0.40.10-1 | Arch Linux extra, x86_64 ([ollama-vulkan](https://archlinux.org/packages/extra/x86_64/ollama-vulkan/)), built 2026-10-24 | No. The aarch64 search API returns no package. Arch Linux ARM has no `ollama` page. |
| `ollama` 0.35.0-1 and `ollama-vulkan` 0.35.0-1 | Omarchy edge aarch64, built by `Omarchy <pkgs@omarchy.org>`. Recipe: [omarchy-pkgs `pkgbuilds/ollama`](https://github.com/omacom/omarchy-pkgs/tree/master/pkgbuilds/ollama). `arch=(aarch64)`. Upstream tag `v0.35.0`, MIT. `OLLAMA_LLAMA_BACKENDS="cuda_v13;vulkan"`. The base package deletes the Vulkan libraries; `ollama-vulkan` puts them back and depends on `ollama` and `vulkan-icd-loader`. | Yes, opt-in only. Both packages are required. The recipe's `.omarchy/package.json` sets `skip_build: true`, so this feed is still on 0.35.0 while Arch's x86_64 package is 0.40.10. |
| `omarchy-mlx` 0.7.28-4 and `omarchy-mlx-vulkan` 0.7.28-4 | Omarchy edge aarch64. Source [joshuaswarren/omarchy-mlx](https://github.com/joshuaswarren/omarchy-mlx) tag `v0.7.28` (MIT), plus a private Honeykrisp build from `joshuaswarren/mesa-1` commit `e7631595df62`. Installed size about 740 MiB. The ICD is `/usr/lib/omarchy-mlx/vulkan/honeykrisp_icd.aarch64.json` and is kept out of the loader search path. | No. Reviewed and left for a later explicit trial. |
| `omarchy-mac-ml` 1-1 | Omarchy edge aarch64. Depends on `omarchy-mlx`, `omarchy-mlx-vulkan`, `omarchy-ane-dkms`, and `mil-hwx-compiler`. | No. |
| `vulkan-aurora` `1:26.3.0_devel.20261017.af3d9c6fc84-2` | Omarchy edge aarch64, group `omarchy-platform-apple-silicon`. Provides and conflicts with `vulkan-asahi`. Built from [aurora-silicon/mesa](https://github.com/aurora-silicon/mesa) commit `af3d9c6fc84` with `-Dvulkan-drivers=asahi`. | Already the desktop driver. This layer does not reinstall it. |
| `vulkan-asahi` 1:26.2.5-1 | Arch Linux ARM extra aarch64 | Already provided by `vulkan-aurora` on this install. Installing the ALARM package conflicts with `vulkan-aurora`. |
| `vulkan-tools` 1.4.363.0-1 | Arch Linux ARM extra aarch64 (`vulkaninfo`) | Installed only with the opt-in, so the hardware check can name the device. |

`omarchy-mlx` has a real upstream tag, sha256-pinned wheels, a 24-hour release delay, and an MIT license. It also installs a second Honeykrisp, a Python 3.14 venv, and an optional out-of-tree Neural Engine module. Asahi's own M1 table still lists the Neural Engine as out of tree ([M1 feature support](https://asahilinux.org/docs/platform/feature-support/m1/)). A private Mesa is a different driver from the desktop `vulkan-aurora` the session is using. This layer does not install it.

## GPU facts, and what is still unchecked

Asahi's M1 (T8103) feature table lists the GPU as `linux-asahi`. The reference machine runs `linux-aurora`, which carries that GPU driver. Honeykrisp is Mesa's Vulkan driver for that GPU. Asahi announced conformant Vulkan 1.3 in June 2024 ([Vulkan 1.3 on the M1](https://asahilinux.org/2024/06/vk13-on-the-m1-in-1-month/)). Fedora Asahi Remix documents conformant Vulkan 1.4, OpenGL 4.6, and OpenCL 3.0 ([Fedora Asahi Remix](https://asahilinux.org/fedora/)). That sentence describes Fedora's mesa, not the `vulkan-aurora` package on this Omarchy image.

Llama.cpp's Vulkan backend runs compute shaders. Upstream merged an Asahi-specific matmul change on 2026-06-30 ([llama.cpp#24663](https://github.com/ggml-org/llama.cpp/pull/24663)). Ollama 0.35.0 vendors llama.cpp `b11081` (commit `161755f29e41`, 2026-09-21), which is later than that merge. This repository has not run either backend on the M1, so no tokens-per-second number from another machine is recorded here.

Ollama 0.35 will silently use the CPU on this hardware if the integrated GPU is dropped. `filterIntegratedGPUs` in `discover/runner.go` keeps integrated GPUs on darwin/arm64 and otherwise drops them unless `OLLAMA_IGPU_ENABLE=1`. The default allow-list is CUDA and a few ROCm targets, not Vulkan. Apple's GPU is unified memory (`uma: 1`). The verification script sets `OLLAMA_IGPU_ENABLE=1`, `OLLAMA_VULKAN=1`, and `OLLAMA_LLM_LIBRARY=vulkan`, and it fails the run when the log contains `dropping integrated GPU` or a `gpu memory` line whose library is anything other than Vulkan.

The published unit file runs `ollama serve` as user `ollama` with `OLLAMA_MODELS=/var/lib/ollama` and does not set the integrated-GPU flag. Ollama's own default bind is `127.0.0.1:11434` (`envconfig.Host` in v0.35.0). Setup does not enable `ollama.service`. The hardware check starts a foreground server with `OLLAMA_HOST=127.0.0.1:11434` and `OLLAMA_NO_CLOUD=1`.

Apple Silicon kernels use 16 KiB pages. Omarchy builds ordinary aarch64 packages on GitHub's `ubuntu-24.04-arm` runners, which are 4 KiB machines. Setup and the verification script both refuse to execute an ELF whose `readelf -lW` LOAD alignment is below the kernel page size. That check has not been executed against the published `ollama` 0.35.0-1 binaries.

## What setup changes

| Setting | Default | With `--with-local-inference` |
|---|---|---|
| `ollama` and `ollama-vulkan` | absent | installed from the existing Omarchy feed |
| `vulkan-tools` (`vulkaninfo`) | absent | installed when the command is missing |
| `vulkan-aurora` / desktop Mesa | unchanged | unchanged |
| `ollama.service` | disabled | still not enabled |
| Model weights | absent | still absent until `verify-local-inference.py download` |
| Hyprland, keyd, providers, package feed | unchanged | unchanged |
| `~/.config/groundwork/omarchy.json` key `local_inference` | missing or false, both mean off | `true` only after the packages pass the ICD and alignment checks |

A saved file from before this choice existed stays off. `--without-local-inference` stores false. Package and model removal is in [local inference](../local-inference.md). A missing Asahi ICD aborts before `omarchy pkg add` and before the preference is saved. `vulkan-aurora` supplies that ICD; this layer does not install a second driver.

The one model the checker will download is Qwen2.5 0.5B Instruct, Q4_K_M, from `Qwen/Qwen2.5-0.5B-Instruct-GGUF` revision `9217f5db79a29953eb74d5343926648285ec7e67` (Apache-2.0). The file is `qwen2.5-0.5b-instruct-q4_k_m.gguf`, 491400032 bytes, sha256 `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`. Context is pinned to 512. The reference Mac mini has 16 GB, shared by the desktop and the GPU.
