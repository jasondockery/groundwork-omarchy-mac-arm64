#!/usr/bin/env python3
# Groundwork Omarchy grep.threads cap (decision 0001)
"""Set grep.threads to 4 through one Groundwork-owned git include.

This is the only git default this layer sets. groundwork-mac's shared Unix
gitconfig is not applied on Omarchy (decision 0001). Agents run many git grep
searches, and upstream starts one worker per core.

The include is ~/.config/groundwork/grep-threads.gitconfig (XDG_CONFIG_HOME
when that path is absolute). The file is written only when it is missing or
its text differs from the rendered cap below, so a value of 4 beside another
key, or without the reason comment, is replaced. A second run of that exact
text does not rewrite it. ~/.config/git/config gains an include.path for the
canonical file when that path is absent. Any other include.path whose file is
named grep-threads.gitconfig is removed, including one listed later, so a
stale copy cannot override the cap. Other include paths are left as they are.

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
    """Rewrite the include when its text is not exactly the rendered cap.

    Parsing grep.threads as 4 is not enough. Another key in the same file, or
    a missing reason comment, would otherwise stay in place.
    """
    require_regular_or_absent(path)
    if path.is_file():
        try:
            current = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as error:
            raise OSError(f'cannot read {path}') from error
        if current == INCLUDE_TEXT:
            return False
    write_atomic(path, INCLUDE_TEXT)
    return True


def same_include(value, include):
    if value == str(include):
        return True
    try:
        return Path(value).expanduser().resolve() == include.resolve()
    except OSError:
        return False


def drop_stale_includes(target, include):
    """Remove include.path entries for a different grep-threads.gitconfig.

    Git uses the last value. A later copy of this file would override the cap
    even when the canonical path is already listed. Other include paths stay.
    """
    require_regular_or_absent(target)
    current = git_values(target, 'include.path') or []
    stale = [
        value for value in current
        if not same_include(value, include) and Path(value).name == include.name
    ]
    if not stale:
        return False
    for value in stale:
        result = subprocess.run(
            ['git', 'config', '--file', str(target), '--fixed-value',
             '--unset', 'include.path', value],
            capture_output=True, text=True, env=git_env())
        if result.returncode not in (0, 5):
            raise OSError(result.stderr.strip() or f'cannot remove include.path {value}')
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
    target = gitconfig_path()
    changed = write_include(include)
    changed = drop_stale_includes(target, include) or changed
    changed = ensure_included(target, include) or changed
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
