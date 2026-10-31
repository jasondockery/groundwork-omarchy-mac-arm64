#!/usr/bin/env python3
# Groundwork Omarchy path shadow
"""Report PATH entries that shadow a Groundwork-managed command.

Vendor curl|bash installers prepend their own directory to PATH. That copy can
win over the package or mise copy this layer manages, and the two drift apart
on the next update of either. This script only reads: it never deletes a
binary, edits a shell file, or touches config. It never runs a managed
command, so a hanging binary cannot stall it.

For each managed command it compares the first PATH hit with the managed path:

  pacman      the first PATH hit that `pacman -Qoq` says a package owns (this
              covers apps Omarchy provides, per the Omarchy-first rule)
  mise        `mise which`, or the mise data directory (its command wrappers,
              shims and installs)
  groundwork  the path the Groundwork installer writes (T3 Code)

When another entry wins, it names the shell files that put that directory on
PATH, where it can find them.

  groundwork-path-shadow.py [--json] [--strict] [command ...]

The exit status is 0 unless --strict is given and a command is shadowed.
"""
import argparse
import glob
import json
import os
from pathlib import Path
import subprocess
import sys


# (command, owner). The commands setup ensures; Cursor and Bitwarden may have
# no package owner, which is reported as unmanaged rather than as a shadow.
MANAGED = [
    ('claude', 'mise'),
    ('codex', 'mise'),
    ('opencode', 'mise'),
    ('grok', 'mise'),
    ('t3', 'groundwork'),
    ('herdr', 'pacman'),
    ('tailscale', 'pacman'),
    ('syncthing', 'pacman'),
    ('keyd', 'pacman'),
    ('cursor', 'pacman'),
    ('bw', 'pacman'),
    ('obsidian', 'pacman'),
]
# Installers that source an env script instead of writing the directory.
ENV_SCRIPTS = {'.cargo/bin': '.cargo/env'}
STATUS_ORDER = ['shadowed', 'missing', 'unmanaged', 'ok', 'absent']
RUN_TIMEOUT = 10


def run(argv, environ, cwd):
    """Return (returncode, stdout). A missing or slow command is a failure."""
    try:
        done = subprocess.run(argv, env=environ, cwd=cwd, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, timeout=RUN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return 1, ''
    return done.returncode, done.stdout


def path_dirs(environ):
    seen = []
    for entry in environ.get('PATH', '').split(':'):
        if entry and os.path.isabs(entry) and entry not in seen:
            seen.append(entry)
    return seen


def path_hits(name, environ):
    """Every executable `name` on PATH, in order (the first one wins)."""
    hits = []
    for directory in path_dirs(environ):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            hits.append(candidate)
    return hits


def same_file(first, second):
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def mise_data_dir(environ):
    home = environ.get('HOME', '')
    if environ.get('MISE_DATA_DIR'):
        return environ['MISE_DATA_DIR']
    return os.path.join(environ.get('XDG_DATA_HOME') or os.path.join(home, '.local/share'), 'mise')


def under(path, directory):
    directory = directory.rstrip('/') + '/'
    return path.startswith(directory)


def pacman_owner(path, environ, runner):
    code, out = runner(['pacman', '-Qoq', '--', path], environ, None)
    owner = out.strip().splitlines()
    return owner[0] if code == 0 and owner else ''


def check_command(name, owner, environ, runner):
    """Classify one command. Returns a dict; reads only."""
    hits = path_hits(name, environ)
    winner = hits[0] if hits else ''
    result = {'command': name, 'owner': owner, 'winner': winner, 'managed': '',
              'package': '', 'status': 'absent', 'detail': ''}
    home = environ.get('HOME', '')
    if owner == 'pacman':
        for hit in hits:
            package = pacman_owner(hit, environ, runner)
            if package:
                result['managed'] = hit
                result['package'] = package
                break
        if winner and result['managed'] == winner:
            result['status'] = 'ok'
        elif winner and result['managed']:
            result['status'] = 'shadowed'
            result['detail'] = 'package %s owns %s' % (result['package'], result['managed'])
        elif winner:
            result['status'] = 'unmanaged'
            result['detail'] = 'no package owns it'
        return result
    if owner == 'mise':
        code, out = runner(['mise', 'which', name], environ, home or None)
        managed = out.strip().splitlines()[0] if code == 0 and out.strip() else ''
        data = mise_data_dir(environ)
        result['managed'] = managed
        if winner and (under(winner, data) or (managed and same_file(winner, managed))):
            result['status'] = 'ok'
        elif winner and managed:
            result['status'] = 'shadowed'
            result['detail'] = 'mise provides %s' % managed
        elif winner:
            result['status'] = 'unmanaged'
            result['detail'] = 'mise does not provide it'
        elif managed:
            result['status'] = 'missing'
            result['detail'] = 'mise provides %s but its directory is not on PATH' % managed
        return result
    managed = os.path.join(environ.get('T3CODE_INSTALL_BIN_DIR') or os.path.join(home, '.local/bin'), name)
    exists = os.path.isfile(managed) and os.access(managed, os.X_OK)
    result['managed'] = managed
    if winner and exists and same_file(winner, managed):
        result['status'] = 'ok'
    elif winner and exists:
        result['status'] = 'shadowed'
        result['detail'] = 'installer wrote %s' % managed
    elif winner:
        result['status'] = 'unmanaged'
        result['detail'] = 'not installed at %s' % managed
    elif exists:
        result['status'] = 'missing'
        result['detail'] = '%s is not on PATH' % os.path.dirname(managed)
    return result


def shell_files(home, omarchy_path, system_root='/'):
    """Files that can set PATH, in rough load order. Missing ones are skipped."""
    def system(path):
        return os.path.join(system_root, path.lstrip('/'))
    files = [system('/etc/environment'), system('/etc/profile')]
    files += sorted(glob.glob(system('/etc/profile.d/*.sh')))
    files += sorted(glob.glob(system('/usr/share/uwsm/env.d/*')))
    omarchy_bash = os.path.join(omarchy_path, 'default/bash')
    files += [os.path.join(omarchy_bash, name)
              for name in ('env-bootstrap', 'envs', 'rc', 'init', 'shell')]
    for name in ('.bash_profile', '.bash_login', '.profile', '.bashrc',
                 '.zshenv', '.zprofile', '.zshrc', '.config/uwsm/env',
                 '.config/uwsm/env-hyprland'):
        files.append(os.path.join(home, name))
    files += sorted(glob.glob(os.path.join(home, '.config/environment.d/*.conf')))
    seen = []
    for path in files:
        if path not in seen and os.path.isfile(path):
            seen.append(path)
    return seen


def directory_needles(directory, home):
    needles = [directory]
    if home and under(directory + '/', home):
        relative = directory[len(home.rstrip('/')) + 1:]
        needles += ['$HOME/' + relative, '${HOME}/' + relative, '~/' + relative]
        for suffix, script in ENV_SCRIPTS.items():
            if relative == suffix:
                needles += ['$HOME/' + script, '${HOME}/' + script, '~/' + script]
    return needles


def attribute(directory, files, home):
    """Lines that name `directory` in a PATH setting, as dicts."""
    needles = directory_needles(directory, home)
    found = []
    for path in files:
        try:
            lines = Path(path).read_text(errors='replace').splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            code = line.strip()
            if code.startswith('#') or 'path' not in code.lower() and '/env' not in code:
                continue
            position = min((code.find(n) for n in needles if n in code), default=-1)
            if position < 0:
                continue
            anchors = [code.find(a) for a in ('$PATH', '${PATH', '$path') if a in code]
            where = 'sets'
            if anchors:
                where = 'prepends' if position < min(anchors) else 'appends'
            found.append({'file': path, 'line': number, 'effect': where, 'text': code[:200]})
    return found


def scan(environ, runner=run, commands=None, files=None):
    home = environ.get('HOME', '')
    omarchy_path = environ.get('OMARCHY_PATH') or '/usr/share/omarchy'
    if files is None:
        files = shell_files(home, omarchy_path)
    wanted = [(n, o) for n, o in MANAGED if not commands or n in commands]
    results = []
    for name, owner in wanted:
        item = check_command(name, owner, environ, runner)
        item['attribution'] = []
        if item['status'] in ('shadowed', 'unmanaged') and item['winner']:
            directory = os.path.dirname(item['winner'])
            item['attribution'] = attribute(directory, files, home)
        results.append(item)
    return results


def display(path, home):
    if home and path.startswith(home.rstrip('/') + '/'):
        return '~/' + path[len(home.rstrip('/')) + 1:]
    return path


def render(results, home):
    lines = []
    for item in sorted(results, key=lambda i: STATUS_ORDER.index(i['status'])):
        if item['status'] == 'absent':
            continue
        text = '%-9s %-10s' % (item['status'], item['command'])
        if item['winner']:
            text += ' wins: ' + display(item['winner'], home)
        if item['status'] != 'ok' and item['managed']:
            text += ' managed: ' + display(item['managed'], home)
        if item['detail']:
            text += ' (%s)' % item['detail']
        lines.append(text)
        for hit in item['attribution']:
            lines.append('          %s PATH at %s:%d' % (hit['effect'], display(hit['file'], home), hit['line']))
        if item['status'] in ('shadowed', 'unmanaged') and item['winner'] and not item['attribution']:
            lines.append('          no scanned shell file names %s (inherited, or set by a file this does not read)'
                         % display(os.path.dirname(item['winner']), home))
    counts = {s: sum(1 for i in results if i['status'] == s) for s in STATUS_ORDER}
    lines.append('%d ok, %d shadowed, %d missing, %d unmanaged, %d not installed. Report only: nothing was changed.'
                 % (counts['ok'], counts['shadowed'], counts['missing'], counts['unmanaged'], counts['absent']))
    return '\n'.join(lines)


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(description='Report PATH entries that shadow a Groundwork-managed command (read only).')
    parser.add_argument('commands', nargs='*', help='limit the check to these managed commands')
    parser.add_argument('--json', action='store_true', help='print machine-readable results')
    parser.add_argument('--strict', action='store_true', help='exit 1 when a command is shadowed')
    args = parser.parse_args(argv)
    environ = dict(os.environ if environ is None else environ)
    known = [name for name, _ in MANAGED]
    unknown = [c for c in args.commands if c not in known]
    if unknown:
        parser.error('not a managed command: %s (managed: %s)' % (', '.join(unknown), ', '.join(known)))
    results = scan(environ, commands=args.commands)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(render(results, environ.get('HOME', '')))
    shadowed = any(item['status'] == 'shadowed' for item in results)
    return 1 if args.strict and shadowed else 0


if __name__ == '__main__':
    sys.exit(main())
