#!/usr/bin/env python3
# Groundwork Omarchy grep.threads cap (decision 0001)
"""Set grep.threads to 4 through one Groundwork-owned git include.

This is the only git default this layer sets. groundwork-mac's shared Unix
gitconfig is not applied on Omarchy (decision 0001). Agents run many git grep
searches, and upstream starts one worker per core.

The include is ~/.config/groundwork/grep-threads.gitconfig (XDG_CONFIG_HOME
when that path is absolute). ~/.config/git/config gains an include.path for it
when that path is absent. Other include paths are left as they are. The include
file is written only when grep.threads there is missing or not exactly 4, and
only when the file bytes differ from the text below. A file that already has
exactly one value, 4, is not rewritten.

Subcommand: apply
Exit status: 0 done or nothing to do, 1 a step failed, 2 usage.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

INCLUDE_TEXT = """\
# Agents run many git grep searches. Upstream starts one worker per core.
[grep]
\tthreads = 4
"""


def home():
    return Path(os.environ.get('HOME') or Path.home())


def config_home():
    xdg = os.environ.get('XDG_CONFIG_HOME')
    return Path(xdg) if xdg and os.path.isabs(xdg) else home() / '.config'


def include_path():
    return config_home() / 'groundwork' / 'grep-threads.gitconfig'


def gitconfig_path():
    return config_home() / 'git' / 'config'


def git_env():
    """Read the named file only. Drop injected config that would hide its value."""
    env = os.environ.copy()
    for key in list(env):
        if key in ('GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT') or key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')):
            env.pop(key)
    env['GIT_CONFIG_NOSYSTEM'] = '1'
    return env


def git_values(path, key):
    """Values of key in path. None when the file is absent. Raises on a git failure."""
    if not path.is_file():
        return None
    result = subprocess.run(
        ['git', 'config', '--file', str(path), '--get-all', key],
        capture_output=True, text=True, env=git_env())
    if result.returncode == 0:
        return result.stdout.splitlines()
    if result.returncode == 1 and not result.stderr.strip():
        return []
    raise OSError(result.stderr.strip() or f'cannot read {key} from {path}')


def require_regular_or_absent(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError(f'{path} is not a regular file; left untouched')


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.groundwork-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_include(path):
    """Write the include only when its grep.threads is missing or not exactly 4."""
    require_regular_or_absent(path)
    if git_values(path, 'grep.threads') == ['4']:
        return False
    try:
        same = path.is_file() and path.read_text() == INCLUDE_TEXT
    except (OSError, UnicodeError):
        same = False
    if same:
        return False
    write_atomic(path, INCLUDE_TEXT)
    return True


def ensure_included(target, include):
    """Add include.path when this file is not already included. Never replace another path."""
    require_regular_or_absent(target)
    wanted = str(include)
    current = git_values(target, 'include.path') or []
    if wanted in current:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ['git', 'config', '--file', str(target), '--add', 'include.path', wanted],
        check=True, env=git_env())
    return True


def apply():
    include = include_path()
    changed = write_include(include)
    changed = ensure_included(gitconfig_path(), include) or changed
    if changed:
        print(f'Groundwork grep.threads: set to 4 in {include}.')
    return 0


def main(argv):
    if argv[1:] != ['apply']:
        print(f'usage: {Path(argv[0]).name} apply', file=sys.stderr)
        return 2
    try:
        return apply()
    except (OSError, subprocess.CalledProcessError) as error:
        print(f'Groundwork grep.threads: not applied ({error}).', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
