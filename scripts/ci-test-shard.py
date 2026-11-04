#!/usr/bin/env python3
"""Split the unittest suite into CI shards balanced by measured duration.

Hosted CI runs one matrix job per shard. Each job discovers the same suite the
canonical local command finds (`python3 -B -m unittest discover -s tests`),
assigns every test ID to exactly one shard, and runs only its own IDs. The
local command is unchanged and still runs everything.

  ci-test-shard.py run --shard N   run shard N of SHARDS (verbose, like CI before)
  ci-test-shard.py plan            print each shard's test count and estimate
  ci-test-shard.py list --shard N  print shard N's test IDs

Assignment is deterministic: tests are taken heaviest first (ties by ID) and
each goes to the shard with the least estimated time so far. A test's weight is
the measured mean seconds per test of its file (WEIGHTS); a file not in the
table gets DEFAULT_WEIGHT. A stale table only unbalances shards; it never drops
a test, because the partition covers every discovered ID.

`run` fails when discovery finds no tests, when the partition does not cover
every discovered ID exactly once, or when the number of tests run differs from
the number assigned. The workflow matrix must list 0..SHARDS-1;
tests/test_ci_test_shard.py checks that against .github/workflows/ci.yml.

Exit status: 0 when the shard passed, 1 when a test failed or a check above
failed, 2 for bad usage.
"""
import argparse
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
SHARDS = 6

# Mean seconds per test, by test module, from hosted run 41208883401
# (main a708938, ubuntu-26.04, 780 tests in 1126 s). Re-measure from a hosted
# run's per-test log timestamps when shards drift apart.
WEIGHTS = {
    'test_t3_install': 6.03,
    'test_brave': 5.18,
    'test_migration_offer': 4.34,
    'test_install_keyd': 3.83,
    'test_syncthing': 0.43,
    'test_omarchy_plugins': 0.35,
    'test_update': 0.22,
    'test_omarchy_feed_watch': 0.10,
    'test_commit_guard': 0.14,
}
DEFAULT_WEIGHT = 0.05


def iter_tests(suite):
    """Yield the leaf test cases of a suite in discovery order."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def discover(start_dir):
    """Return the discovered test cases, in the canonical command's order."""
    loader = unittest.TestLoader()
    return list(iter_tests(loader.discover(str(start_dir))))


def weight(test_id, weights=WEIGHTS, default=DEFAULT_WEIGHT):
    return weights.get(test_id.split('.', 1)[0], default)


def partition(test_ids, shards, weights=WEIGHTS, default=DEFAULT_WEIGHT):
    """Assign each unique test ID to one of `shards` lists, balanced by weight."""
    if shards < 1:
        raise ValueError('shards must be at least 1')
    ids = sorted(set(test_ids), key=lambda t: (-weight(t, weights, default), t))
    loads = [0.0] * shards
    out = [[] for _ in range(shards)]
    for test_id in ids:
        target = min(range(shards), key=lambda i: (loads[i], i))
        out[target].append(test_id)
        loads[target] += weight(test_id, weights, default)
    return out


def check_partition(test_ids, parts):
    """Raise when `parts` does not hold every ID in `test_ids` exactly once."""
    seen = {}
    for index, part in enumerate(parts):
        for test_id in part:
            if test_id in seen:
                raise AssertionError(f'{test_id} is in shard {seen[test_id]} and shard {index}')
            seen[test_id] = index
    missing = set(test_ids) - set(seen)
    extra = set(seen) - set(test_ids)
    if missing or extra:
        raise AssertionError(f'partition mismatch: missing {sorted(missing)}, extra {sorted(extra)}')


def shard_suite(cases, shard, shards):
    ids = [case.id() for case in cases]
    if not ids:
        raise AssertionError('discovery found no tests')
    if len(set(ids)) != len(ids):
        raise AssertionError('discovery returned a test ID twice')
    parts = partition(ids, shards)
    check_partition(ids, parts)
    mine = set(parts[shard])
    # Keep discovery order so each class's tests stay together.
    return unittest.TestSuite(case for case in cases if case.id() in mine), parts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('command', choices=['run', 'plan', 'list'])
    parser.add_argument('--shard', type=int)
    parser.add_argument('--shards', type=int, default=SHARDS, help=argparse.SUPPRESS)
    parser.add_argument('--start-dir', default=str(REPO / 'tests'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command != 'plan' and args.shard is None:
        parser.error(f'{args.command} needs --shard')
    if args.shard is not None and not 0 <= args.shard < args.shards:
        parser.error(f'--shard must be 0..{args.shards - 1}')

    cases = discover(args.start_dir)
    try:
        suite, parts = shard_suite(cases, args.shard or 0, args.shards)
    except AssertionError as error:
        print(f'ci-test-shard: {error}', file=sys.stderr)
        return 1

    if args.command == 'plan':
        for index, part in enumerate(parts):
            estimate = sum(weight(t) for t in part)
            print(f'shard {index}: {len(part)} tests, about {estimate:.0f} s')
        print(f'total: {sum(len(p) for p in parts)} tests')
        return 0
    if args.command == 'list':
        print('\n'.join(case.id() for case in suite))
        return 0

    expected = suite.countTestCases()
    print(f'shard {args.shard} of {args.shards}: {expected} of {len(cases)} tests', flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.testsRun != expected:
        print(f'ci-test-shard: ran {result.testsRun} tests, expected {expected}', file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
