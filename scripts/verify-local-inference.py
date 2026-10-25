#!/usr/bin/env python3
"""Check opt-in Ollama Vulkan inference on an Apple Silicon Omarchy machine.

This file does not record a completed M1 run. `preflight` and `download` stop
before inference. `run` is the hardware check: it refuses a CPU fallback, a
missing Honeykrisp device, or an ELF this kernel cannot map.

Shared model-evaluation rules live in groundwork-core, not in this script.
See docs/local-inference.md.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request


MODEL_REPO = 'Qwen/Qwen2.5-0.5B-Instruct-GGUF'
MODEL_REVISION = '9217f5db79a29953eb74d5343926648285ec7e67'
MODEL_FILE = 'qwen2.5-0.5b-instruct-q4_k_m.gguf'
MODEL_SHA256 = '74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db'
MODEL_BYTES = 491400032
MODEL_LICENSE = 'Apache-2.0'
MODEL_URL = (
    f'https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{MODEL_FILE}'
)
LICENSE_URL = (
    f'https://huggingface.co/{MODEL_REPO}/blob/{MODEL_REVISION}/LICENSE'
)
MODEL_NAME = 'groundwork-m1-eval'
PROMPT = 'The capital of France is'
HEADROOM_BYTES = 1024 ** 3
MIN_FREE_BYTES = 2 * 1024 ** 3
SERVER_HOST = '127.0.0.1'
SERVER_PORT = 11434

DEVICE_NAME = re.compile(r'^\s*deviceName\s*=\s*(.+?)\s*$', re.MULTILINE)
LIBRARY = re.compile(r'\blibrary=("?)([A-Za-z0-9_+-]+)\1')


class CheckError(Exception):
    pass


def data_root():
    base = os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local/share')
    return Path(base) / 'groundwork'


def state_root():
    base = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local/state')
    return Path(base) / 'groundwork'


def model_path():
    return data_root() / 'models' / MODEL_FILE


def report_path():
    return state_root() / 'local-inference-report.txt'


def require_aarch64():
    machine = os.uname().machine
    if machine != 'aarch64':
        raise CheckError(
            f'This check is for Apple Silicon Omarchy (aarch64). This machine is {machine}.'
        )


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def model_record():
    return {
        'publisher': 'Qwen',
        'repo': MODEL_REPO,
        'revision': MODEL_REVISION,
        'file': MODEL_FILE,
        'quantization': 'Q4_K_M',
        'license': MODEL_LICENSE,
        'license_url': LICENSE_URL,
        'url': MODEL_URL,
        'bytes': MODEL_BYTES,
        'sha256': MODEL_SHA256,
    }


def honeykrisp_devices(vulkaninfo):
    """Return Apple Honeykrisp device names, or raise CheckError."""
    if 'DRIVER_ID_MESA_HONEYKRISP' not in vulkaninfo and 'honeykrisp' not in vulkaninfo.lower():
        raise CheckError('vulkaninfo does not report Honeykrisp (DRIVER_ID_MESA_HONEYKRISP).')
    names = [line.strip() for line in DEVICE_NAME.findall(vulkaninfo)]
    if not names:
        raise CheckError('vulkaninfo reported Honeykrisp but no deviceName.')
    foreign = [name for name in names if 'Apple' not in name]
    if foreign:
        raise CheckError(f'vulkaninfo lists a device other than Apple: {", ".join(foreign)}.')
    return names


def gpu_libraries(server_log):
    found = []
    for line in server_log.splitlines():
        if 'gpu memory' not in line:
            continue
        match = LIBRARY.search(line)
        if match:
            found.append(match.group(2))
    return found


def assess_logs(vulkaninfo, server_log):
    """Return a proof dict, or raise CheckError when the GPU path is unproven.

    Ollama 0.35 drops integrated GPUs unless OLLAMA_IGPU_ENABLE=1. Apple Silicon
    is unified memory, so a missing flag selects the CPU while Vulkan still
    initializes. A passing log has Honeykrisp, an Apple device, and every
    `gpu memory` library equal to Vulkan.
    """
    devices = honeykrisp_devices(vulkaninfo)
    if 'dropping integrated GPU' in server_log:
        raise CheckError(
            'Ollama dropped the integrated GPU. Set OLLAMA_IGPU_ENABLE=1 and rerun. '
            'A dropped GPU means the model ran on the CPU.'
        )
    libraries = gpu_libraries(server_log)
    if not libraries:
        raise CheckError('The Ollama log has no `gpu memory` line, so the backend is unknown.')
    foreign = [library for library in libraries if library.lower() != 'vulkan']
    if foreign:
        raise CheckError(
            f'Ollama selected a backend other than Vulkan: {", ".join(foreign)}.'
        )
    return {
        'devices': devices,
        'libraries': libraries,
        'acceleration': 'Vulkan / Honeykrisp',
    }


def checkout_revision():
    script_dir = Path(__file__).resolve().parent
    try:
        return subprocess.check_output(
            ['git', '-C', str(script_dir), 'rev-parse', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


def command_environment(models_dir):
    """Environment for the foreground server this script starts."""
    env = os.environ.copy()
    env.update({
        'OLLAMA_HOST': f'{SERVER_HOST}:{SERVER_PORT}',
        'OLLAMA_IGPU_ENABLE': '1',
        'OLLAMA_VULKAN': '1',
        'OLLAMA_LLM_LIBRARY': 'vulkan',
        'OLLAMA_DEBUG': '1',
        'OLLAMA_NO_CLOUD': '1',
        'OLLAMA_MODELS': str(models_dir),
    })
    return env


def pinned_model_status(path):
    if path.is_symlink():
        return 'symlink'
    if not path.is_file():
        return 'absent'
    if path.stat().st_size == MODEL_BYTES and sha256_file(path) == MODEL_SHA256:
        return 'match'
    return 'mismatch'


def print_model_plan():
    record = model_record()
    print('Explicit model download:')
    print(f"  publisher: {record['publisher']}")
    print(f"  repo: {record['repo']}")
    print(f"  revision: {record['revision']}")
    print(f"  file: {record['file']} ({record['quantization']})")
    print(f"  license: {record['license']} ({record['license_url']})")
    print(f"  bytes: {record['bytes']}")
    print(f"  sha256: {record['sha256']}")
    print(f"  url: {record['url']}")
    print(f'  destination: {model_path()}')
    print('No other model is selected by this script.')


def download_model():
    require_aarch64()
    print_model_plan()
    destination = model_path()
    status = pinned_model_status(destination)
    if status == 'symlink':
        raise CheckError(f'Refusing a symlink at {destination}.')
    if status == 'match':
        print(f'Already downloaded and checksum matches: {destination}')
        return destination
    if status == 'mismatch':
        raise CheckError(
            f'{destination} exists and its checksum does not match the pinned file. '
            'Move it aside before downloading again.'
        )
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(parent).free
    needed = MODEL_BYTES + HEADROOM_BYTES
    if free < needed:
        raise CheckError(
            f'Need {needed} bytes free for the model and working space. {parent} has {free}.'
        )
    partial = destination.with_name(destination.name + '.partial')
    if partial.exists() or partial.is_symlink():
        partial.unlink()
    request = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'groundwork-omarchy-local-inference'})
    print('Downloading the pinned file.')
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open('wb') as output:
            length = response.headers.get('Content-Length')
            if length is not None and int(length) != MODEL_BYTES:
                raise CheckError(f'Content-Length is {length}; the pinned file is {MODEL_BYTES} bytes.')
            copied = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > MODEL_BYTES:
                    raise CheckError(f'Download exceeded {MODEL_BYTES} bytes.')
                output.write(chunk)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    if partial.stat().st_size != MODEL_BYTES or sha256_file(partial) != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        raise CheckError('Downloaded bytes do not match the pinned size and sha256.')
    os.replace(partial, destination)
    print(f'Checksum matches: {destination}')
    return destination


def mem_available():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                kib = int(line.split()[1])
                return kib * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def port_open():
    try:
        with socket.create_connection((SERVER_HOST, SERVER_PORT), timeout=0.2):
            return True
    except OSError:
        return False


def elf_files():
    files = []
    binary = shutil.which('ollama')
    if binary:
        files.append(Path(binary))
    vulkan = Path('/usr/lib/ollama/vulkan')
    if vulkan.is_dir():
        files.extend(path for path in sorted(vulkan.iterdir()) if path.is_file() and not path.is_symlink())
    return files


def alignment_problems():
    script = Path(__file__).resolve().parent / 'groundwork-update.py'
    problems = []
    checked = 0
    for path in elf_files():
        result = subprocess.run(
            [sys.executable, str(script), 'check-elf-alignment', str(path)],
            capture_output=True, text=True,
        )
        if result.returncode == 2:
            continue
        if result.returncode != 0:
            problems.append(result.stderr.strip() or f'{path} failed the alignment check.')
        else:
            checked += 1
    if checked < 2:
        problems.append(
            'Expected an aligned ollama executable and at least one ELF under /usr/lib/ollama/vulkan.'
        )
    return problems


def preflight():
    require_aarch64()
    print('Local inference preflight. This command does not download a model or run inference.')
    print('This repository has no completed M1 GPU run. preflight passing is not that run.')
    print(f'machine: {os.uname().machine}')
    print(f'kernel: {" ".join(os.uname())}')
    compatible = Path('/proc/device-tree/compatible')
    if compatible.is_file():
        print('device-tree:', compatible.read_bytes().replace(b'\x00', b' ').decode(errors='replace').strip())
    if shutil.which('pacman'):
        for package in ('ollama', 'ollama-vulkan', 'vulkan-aurora', 'vulkan-asahi'):
            result = subprocess.run(['pacman', '-Q', package], capture_output=True, text=True)
            detail = (result.stdout or result.stderr).strip()
            print(f'package {package}: {detail or "not installed"}')
    else:
        print('pacman is absent, so package versions were not queried.')
    icd_dir = Path('/usr/share/vulkan/icd.d')
    icds = sorted(path.name for path in icd_dir.glob('*asahi*.json') if path.is_file() and not path.is_symlink()) if icd_dir.is_dir() else []
    print('asahi ICDs:', ', '.join(icds) if icds else 'none')
    problems = []
    if not icds:
        problems.append('No regular Asahi Vulkan ICD in /usr/share/vulkan/icd.d.')
    if not shutil.which('ollama'):
        problems.append('ollama is not on PATH. Install with ./install.sh --with-local-inference.')
    if not shutil.which('vulkaninfo'):
        problems.append('vulkaninfo is not on PATH. ./install.sh --with-local-inference installs vulkan-tools.')
    problems.extend(alignment_problems())
    vulkan_text = ''
    if shutil.which('vulkaninfo'):
        result = subprocess.run(['vulkaninfo', '--summary'], capture_output=True, text=True, timeout=60)
        vulkan_text = result.stdout + '\n' + result.stderr
        print('--- vulkaninfo --summary ---')
        print(vulkan_text.rstrip())
        print('--- end vulkaninfo ---')
        try:
            devices = honeykrisp_devices(vulkan_text)
        except CheckError as error:
            problems.append(str(error))
        else:
            print('Honeykrisp devices:', ', '.join(devices))
    destination = model_path()
    status = pinned_model_status(destination)
    print(f'model: {destination} ({status})')
    if status == 'absent':
        print_model_plan()
    if problems:
        print('Preflight failed:', file=sys.stderr)
        for problem in problems:
            print(f'- {problem}', file=sys.stderr)
        return 1
    print('Runtime preflight passed. GPU inference has not been run.')
    print('Next: python3 scripts/verify-local-inference.py download')
    print('Then: python3 scripts/verify-local-inference.py run')
    return 0


def wait_for_server(process, deadline):
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CheckError(f'ollama serve exited {process.returncode} before it accepted connections.')
        if port_open():
            return
        time.sleep(0.2)
    raise CheckError('ollama serve did not open 127.0.0.1:11434 before the timeout.')


def api_generate():
    payload = json.dumps({
        'model': MODEL_NAME,
        'prompt': PROMPT,
        'stream': False,
        'options': {'num_predict': 32, 'temperature': 0, 'num_ctx': 512},
    }).encode()
    request = urllib.request.Request(
        f'http://{SERVER_HOST}:{SERVER_PORT}/api/generate',
        data=payload,
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode())


def write_report(fields):
    path = report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        'local inference hardware report',
        'prior_repository_record: none. This file is the record of the run that wrote it.',
        f'checkout: {checkout_revision()}',
    ]
    for key, value in fields.items():
        lines.append(f'{key}: {value}')
    text = '\n'.join(lines) + '\n'
    temporary = path.with_name(path.name + '.partial')
    temporary.write_text(text)
    os.replace(temporary, path)
    print(text)
    print(f'Report: {path}')
    return path


def run_inference():
    require_aarch64()
    destination = model_path()
    status = pinned_model_status(destination)
    if status == 'absent':
        raise CheckError(f'Model file is absent. Run download first. Expected {destination}.')
    if status != 'match':
        raise CheckError('Model file does not match the pinned size and sha256. Download was not repeated.')
    problems = alignment_problems()
    if problems:
        raise CheckError(' '.join(problems))
    if not shutil.which('vulkaninfo'):
        raise CheckError('vulkaninfo is missing.')
    vulkan = subprocess.run(['vulkaninfo', '--summary'], capture_output=True, text=True, timeout=60)
    vulkan_text = vulkan.stdout + '\n' + vulkan.stderr
    honeykrisp_devices(vulkan_text)
    available = mem_available()
    if available is not None and available < MIN_FREE_BYTES:
        raise CheckError(
            f'MemAvailable is {available} bytes. Refusing to load a model with under {MIN_FREE_BYTES} bytes free.'
        )
    if port_open():
        raise CheckError(
            f'{SERVER_HOST}:{SERVER_PORT} is already open. Stop that Ollama server before this check so a CPU server cannot answer it.'
        )
    models_dir = data_root() / 'ollama-models'
    models_dir.mkdir(parents=True, exist_ok=True)
    modelfile = data_root() / 'Modelfile.groundwork-m1-eval'
    modelfile.write_text(f'FROM {destination}\nPARAMETER num_ctx 512\nPARAMETER temperature 0\n')
    env = command_environment(models_dir)
    log_path = state_root() / 'ollama-serve.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print('Starting a foreground ollama serve on 127.0.0.1:11434. The system service is not used.')
    print('OLLAMA_IGPU_ENABLE=1 OLLAMA_VULKAN=1 OLLAMA_LLM_LIBRARY=vulkan OLLAMA_NO_CLOUD=1')
    with log_path.open('w') as log_handle:
        process = subprocess.Popen(
            ['ollama', 'serve'],
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            wait_for_server(process, time.monotonic() + 45)
            create = subprocess.run(
                ['ollama', 'create', MODEL_NAME, '-f', str(modelfile)],
                env=env, capture_output=True, text=True, timeout=180,
            )
            if create.returncode != 0:
                raise CheckError(f'ollama create failed: {create.stderr or create.stdout}')
            started = time.perf_counter()
            body = api_generate()
            elapsed = time.perf_counter() - started
            log_handle.flush()
            server_log = log_path.read_text(errors='replace')
            proof = assess_logs(vulkan_text, server_log)
            version = subprocess.run(['ollama', '--version'], env=env, capture_output=True, text=True, timeout=20)
            rss = ''
            status = Path(f'/proc/{process.pid}/status')
            if status.is_file():
                for line in status.read_text().splitlines():
                    if line.startswith('VmRSS:') or line.startswith('VmHWM:'):
                        rss += line + ' '
            eval_count = body.get('eval_count')
            eval_duration = body.get('eval_duration')
            tokens_per_second = ''
            if isinstance(eval_count, int) and isinstance(eval_duration, int) and eval_duration > 0:
                tokens_per_second = f'{eval_count / (eval_duration / 1e9):.3f}'
            fields = {
                'result': 'gpu',
                'date': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'uname': ' '.join(os.uname()),
                'runtime': (version.stdout or version.stderr).strip(),
                'acceleration': proof['acceleration'],
                'devices': ', '.join(proof['devices']),
                'gpu_memory_libraries': ', '.join(proof['libraries']),
                'model_repo': MODEL_REPO,
                'model_revision': MODEL_REVISION,
                'model_file': MODEL_FILE,
                'model_sha256': MODEL_SHA256,
                'model_license': MODEL_LICENSE,
                'task': PROMPT,
                'output': json.dumps(body.get('response', '')),
                'wall_seconds': f'{elapsed:.3f}',
                'prompt_eval_count': body.get('prompt_eval_count', ''),
                'prompt_eval_duration_ns': body.get('prompt_eval_duration', ''),
                'eval_count': eval_count if eval_count is not None else '',
                'eval_duration_ns': eval_duration if eval_duration is not None else '',
                'eval_tokens_per_second': tokens_per_second,
                'mem_available_before_bytes': available if available is not None else '',
                'server_memory': rss.strip(),
                'ollama_host': env['OLLAMA_HOST'],
                'ollama_igpu_enable': env['OLLAMA_IGPU_ENABLE'],
                'ollama_llm_library': env['OLLAMA_LLM_LIBRARY'],
                'ollama_no_cloud': env['OLLAMA_NO_CLOUD'],
                'server_log': str(log_path),
            }
            write_report(fields)
            print('GPU proof passed for this run. Keep the report with the machine notes.')
            return 0
        except Exception as error:
            log_handle.flush()
            server_log = log_path.read_text(errors='replace') if log_path.exists() else ''
            fields = {
                'result': 'failed',
                'date': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'uname': ' '.join(os.uname()),
                'error': str(error),
                'server_log': str(log_path),
                'server_log_tail': server_log[-2000:].replace('\n', ' | '),
            }
            write_report(fields)
            if isinstance(error, CheckError):
                raise
            raise CheckError(str(error)) from error
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, 15)
                except OSError:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, 9)
                    except OSError:
                        process.kill()
                    process.wait(timeout=5)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('preflight', help='Check the runtime and Honeykrisp. Does not run a model.')
    commands.add_parser('download', help='Download the one pinned GGUF after printing its license and size.')
    commands.add_parser('run', help='Run one GPU proof on this machine and write a report.')
    assess = commands.add_parser('assess', help='Assess saved vulkaninfo and server logs.')
    assess.add_argument('--vulkaninfo', type=Path, required=True)
    assess.add_argument('--server-log', type=Path, required=True)
    return parser


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == 'preflight':
            return preflight()
        if args.command == 'download':
            download_model()
            return 0
        if args.command == 'run':
            return run_inference()
        if args.command == 'assess':
            proof = assess_logs(args.vulkaninfo.read_text(), args.server_log.read_text())
            print(json.dumps(proof))
            return 0
    except CheckError as error:
        print(error, file=sys.stderr)
        return 1
    parser.error(f'Unknown command {args.command}')
    return 64


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
