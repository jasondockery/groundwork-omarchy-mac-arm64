# Local inference on Apple Silicon Omarchy

Shared rules for choosing a model, reading its license, judging hardware fit, and keeping prompts on the machine are in groundwork-core: [issue #18](https://github.com/jasondockery/groundwork-core/issues/18) and [pull request #19](https://github.com/jasondockery/groundwork-core/pull/19) (`docs/local-models.md` on `codex/local-model-guidance`). This page is only the Apple Silicon Omarchy runtime. The decision is [0012](decisions/0012-local-inference-opt-in.md).

**Hardware status: unverified.** Nothing in this repository is a completed M1 GPU run. Package installation does not prove the GPU. The script below is the check, and it has not been executed on the reference Mac mini.

## What is installed

`./install.sh` and `update-all` leave local inference off. An existing install is left in place when you omit the flag.

```bash
./install.sh --with-local-inference
```

That installs three packages from the feeds Omarchy already configured:

- `ollama` 0.35.0-1 and `ollama-vulkan` 0.35.0-1 from the Omarchy edge aarch64 database. The base package ships without Vulkan libraries. Both packages are required.
- `vulkan-tools`, for `vulkaninfo`, when that command is missing.

It does not download a model, enable `ollama.service`, change Hyprland, or change provider logins. It refuses to continue when `/usr/share/vulkan/icd.d/` has no regular Asahi ICD. On this desktop that ICD belongs to `vulkan-aurora` (Honeykrisp). `vulkan-aurora` provides `vulkan-asahi` and conflicts with the Arch Linux ARM `vulkan-asahi` package, so this layer does not install a second Mesa.

`omarchy-mlx` is published in the same edge database and is not installed. It carries a private Honeykrisp ICD and a Python 3.14 environment. `llama-cpp` / `ggml-vulkan` are x86_64 packages in Arch extra and are absent from Arch Linux ARM and from the Omarchy edge aarch64 database (checked 2026-10-25).

Ollama 0.35 drops integrated GPUs unless `OLLAMA_IGPU_ENABLE=1`. The M1 GPU is unified memory, so a server started without that variable can report a successful answer that never touched the GPU. The system unit shipped by the package does not set the variable. Leave it disabled.

On a 16 KiB-page kernel, setup also refuses the preference when `ollama` or the Vulkan library has a LOAD alignment below the page size (`readelf -lW`). Those published binaries have not been checked on the M1 yet. A refusal prints:

```bash
sudo pacman -Rns ollama-vulkan ollama
```

## Prove the GPU

Run these on the Omarchy Mac, from this checkout. They are the acceptance run. Until `run` prints `GPU proof passed` and writes a report whose `result` is `gpu`, this path is unverified.

```bash
cd ~/code/groundwork-omarchy-mac-arm64
python3 scripts/verify-local-inference.py preflight
python3 scripts/verify-local-inference.py download
python3 scripts/verify-local-inference.py run
```

`preflight` prints the device tree, package versions, `vulkaninfo --summary`, and LOAD alignment. It does not load a model.

`download` fetches one file and checks its sha256:

| Field | Value |
|---|---|
| Publisher | Qwen |
| Repository | `Qwen/Qwen2.5-0.5B-Instruct-GGUF` |
| Revision | `9217f5db79a29953eb74d5343926648285ec7e67` |
| File | `qwen2.5-0.5b-instruct-q4_k_m.gguf` |
| Quantization | Q4_K_M |
| License | Apache-2.0 ([LICENSE](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/blob/9217f5db79a29953eb74d5343926648285ec7e67/LICENSE)) |
| Size | 491400032 bytes |
| sha256 | `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db` |

The file lands in `~/.local/share/groundwork/models/` (or `$XDG_DATA_HOME/groundwork/models/`). The script asks for that size plus 1 GiB free. It will not replace a file whose checksum differs.

`run` refuses to start when `MemAvailable` is under 2 GiB, when `127.0.0.1:11434` is already open, or when the ELFs are misaligned. It then starts a foreground `ollama serve` with:

```text
OLLAMA_HOST=127.0.0.1:11434
OLLAMA_IGPU_ENABLE=1
OLLAMA_VULKAN=1
OLLAMA_LLM_LIBRARY=vulkan
OLLAMA_DEBUG=1
OLLAMA_NO_CLOUD=1
```

The model name is `groundwork-m1-eval`. Context is 512. The task is the prompt `The capital of France is`. A passing run requires all of:

- `vulkaninfo` names Honeykrisp (`DRIVER_ID_MESA_HONEYKRISP` or driver name `honeykrisp`) and every `deviceName` contains `Apple`.
- The server log has at least one `gpu memory` line, and every such line has `library=Vulkan`.
- The log does not contain `dropping integrated GPU`.

The report is `~/.local/state/groundwork/local-inference-report.txt`. It records the checkout, runtime version, device, license, sha256, prompt output, wall time, `eval_count`, `eval_duration_ns`, tokens per second when both counts exist, `MemAvailable` before the load, and the server `VmRSS`. Copy that file into your machine notes. This git repo should not gain the report, or any Tailscale address that might be sitting in a log.

A failure still writes the report with `result: failed`. That includes a timeout, a CPU library line, and a missing Honeykrisp device. Treat a failed report as "GPU inference unsupported on this boot" and keep the log `~/.local/state/groundwork/ollama-serve.log`.

## Update

`omarchy update` (including the copy `update-all` already runs) is the package update. The Omarchy recipe currently sets `skip_build: true` and the published aarch64 build is 0.35.0-1. This layer does not compile a newer Ollama. After any change to `ollama`, `ollama-vulkan`, `vulkan-aurora`, or the kernel, run `preflight` and `run` again. Weights stay at the pinned file until you delete them.

## Stop

The verification script stops the server it started when the run ends or is interrupted. To stop a server you enabled yourself:

```bash
sudo systemctl stop ollama
```

`OLLAMA_HOST` for any server you keep should stay `127.0.0.1:11434`, and `OLLAMA_IGPU_ENABLE=1` has to be set in that service or the GPU is dropped.

## Remove

```bash
ollama rm groundwork-m1-eval
rm -rf ~/.local/share/groundwork/models \
       ~/.local/share/groundwork/ollama-models \
       ~/.local/share/groundwork/Modelfile.groundwork-m1-eval \
       ~/.local/state/groundwork/ollama-serve.log \
       ~/.local/state/groundwork/local-inference-report.txt
sudo systemctl disable --now ollama
sudo pacman -Rns ollama-vulkan ollama vulkan-tools
./install.sh --without-local-inference
```

`./install.sh --without-local-inference` stores the preference as off. The `pacman` and `rm` commands remove the packages and the downloaded weights. `vulkan-tools` can stay installed if you still want `vulkaninfo`. `vulkan-aurora` stays; it is the desktop driver.
