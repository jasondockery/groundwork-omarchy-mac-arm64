#!/usr/bin/env python3
# Groundwork Omarchy Node pin (decision 0016)
"""Pin Node in the user's global mise config so it does not move to the next major.

Omarchy declares `node = { version = "latest", lazy = true }` in
/etc/mise/conf.d/omarchy-tools.toml, so `latest` follows each new major, even a
non-LTS one. This adds `node = "<NODE_PIN>"` under [tools] in the user's global
mise config, only when no mise config the user owns sets node at all:

  - the global config file (MISE_GLOBAL_CONFIG_FILE, else config.toml in
    MISE_CONFIG_DIR or $XDG_CONFIG_HOME/mise or ~/.config/mise),
  - config.local.toml, config.<env>.toml and conf.d/*.toml beside it,
  - ~/.tool-versions, ~/.node-version, and the project-style files that override
    global config under $HOME: ~/.mise.toml, ~/mise.toml, ~/.mise.local.toml,
    ~/mise.local.toml and ~/.config/mise.toml.

An existing entry of any kind is kept. An absent config file is created. The
edit is a text insertion that is parsed and compared before it is written, an
existing file gets a numbered backup, and an unreadable, invalid or symlinked
file is left alone.

Subcommands: `apply` (the pin) and `version` (print NODE_PIN for the installer).
Exit status: 0 done or nothing to do, 1 the pin could not be checked or written,
2 usage, 3 a pin was written (the caller should install it and say so if it
cannot).
"""
import os
from pathlib import Path
import shutil
import sys
import tempfile

try:
    import tomllib
except ImportError:  # Python before 3.11
    tomllib = None

# The first LTS release of Node 26 was 26.13.0 (2026-10-28). Moving to the next
# line is a deliberate edit here, in the change that moves Groundwork's repos.
NODE_PIN = '26'


def home():
    return Path(os.environ.get('HOME') or Path.home())


def mise_dir():
    configured = os.environ.get('MISE_CONFIG_DIR')
    if configured:
        return Path(configured).expanduser()
    xdg = os.environ.get('XDG_CONFIG_HOME')
    if xdg and os.path.isabs(xdg):
        return Path(xdg) / 'mise'
    return home() / '.config' / 'mise'


def global_config():
    configured = os.environ.get('MISE_GLOBAL_CONFIG_FILE')
    if configured:
        return Path(configured).expanduser()
    return mise_dir() / 'config.toml'


def user_config_files(config):
    base = mise_dir()
    found = [config, base / 'config.local.toml', *sorted(base.glob('config.*.toml')),
             *sorted((base / 'conf.d').glob('*.toml'))]
    unique = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def toml_sets_node(path):
    with path.open('rb') as handle:
        data = tomllib.load(handle)
    tools = data.get('tools')
    return isinstance(tools, dict) and 'node' in tools


def tool_versions_sets_node(path):
    for line in path.read_text().splitlines():
        words = line.split('#', 1)[0].split()
        if words and words[0] == 'node':
            return True
    return False


def write_atomic(path, text, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(handle, 'w') as out:
            out.write(text)
        os.chmod(name, mode)
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def next_backup(path):
    number = 1
    while True:
        candidate = path.with_name(f'{path.name}.~{number}~')
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        number += 1


def pinned_text(text):
    """Return text with node added to [tools], or None when it cannot be done safely."""
    lines = text.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if line.split('#', 1)[0].strip() == '[tools]']
    entry = f'node = "{NODE_PIN}"\n'
    if len(headers) == 1:
        if not lines[headers[0]].endswith('\n'):
            lines[headers[0]] += '\n'
        lines.insert(headers[0] + 1, entry)
    elif not headers:
        if text and not text.endswith('\n'):
            lines[-1] += '\n'
        if text.strip():
            lines.append('\n')
        lines.extend(['[tools]\n', entry])
    else:
        return None
    return ''.join(lines)


def left_alone(reason, error=False):
    print(f'Node pin left alone: {reason}', file=sys.stderr if error else sys.stdout)
    return 1 if error else 0


def apply_pin():
    if tomllib is None:
        return left_alone('this Python has no tomllib (3.11 or newer).', error=True)
    config = global_config()
    if config.suffix != '.toml':
        return left_alone(f'{config} is not a .toml file. Add node = "{NODE_PIN}" yourself.')
    for linked in (config, config.parent):
        if linked.is_symlink():
            return left_alone(f'{linked} is a symlink.', error=True)
    for candidate in user_config_files(config):
        if not candidate.exists():
            continue
        try:
            if toml_sets_node(candidate):
                return left_alone(f'{candidate} already sets node.')
        except (OSError, tomllib.TOMLDecodeError) as error:
            return left_alone(f'{candidate} could not be read ({error}).', error=True)
    for candidate in (home() / '.mise.toml', home() / 'mise.toml', home() / '.mise.local.toml',
                      home() / 'mise.local.toml', home() / '.config' / 'mise.toml'):
        if candidate.is_file() and candidate not in user_config_files(config):
            try:
                if toml_sets_node(candidate):
                    return left_alone(f'{candidate} already sets node.')
            except (OSError, tomllib.TOMLDecodeError) as error:
                return left_alone(f'{candidate} could not be read ({error}).', error=True)
    node_version = home() / '.node-version'
    if node_version.is_file():
        try:
            if node_version.read_text().split('#', 1)[0].strip():
                return left_alone(f'{node_version} already sets node.')
        except (OSError, UnicodeError) as error:
            return left_alone(f'{node_version} could not be read ({error}).', error=True)
    tool_versions = home() / '.tool-versions'
    if tool_versions.is_file():
        try:
            if tool_versions_sets_node(tool_versions):
                return left_alone(f'{tool_versions} already sets node.')
        except (OSError, UnicodeError) as error:
            return left_alone(f'{tool_versions} could not be read ({error}).', error=True)
    if config.exists() and not config.is_file():
        return left_alone(f'{config} is not a regular file.', error=True)
    try:
        original = config.read_text() if config.exists() else ''
    except (OSError, UnicodeError) as error:
        return left_alone(f'{config} could not be read ({error}).', error=True)
    updated = pinned_text(original)
    if updated is None:
        return left_alone(f'{config} has more than one [tools] table. Add node = "{NODE_PIN}" yourself.')
    # Prove the edit before writing: node is the only new tool and every other
    # table is unchanged.
    try:
        before, after = tomllib.loads(original), tomllib.loads(updated)
    except tomllib.TOMLDecodeError as error:
        return left_alone(f'{config} would not stay valid TOML ({error}).', error=True)
    expected = dict(before)
    expected['tools'] = {**before.get('tools', {}), 'node': NODE_PIN}
    if after != expected:
        return left_alone(f'{config} has a layout this layer cannot edit safely. Add node = "{NODE_PIN}" yourself.')
    try:
        mode = config.stat().st_mode & 0o777 if config.exists() else 0o644
        if config.exists():
            shutil.copy2(config, next_backup(config))
        write_atomic(config, updated, mode)
    except OSError as error:
        print(f'Node pin was not written: {config} ({error}).', file=sys.stderr)
        return 1
    print(f'Pinned node = "{NODE_PIN}" in {config}; it is not installed by this step. '
          f'Change the pin there when you choose to move to another major.')
    return 3


def main(argv):
    if argv[1:] == ['version']:
        print(NODE_PIN)
        return 0
    if argv[1:] == ['apply']:
        return apply_pin()
    print(f'usage: {Path(argv[0]).name} apply|version', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
