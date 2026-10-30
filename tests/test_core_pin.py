"""The core gitlink must be the commit of the tag named by `branch =` in .gitmodules.

Renovate bumps core by tag, so a gitlink that is not exactly that tag's commit
would break the contract the groundwork-core preset relies on.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE_URL = "https://github.com/jasondockery/groundwork-core.git"


class CorePinTest(unittest.TestCase):
    def test_core_gitlink_matches_gitmodules_branch_tag(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        modules = REPO / ".gitmodules"
        tag = subprocess.run(
            ["git", "config", "--file", str(modules), "submodule.core.branch"],
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()
        self.assertRegex(tag, r"^v\d+\.\d+\.\d+$", "[submodule \"core\"] needs branch = <tag>")
        gitlink = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD:core"],
            capture_output=True,
            text=True,
            env=env,
        )
        if gitlink.returncode != 0:
            self.skipTest("not a git checkout with a core gitlink")
        sha = gitlink.stdout.strip()

        tagged = None
        if (REPO / "core" / ".git").exists():
            local = subprocess.run(
                ["git", "-C", str(REPO / "core"), "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"],
                capture_output=True,
                text=True,
                env=env,
            )
            if local.returncode == 0:
                tagged = local.stdout.strip()
        if tagged is None:
            # The checkout may lack tags, so ask the remote. An empty global config
            # keeps an insteadOf https->ssh rule from breaking the lookup.
            remote_env = dict(env, GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
            listing = subprocess.run(
                ["git", "ls-remote", CORE_URL, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
                capture_output=True,
                text=True,
                env=remote_env,
            )
            if listing.returncode != 0 or not listing.stdout.strip():
                if os.environ.get("CI"):
                    self.fail(f"cannot resolve tag {tag}: {listing.stderr.strip()}")
                self.skipTest(f"tag {tag} is not in a local core checkout and the remote is unreachable")
            # An annotated tag lists its commit last, on the peeled ^{} line.
            tagged = listing.stdout.splitlines()[-1].split()[0]
        self.assertEqual(sha, tagged, f"core gitlink is not the commit of {tag}")


if __name__ == "__main__":
    unittest.main()
