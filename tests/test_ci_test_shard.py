"""Test the CI shard planner: every test runs in exactly one shard."""
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/ci-test-shard.py'
spec = importlib.util.spec_from_file_location('ci_test_shard', SCRIPT)
shard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shard)

FAKE_IDS = (
    [f'test_install_keyd.InstallKeydTests.test_{n:03d}' for n in range(40)]
    + [f'test_t3_install.T3InstallTests.test_{n:03d}' for n in range(13)]
    + [f'test_small.SmallTests.test_{n:03d}' for n in range(30)]
    + ['test_new_file.NewTests.test_only']
)


class PartitionTests(unittest.TestCase):
    def test_every_id_is_in_exactly_one_shard(self):
        for shards in (1, 2, 6, 7, len(FAKE_IDS) + 3):
            parts = shard.partition(FAKE_IDS, shards)
            self.assertEqual(len(parts), shards)
            shard.check_partition(FAKE_IDS, parts)
            flat = [t for part in parts for t in part]
            self.assertEqual(sorted(flat), sorted(FAKE_IDS))

    def test_assignment_does_not_depend_on_input_order(self):
        reordered = list(reversed(FAKE_IDS))
        self.assertEqual(shard.partition(FAKE_IDS, 6), shard.partition(reordered, 6))

    def test_a_heavy_file_is_split_across_every_shard(self):
        for part in shard.partition(FAKE_IDS, 6):
            self.assertTrue(any(t.startswith('test_install_keyd.') for t in part), part)

    def test_estimated_loads_stay_within_one_heaviest_test(self):
        parts = shard.partition(FAKE_IDS, 6)
        loads = [sum(shard.weight(t) for t in part) for part in parts]
        self.assertLessEqual(max(loads) - min(loads), max(shard.WEIGHTS.values()))

    def test_check_partition_rejects_a_dropped_or_duplicated_id(self):
        ids = ['a.A.one', 'a.A.two', 'a.A.three']
        with self.assertRaisesRegex(AssertionError, 'missing'):
            shard.check_partition(ids, [['a.A.one'], ['a.A.two']])
        with self.assertRaisesRegex(AssertionError, 'shard 0 and shard 1'):
            shard.check_partition(ids, [['a.A.one', 'a.A.two'], ['a.A.two', 'a.A.three']])

    def test_the_real_suite_partitions_completely(self):
        cases = shard.discover(REPO / 'tests')
        ids = [case.id() for case in cases]
        self.assertIn('test_ci_test_shard.PartitionTests.test_the_real_suite_partitions_completely', ids)
        self.assertEqual(len(ids), len(set(ids)))
        shard.check_partition(ids, shard.partition(ids, shard.SHARDS))

    def test_the_workflow_matrix_lists_every_shard(self):
        workflow = (REPO / '.github/workflows/ci.yml').read_text()
        match = re.search(r'^\s*shard: \[([0-9, ]+)\]\s*$', workflow, re.M)
        self.assertIsNotNone(match, 'ci.yml has no shard matrix list')
        listed = [int(n) for n in match.group(1).split(',')]
        self.assertEqual(listed, list(range(shard.SHARDS)))
        self.assertIn('scripts/ci-test-shard.py run --shard ${{ matrix.shard }}', workflow)


class RunTests(unittest.TestCase):
    """Run the script on a small fake suite, as the workflow does."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='ci-test-shard-'))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.tests = self.root / 'tests'
        self.tests.mkdir()
        self.log = self.root / 'ran.log'
        body = 'import os, unittest\n\n\nclass T(unittest.TestCase):\n'
        for n in range(5):
            body += (
                f'    def test_{n}(self):\n'
                f"        with open(os.environ['RAN_LOG'], 'a') as f:\n"
                f"            f.write(self.id() + '\\n')\n"
            )
        (self.tests / 'test_alpha.py').write_text(body)
        (self.tests / 'test_beta.py').write_text(body)

    def run_shard(self, index, shards):
        env = {
            'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
            'HOME': str(self.root),
            'TMPDIR': str(self.root),
            'RAN_LOG': str(self.log),
        }
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPT), 'run', '--shard', str(index),
             '--shards', str(shards), '--start-dir', str(self.tests)],
            env=env, capture_output=True, text=True, timeout=60)

    def test_shards_together_run_every_test_once(self):
        for index in range(3):
            proc = self.run_shard(index, 3)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        ran = self.log.read_text().split()
        self.assertEqual(len(ran), 10)
        self.assertEqual(len(set(ran)), 10)

    def test_a_failing_test_fails_its_shard(self):
        (self.tests / 'test_gamma.py').write_text(
            'import unittest\n\n\nclass T(unittest.TestCase):\n'
            '    def test_fails(self):\n        self.fail("boom")\n')
        codes = [self.run_shard(index, 3).returncode for index in range(3)]
        self.assertEqual(sorted(codes), [0, 0, 1])

    def test_an_import_error_fails_a_shard(self):
        (self.tests / 'test_broken.py').write_text('import does_not_exist_ci_shard\n')
        codes = [self.run_shard(index, 3).returncode for index in range(3)]
        self.assertIn(1, codes)

    def test_an_empty_suite_fails(self):
        for path in self.tests.iterdir():
            path.unlink()
        proc = self.run_shard(0, 1)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('no tests', proc.stderr)


if __name__ == '__main__':
    unittest.main()
