"""Opt-in local inference: log assessment, the pinned model, and saved preference."""
import hashlib
import importlib.util
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('groundwork_local_inference_updater', REPO / 'scripts/groundwork-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)
verify_spec = importlib.util.spec_from_file_location('verify_local_inference', REPO / 'scripts/verify-local-inference.py')
verify = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(verify)


VULKANINFO = """
GPU0:
    deviceName         = Apple M1 (G13G B1)
    driverName         = honeykrisp
    driverID           = DRIVER_ID_MESA_HONEYKRISP
"""

GPU_LOG = """
system memory total="15.0 GiB" free="8.0 GiB" free_swap="0 B"
gpu memory id=0 library=Vulkan available="6.0 GiB" free="7.0 GiB" minimum="457.0 MiB" overhead="0 B"
ggml_vulkan: 0 = Apple M1 (G13G B1) | uma: 1 | fp16: 1 |
"""


class AssessTests(unittest.TestCase):
    def test_honeykrisp_vulkan_log_is_a_gpu_proof(self):
        proof = verify.assess_logs(VULKANINFO, GPU_LOG)
        self.assertEqual(proof['libraries'], ['Vulkan'])
        self.assertIn('Apple M1', proof['devices'][0])
        self.assertEqual(proof['acceleration'], 'Vulkan / Honeykrisp')

    def test_dropped_integrated_gpu_is_a_cpu_fallback(self):
        log = 'dropping integrated GPU; to enable, set OLLAMA_IGPU_ENABLE=1 library=Vulkan\n' + GPU_LOG
        with self.assertRaisesRegex(verify.CheckError, 'dropped the integrated GPU'):
            verify.assess_logs(VULKANINFO, log)

    def test_cpu_library_line_fails(self):
        log = 'gpu memory id=0 library=CPU available="8.0 GiB"\n'
        with self.assertRaisesRegex(verify.CheckError, 'other than Vulkan'):
            verify.assess_logs(VULKANINFO, log)

    def test_missing_gpu_memory_line_fails(self):
        with self.assertRaisesRegex(verify.CheckError, 'no `gpu memory` line'):
            verify.assess_logs(VULKANINFO, 'listening on 127.0.0.1:11434\n')

    def test_llvmpipe_device_fails(self):
        text = VULKANINFO + 'deviceName = llvmpipe (LLVM 18.1.8)\n'
        with self.assertRaisesRegex(verify.CheckError, 'other than Apple'):
            verify.assess_logs(text, GPU_LOG)

    def test_missing_honeykrisp_fails(self):
        text = 'deviceName = Apple M1 (G13G B1)\ndriverName = lavapipe\n'
        with self.assertRaisesRegex(verify.CheckError, 'Honeykrisp'):
            verify.assess_logs(text, GPU_LOG)

    def test_server_environment_pins_localhost_and_the_igpu_flag(self):
        env = verify.command_environment(Path('/tmp/models'))
        self.assertEqual(env['OLLAMA_HOST'], '127.0.0.1:11434')
        self.assertEqual(env['OLLAMA_IGPU_ENABLE'], '1')
        self.assertEqual(env['OLLAMA_VULKAN'], '1')
        self.assertEqual(env['OLLAMA_LLM_LIBRARY'], 'vulkan')
        self.assertEqual(env['OLLAMA_NO_CLOUD'], '1')
        self.assertEqual(env['OLLAMA_MODELS'], '/tmp/models')


class ModelPinTests(unittest.TestCase):
    def test_pinned_identity(self):
        record = verify.model_record()
        self.assertEqual(record['license'], 'Apache-2.0')
        self.assertEqual(record['bytes'], 491400032)
        self.assertEqual(record['sha256'], '74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db')
        self.assertIn(record['revision'], record['url'])
        self.assertTrue(record['url'].startswith('https://huggingface.co/'))

    def test_checksum_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'model.gguf'
            self.assertEqual(verify.pinned_model_status(path), 'absent')
            path.write_bytes(b'abc')
            digest = hashlib.sha256(b'abc').hexdigest()
            with patch.object(verify, 'MODEL_BYTES', 3), patch.object(verify, 'MODEL_SHA256', digest):
                self.assertEqual(verify.pinned_model_status(path), 'match')
            self.assertEqual(verify.pinned_model_status(path), 'mismatch')
            link = Path(temporary) / 'link.gguf'
            link.symlink_to(path)
            self.assertEqual(verify.pinned_model_status(link), 'symlink')


# Report another architecture, so the refusal is tested on any host. On the
# Apple Silicon machine itself the real command would download the model.
FOREIGN_MACHINE = """
import os, runpy, sys
real = os.uname()
os.uname = lambda: os.uname_result((real.sysname, real.nodename, real.release, real.version, 'x86_64'))
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""


class CommandTests(unittest.TestCase):
    def script(self, *args, prefix=()):
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, XDG_DATA_HOME=f'{temporary}/data', XDG_STATE_HOME=f'{temporary}/state')
            return subprocess.run(
                [sys.executable, *prefix, str(REPO / 'scripts/verify-local-inference.py'), *args],
                capture_output=True, text=True, timeout=10, env=env,
            )

    def test_a_foreign_machine_is_refused_before_download_or_inference(self):
        foreign = ('-c', FOREIGN_MACHINE)
        preflight = self.script('preflight', prefix=foreign)
        download = self.script('download', prefix=foreign)
        run = self.script('run', prefix=foreign)
        for result in (preflight, download, run):
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('aarch64', result.stderr)
        self.assertNotIn('Downloading', download.stdout)

    def test_assess_command_accepts_a_saved_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            vulkaninfo = directory / 'vulkaninfo.txt'
            server_log = directory / 'server.log'
            vulkaninfo.write_text(VULKANINFO)
            server_log.write_text(GPU_LOG)
            result = self.script('assess', '--vulkaninfo', str(vulkaninfo), '--server-log', str(server_log))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['libraries'], ['Vulkan'])


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='groundwork-inference-')
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name) / 'omarchy.json'
        self.patch = patch.object(updater, 'CONFIG', self.config)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write(self, **extra):
        value = dict(schema=1, source='/tmp/groundwork-source', keyd=False, screen_sharing=True,
                     obsidian_vault='Notes')
        value.update(extra)
        self.config.write_text(json.dumps(value) + '\n')

    def test_missing_choice_stays_off(self):
        self.write()
        self.assertFalse(updater.local_inference_enabled(updater.settings()))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(updater.main(['preferences', 'setup']), 0)
        self.assertEqual(output.getvalue().splitlines()[-1], '0')

    def test_non_boolean_choice_is_rejected(self):
        self.write(local_inference='yes')
        with self.assertRaisesRegex(updater.UpdateError, 'Invalid updater settings'):
            updater.settings()


if __name__ == '__main__':
    unittest.main()
