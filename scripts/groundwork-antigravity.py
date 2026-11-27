#!/usr/bin/env python3
# Antigravity CLI, desktop launcher, and T3 Code provider (issue 162).
"""Pin T3 Code's Antigravity provider and add one Hyprland binding.

  groundwork-antigravity.py merge-settings <absolute-agy>
  groundwork-antigravity.py bind
  groundwork-antigravity.py unbind

merge-settings adds providerInstances.antigravity only when that key is
absent. An existing instance, including one with enabled false or its own
binaryPath, is left byte for byte. A missing file is created mode 0600.
Invalid JSON, a non-object root, and a providerInstances value that is not
an object are left untouched. T3CODE_HOME empty or unset is ~/.t3. The file
and its parents must be regular; a symlink is not followed and not written.

Nothing from settings.json is printed. apiKey is never written or read back
out. binaryPath is the absolute pacman-owned agy the caller checked.

bind copies config/groundwork-antigravity.lua to
~/.config/hypr/groundwork-antigravity.lua (a regular file, not a link into
this checkout) and appends one dofile line to bindings.lua when that file is
a regular file and the line is absent. A symlink bindings.lua is not edited.
The focus class comes from the antigravity package's desktop entry when that
class is a plain name. A desktop entry is written only when the package
query succeeds and lists none.

unbind removes only that dofile line. It does not remove the package, the
settings entry, or any other line.

Exit 0 when nothing was written, 10 when a Hypr file changed, 1 on a bad
argument or a failed write.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

INSTANCE_KEY = 'antigravity'
# The line append to bindings.lua. Exact match, so a later run can see it
# and a removal takes only this line.
BINDING_LINE = (
    'dofile((os.getenv("XDG_CONFIG_HOME") or (os.getenv("HOME") .. "/.config"))'
    ' .. "/hypr/groundwork-antigravity.lua")'
    ' -- Groundwork Antigravity. One line. See docs/apps/antigravity.md.'
)
OWNED_LUA_MARKER = 'Groundwork Antigravity binding'
DEFAULT_FOCUS = '^antigravity$'
FOCUS_CLASS = re.compile(r'[A-Za-z0-9_-]+')
DESKTOP_MARKER = 'X-Groundwork-Antigravity=1'
CHANGED = 10


def config_dir(environ):
    configured = environ.get('XDG_CONFIG_HOME') or ''
    if configured:
        return Path(configured)
    home = environ.get('HOME') or ''
    if not home:
        raise OSError('HOME is unset, so the Hyprland config directory is unknown.')
    return Path(home) / '.config'


def data_dir(environ):
    configured = environ.get('XDG_DATA_HOME') or ''
    if configured:
        return Path(configured)
    home = environ.get('HOME') or ''
    if not home:
        raise OSError('HOME is unset, so the applications directory is unknown.')
    return Path(home) / '.local' / 'share'


def t3_root(environ):
    """${T3CODE_HOME:-~/.t3}. An empty value uses ~/.t3. A relative value is refused."""
    raw = environ.get('T3CODE_HOME')
    if raw is None or raw == '':
        home = environ.get('HOME') or ''
        if not home:
            raise OSError('HOME is unset, so T3 Code settings cannot be placed.')
        return Path(home) / '.t3'
    path = Path(raw)
    if not path.is_absolute():
        raise OSError('T3CODE_HOME must be an absolute path.')
    return path


def contains_symlink(path):
    """True when `path` or an existing parent is a symlink."""
    current = Path(path.anchor)
    for part in Path(path).parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
        if not current.exists():
            return False
    return False


def atomic_write(path, content, mode):
    """Replace `path` with `content` in the same directory. `mode` is the permission bits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix='.groundwork-antigravity-', dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def instance_document(binary):
    # enabled on the instance is enough. authMethod stays at T3's
    # oauth-personal default, which is what a personal Ultra account uses.
    # apiKey is a secret T3 stores itself; this layer never writes it.
    return {
        'driver': 'antigravity',
        'enabled': True,
        'config': {'binaryPath': binary},
    }


def refuse_binary(binary):
    if not isinstance(binary, str) or not binary or '\n' in binary or '\x00' in binary:
        return 'The agy path is empty or contains a newline.'
    path = Path(binary)
    if not path.is_absolute():
        return 'The agy path must be absolute.'
    if path.is_symlink() or not path.is_file():
        return 'The agy path must be a regular file.'
    return None


def merge_settings(binary, environ):
    """Add the Antigravity instance when it is absent. Return an exit status.

    A reason to leave the file alone is printed and returns 0. A bad agy
    path or a failed write returns 1. The file body is never printed.
    """
    problem = refuse_binary(binary)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    try:
        path = t3_root(environ) / 'userdata' / 'settings.json'
    except OSError as error:
        print(f'T3 Code settings were not changed ({error}).', file=sys.stderr)
        return 1
    if contains_symlink(path):
        print(f'T3 Code settings.json is a symlink or sits under one, so it was left unchanged: {path}',
              file=sys.stderr)
        return 0
    if path.exists() and not path.is_file():
        print(f'T3 Code settings.json is not a regular file, so it was left unchanged: {path}',
              file=sys.stderr)
        return 0
    if not path.exists():
        document = {'providerInstances': {INSTANCE_KEY: instance_document(binary)}}
        try:
            atomic_write(path, (json.dumps(document, indent=2, ensure_ascii=False) + '\n').encode(), 0o600)
        except OSError as error:
            print(f'T3 Code settings.json was not created: {path} ({error})', file=sys.stderr)
            return 1
        return 0
    try:
        original = path.read_bytes()
    except OSError as error:
        print(f'T3 Code settings.json could not be read, so it was left unchanged: {path} ({error})',
              file=sys.stderr)
        return 0
    try:
        value = json.loads(original.decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError):
        print(f'T3 Code settings.json is not valid JSON, so it was left unchanged: {path}', file=sys.stderr)
        return 0
    if not isinstance(value, dict):
        print(f'T3 Code settings.json is not a JSON object, so it was left unchanged: {path}', file=sys.stderr)
        return 0
    instances = value.get('providerInstances', None)
    if instances is None:
        instances = {}
        value['providerInstances'] = instances
    elif not isinstance(instances, dict):
        print('T3 Code providerInstances is not a JSON object, so settings.json was left unchanged.',
              file=sys.stderr)
        return 0
    if INSTANCE_KEY in instances:
        return 0
    instances[INSTANCE_KEY] = instance_document(binary)
    mode = stat.S_IMODE(path.stat().st_mode)
    try:
        atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode(), mode)
    except OSError as error:
        print(f'T3 Code settings.json was left unchanged: {path} ({error})', file=sys.stderr)
        return 1
    return 0


def template_text():
    path = Path(__file__).resolve().parent.parent / 'config' / 'groundwork-antigravity.lua'
    text = path.read_text(encoding='utf-8')
    if OWNED_LUA_MARKER not in text or f'focus = "{DEFAULT_FOCUS}"' not in text:
        raise OSError(f'The Antigravity binding template is incomplete: {path}')
    if not text.endswith('\n'):
        text += '\n'
    return text


def render_binding(focus_class):
    text = template_text()
    if focus_class and FOCUS_CLASS.fullmatch(focus_class):
        pattern = '^' + focus_class + '$'
    else:
        pattern = DEFAULT_FOCUS
    if pattern != DEFAULT_FOCUS:
        text = text.replace(f'focus = "{DEFAULT_FOCUS}"', f'focus = "{pattern}"', 1)
    return text


def package_desktop_files(environ):
    """Desktop files the antigravity package installed, [] when it lists none, None when unknown."""
    try:
        result = subprocess.run(['pacman', '-Qlq', 'antigravity'], env=environ, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, errors='replace', timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    found = []
    for line in result.stdout.splitlines():
        if not line.endswith('.desktop'):
            continue
        path = Path(line)
        if path.is_symlink() or not path.is_file():
            continue
        found.append(path)
    return found


def focus_class_from(desktops):
    chosen = desktops[0]
    for item in desktops:
        try:
            content = item.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if re.search(r'(?m)^Exec=antigravity(?:\s|$)', content):
            chosen = item
            break
    try:
        content = chosen.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 'antigravity'
    match = re.search(r'(?m)^StartupWMClass=(.*)$', content)
    if not match:
        return 'antigravity'
    value = match.group(1).strip()
    if FOCUS_CLASS.fullmatch(value):
        return value
    return 'antigravity'


def ensure_launcher(environ, desktops):
    """Write a user desktop entry only when the package query listed none."""
    if desktops is None or desktops:
        return
    path = data_dir(environ) / 'applications' / 'antigravity.desktop'
    if contains_symlink(path):
        print(f'Refusing to replace a symlink: {path}', file=sys.stderr)
        return
    if path.exists() and not path.is_file():
        print(f'Refusing to replace {path} because it is not a regular file.', file=sys.stderr)
        return
    if path.is_file():
        try:
            current = path.read_text(encoding='utf-8', errors='replace')
        except OSError as error:
            print(f'Leaving {path} alone ({error}).', file=sys.stderr)
            return
        if DESKTOP_MARKER not in current:
            print(f'Leaving {path} alone because Groundwork did not write it.', file=sys.stderr)
            return
    content = (
        '[Desktop Entry]\n'
        'Name=Antigravity\n'
        'Comment=Groundwork launcher because the antigravity package shipped no desktop entry.\n'
        'Exec=antigravity\n'
        'Terminal=false\n'
        'Type=Application\n'
        'StartupWMClass=antigravity\n'
        f'{DESKTOP_MARKER}\n'
    )
    try:
        atomic_write(path, content.encode(), 0o644)
    except OSError as error:
        print(f'Could not write {path} ({error}).', file=sys.stderr)


def write_lua(environ, focus_class):
    """Copy the binding. Return True when the file changed. Refuse a symlink."""
    path = config_dir(environ) / 'hypr' / 'groundwork-antigravity.lua'
    if contains_symlink(path):
        print(f'Refusing to replace a symlink: {path}', file=sys.stderr)
        return False
    if path.exists() and not path.is_file():
        print(f'Refusing to replace {path} because it is not a regular file.', file=sys.stderr)
        return False
    if path.is_file():
        try:
            current = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as error:
            print(f'Leaving {path} alone ({error}).', file=sys.stderr)
            return False
        if OWNED_LUA_MARKER not in current:
            print(f'Leaving {path} alone because Groundwork did not write it.', file=sys.stderr)
            return False
    rendered = render_binding(focus_class).encode()
    if path.is_file():
        try:
            if path.read_bytes() == rendered:
                return False
        except OSError:
            pass
    try:
        atomic_write(path, rendered, 0o644)
    except OSError as error:
        print(f'Could not write {path} ({error}).', file=sys.stderr)
        raise
    return True


def bindings_path(environ):
    return config_dir(environ) / 'hypr' / 'bindings.lua'


def append_binding_line(environ):
    """Append the one dofile line. Return CHANGED, 0, or 1. Never follow a symlink."""
    path = bindings_path(environ)
    if path.is_symlink() or contains_symlink(path):
        print(f'Refusing to edit a symlink: {path}', file=sys.stderr)
        print('Add the Antigravity dofile line yourself, or move that symlink aside. '
              'See docs/apps/antigravity.md.', file=sys.stderr)
        return 0
    if path.exists() and not path.is_file():
        print(f'Refusing to edit {path} because it is not a regular file.', file=sys.stderr)
        return 0
    line = (BINDING_LINE + '\n').encode()
    if not path.exists():
        atomic_write(path, line, 0o644)
        return CHANGED
    original = path.read_bytes()
    try:
        text = original.decode('utf-8')
    except UnicodeError:
        print(f'Leaving {path} alone because it is not UTF-8.', file=sys.stderr)
        return 0
    if BINDING_LINE in text.splitlines():
        return 0
    if original.endswith(b'\n') or original == b'':
        updated = original + line
    else:
        updated = original + b'\n' + line
    atomic_write(path, updated, stat.S_IMODE(path.stat().st_mode))
    return CHANGED


def remove_binding_line(environ):
    """Remove only the dofile line. A symlink is left alone. Return CHANGED or 0."""
    path = bindings_path(environ)
    if not path.exists() or path.is_symlink() or contains_symlink(path) or not path.is_file():
        return 0
    try:
        original = path.read_bytes()
        text = original.decode('utf-8')
    except (OSError, UnicodeError):
        return 0
    lines = text.splitlines(keepends=True)
    kept = [line for line in lines if line.rstrip('\r\n') != BINDING_LINE]
    if len(kept) == len(lines):
        return 0
    updated = ''.join(kept).encode()
    if not updated.strip():
        path.unlink()
        return CHANGED
    atomic_write(path, updated, stat.S_IMODE(path.stat().st_mode))
    return CHANGED


def bind(environ):
    desktops = package_desktop_files(environ)
    ensure_launcher(environ, desktops)
    focus = focus_class_from(desktops) if desktops else 'antigravity'
    changed = write_lua(environ, focus)
    status = append_binding_line(environ)
    if status == 1:
        return 1
    if changed or status == CHANGED:
        return CHANGED
    return 0


def main(argv=None, environ=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    environ = dict(os.environ if environ is None else environ)
    if argv[:1] == ['merge-settings'] and len(argv) == 2:
        return merge_settings(argv[1], environ)
    if argv == ['bind']:
        try:
            return bind(environ)
        except OSError as error:
            print(f'Antigravity binding was not applied ({error}).', file=sys.stderr)
            return 1
    if argv == ['unbind']:
        try:
            return remove_binding_line(environ)
        except OSError as error:
            print(f'Antigravity binding line was left in place ({error}).', file=sys.stderr)
            return 1
    print('Usage: groundwork-antigravity.py merge-settings <absolute-agy> | bind | unbind', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
