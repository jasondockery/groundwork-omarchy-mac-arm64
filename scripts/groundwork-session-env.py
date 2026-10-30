#!/usr/bin/env python3
# Groundwork Omarchy session tool path (decision 0016)
"""Make desktop-launched apps resolve mise-managed tools like an interactive shell.

Omarchy's env-bootstrap appends ~/.local/share/mise/shims after system binaries
so system binaries keep precedence. Interactive shells run `mise activate` and
put mise first, but apps launched from the desktop (T3 Code, Claude, Codex and
the agents they spawn) never do, so /usr/bin/node (nodejs-lts-jod, kept for
bitwarden-cli) wins over the Node a repository's .node-version asks for.

Two idempotent steps, both under the user's own config directories:

  env   Write ~/.config/uwsm/env.d/50-groundwork-mise, a file this layer owns
        entirely. uwsm sources it after Omarchy's own 10-omarchy, so the shims
        directory moves ahead of system binaries but stays behind mise's
        command-wrappers directory, which Omarchy keeps first on purpose
        (account-switching wrappers for claude, codex, grok). A file without
        the ownership header is the user's and is left alone.
  node  Add `node = "26"` to [tools] in ~/.config/mise/config.toml when neither
        that file nor ~/.config/mise/conf.d/*.toml sets node. Omarchy's system
        config declares node = "latest", which would move to Node 27 on its
        own. An existing entry of any kind is kept.

Exit status: 0 done or nothing to do, 1 a step failed (the other still ran),
2 usage, 3 a node pin was written (the caller may install it).
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

NODE_PIN = '26'
OWNER_LINE = '# Managed by Groundwork (groundwork-omarchy-mac-arm64, decision 0016).'
DROPIN = '50-groundwork-mise'

# POSIX sh only: uwsm sources this under a plain shell. Move mise's shims to
# just behind the command-wrappers directory (first, when that is on PATH).
ENV_BODY = r'''# Apps started from the desktop never run `mise activate`, so Omarchy's appended
# shims directory loses to /usr/bin/node and friends. Put the shims ahead of
# system binaries, behind mise's command-wrappers directory, as an interactive
# shell has them. Takes effect at the next login.
# To manage this yourself, delete the "Managed by" line above; this layer then
# leaves the file alone.
_gw_mise="${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}"
_gw_shims="$_gw_mise/shims"
_gw_wrap="$_gw_mise/command-wrappers/bin"
_gw_has_wrap=0
_gw_rest=
_gw_old="$PATH:"
while [ -n "$_gw_old" ]; do
  _gw_entry=${_gw_old%%:*}
  _gw_old=${_gw_old#*:}
  case $_gw_entry in
    "") ;;
    "$_gw_wrap") _gw_has_wrap=1 ;;
    "$_gw_shims") ;;
    *) _gw_rest=${_gw_rest:+$_gw_rest:}$_gw_entry ;;
  esac
done
if [ "$_gw_has_wrap" = 1 ]; then
  PATH="$_gw_wrap:$_gw_shims${_gw_rest:+:$_gw_rest}"
else
  PATH="$_gw_shims${_gw_rest:+:$_gw_rest}"
fi
export PATH
unset _gw_mise _gw_shims _gw_wrap _gw_has_wrap _gw_rest _gw_old _gw_entry
'''
ENV_TEXT = OWNER_LINE + '\n' + ENV_BODY


def config_home():
    configured = os.environ.get('XDG_CONFIG_HOME')
    if configured and os.path.isabs(configured):
        return Path(configured)
    return Path.home() / '.config'


def symlink_below(path, base):
    """Return the first symlink on the way from base down to path, if any."""
    current = path
    while current != base and current != current.parent:
        if current.is_symlink():
            return current
        current = current.parent
    return None


def write_atomic(path, text, mode=0o644):
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


def apply_env():
    base = config_home()
    target = base / 'uwsm' / 'env.d' / DROPIN
    linked = symlink_below(target, base)
    if linked is not None:
        print(f'Session tool path left alone: {linked} is a symlink.', file=sys.stderr)
        return 1
    if target.exists():
        if not target.is_file():
            print(f'Session tool path left alone: {target} is not a regular file.', file=sys.stderr)
            return 1
        try:
            current = target.read_text()
        except (OSError, UnicodeError) as error:
            print(f'Session tool path left alone: {target} could not be read ({error}).', file=sys.stderr)
            return 1
        if not current.startswith(OWNER_LINE):
            print(f'Session tool path left alone: {target} is yours (no Groundwork header).')
            return 0
        if current == ENV_TEXT:
            print('Session tool path is current.')
            return 0
        verb = 'Updated'
    else:
        verb = 'Wrote'
    try:
        write_atomic(target, ENV_TEXT)
    except OSError as error:
        print(f'Session tool path was not written: {target} ({error}).', file=sys.stderr)
        return 1
    print(f'{verb} {target}. Log out and back in so desktop-launched apps use mise\'s tools.')
    return 0


def node_entry(path):
    """Return (found, error) for a tools.node entry in one TOML file."""
    try:
        with path.open('rb') as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        return None, error
    tools = data.get('tools')
    return (isinstance(tools, dict) and 'node' in tools), None


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
    headers = [i for i, line in enumerate(lines) if line.strip().split('#')[0].strip() == '[tools]']
    entry = f'node = "{NODE_PIN}"\n'
    if len(headers) == 1:
        index = headers[0] + 1
        if not lines[headers[0]].endswith('\n'):
            lines[headers[0]] += '\n'
        lines.insert(index, entry)
    elif not headers:
        if text and not text.endswith('\n'):
            lines[-1] += '\n'
        if text.strip():
            lines.append('\n')
        lines.append('[tools]\n')
        lines.append(entry)
    else:
        return None
    return ''.join(lines)


def tools_of(text):
    return tomllib.loads(text).get('tools', {})


def apply_node():
    if tomllib is None:
        print('Node pin left alone: this Python has no tomllib (3.11 or newer).', file=sys.stderr)
        return 1
    base = config_home() / 'mise'
    config = base / 'config.toml'
    linked = symlink_below(config, config_home())
    if linked is not None:
        print(f'Node pin left alone: {linked} is a symlink.', file=sys.stderr)
        return 1
    for candidate in [config, *sorted((base / 'conf.d').glob('*.toml'))]:
        if not candidate.exists():
            continue
        found, error = node_entry(candidate)
        if error is not None:
            print(f'Node pin left alone: {candidate} could not be read ({error}).', file=sys.stderr)
            return 1
        if found:
            print(f'Node pin left alone: {candidate} already sets node.')
            return 0
    if config.exists():
        if not config.is_file():
            print(f'Node pin left alone: {config} is not a regular file.', file=sys.stderr)
            return 1
        try:
            original = config.read_text()
        except (OSError, UnicodeError) as error:
            print(f'Node pin left alone: {config} could not be read ({error}).', file=sys.stderr)
            return 1
    else:
        original = ''
    updated = pinned_text(original)
    if updated is None:
        print(f'Node pin left alone: {config} has more than one [tools] table. Add node = "{NODE_PIN}" yourself.')
        return 0
    # Prove the edit before writing: node is the only new tool, and the rest of
    # [tools] and every other table are unchanged.
    try:
        before = tomllib.loads(original)
        after = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as error:
        print(f'Node pin left alone: {config} would not stay valid TOML ({error}).', file=sys.stderr)
        return 1
    expected_tools = dict(before.get('tools', {}))
    expected_tools['node'] = NODE_PIN
    expected = dict(before)
    expected['tools'] = expected_tools
    if after != expected:
        print(f'Node pin left alone: {config} has a layout this layer cannot edit safely. Add node = "{NODE_PIN}" yourself.')
        return 0
    try:
        if config.exists():
            shutil.copy2(config, next_backup(config))
        write_atomic(config, updated, mode=config.stat().st_mode & 0o777 if config.exists() else 0o644)
    except OSError as error:
        print(f'Node pin was not written: {config} ({error}).', file=sys.stderr)
        return 1
    print(f'Pinned node = "{NODE_PIN}" in {config}, so mise does not move to the next major on its own. Change it there when you choose to.')
    return 3


def main(argv):
    steps = {'env': apply_env, 'node': apply_node}
    if len(argv) != 2 or argv[1] not in ('apply', *steps):
        print(f'usage: {Path(argv[0]).name} apply|env|node', file=sys.stderr)
        return 2
    chosen = list(steps.values()) if argv[1] == 'apply' else [steps[argv[1]]]
    results = [step() for step in chosen]
    if 1 in results:
        return 1
    return 3 if 3 in results else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
