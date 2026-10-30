#!/usr/bin/env python3
# Groundwork Omarchy commit identity guard (decision 0017)
"""Install groundwork-core's commit identity guard (core/docs/commit-identity.md) for this user.

Scope is the user's own repositories. Hooks live in ~/.config/groundwork/git-hooks.
The file commit-identity-guard.sh there is a symlink to the pinned
core/commit-guard/commit-identity-guard.sh; nothing is copied. core.hooksPath is
NOT set globally. It lives in ~/.config/groundwork/commit-guard.gitconfig, which
the XDG git config (~/.config/git/config) includes through
includeIf "hasconfig:remote.*.url:..." for the owner's GitHub URL forms and any
scp-style host alias such as git@github-personal:owner/repo (git 2.36 or newer; older git warns and skips). Work repositories and hook tools
such as pre-commit are untouched. If the user already sets core.hooksPath
anywhere, nothing is written. Every documented hook name forwards to the
repository's own .git/hooks/<name>.

Gaps: a push straight to an owned URL from a repository with no owned remote is
not guarded, and owned repositories with their own core.hooksPath (husky,
lefthook) bypass the guard; `doctor` lists those under the code directory. URLs
with userinfo (https://user@github.com/...) are not matched.

Opt out with ~/.config/groundwork/commit-guard-off (`off` creates it, `on`
removes it), or GROUNDWORK_COMMIT_GUARD=off for one command. The hooks check
both at run time, and `apply` changes nothing while the file exists.

It also fills in Claude Code `attribution.commit` and `attribution.pr` as ""
in ~/.claude/settings.json (CLAUDE_CONFIG_DIR honored) for keys the user has not
set, with a numbered backup, and creates an empty denylist when none exists.
No value is ever written into the denylist.

Subcommands: `apply [--guard PATH]`, `doctor`, `off`, `on`.
Exit status: 0 done or nothing to do, 1 a step failed, 2 usage.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

MARKER = '# Groundwork Omarchy commit-guard'
HOOK_NAMES = (
    'applypatch-msg', 'pre-applypatch', 'post-applypatch', 'pre-commit', 'pre-merge-commit',
    'prepare-commit-msg', 'commit-msg', 'post-commit', 'pre-rebase', 'post-checkout',
    'post-merge', 'post-rewrite', 'pre-push', 'pre-auto-gc', 'reference-transaction',
    'push-to-checkout', 'sendemail-validate', 'fsmonitor-watchman', 'post-index-change',
    'p4-changelist', 'p4-prepare-changelist', 'p4-post-changelist', 'p4-pre-submit',
    'pre-receive', 'update', 'proc-receive', 'post-receive', 'post-update',
)
OWNER = 'jasondockery'
MIN_GIT = (2, 36)
DISPATCHER = f'''#!/usr/bin/env bash
{MARKER}
# Runs the pinned commit identity guard for commit-msg and pre-push, then the
# repository's own hook of the same name.
name="$(basename "$0")"
here="$(cd "$(dirname "$0")" && pwd)"
off_flag="${{XDG_CONFIG_HOME:-$HOME/.config}}/groundwork/commit-guard-off"
stdin_copy=""
trap '[ -n "$stdin_copy" ] && rm -f "$stdin_copy"' EXIT

guard_active=1
if [ -e "$off_flag" ] || [ "${{GROUNDWORK_COMMIT_GUARD:-}}" = off ]; then
  guard_active=0
elif [ ! -x "$here/commit-identity-guard.sh" ]; then
  guard_active=0
  echo "Groundwork: the pinned commit identity guard is missing ($here/commit-identity-guard.sh); skipping it. Run groundwork-update." >&2
fi

if [ "$guard_active" = 1 ]; then
  case "$name" in
    commit-msg)
      "$here/commit-identity-guard.sh" commit-msg "$@" || rc=$?
      ;;
    pre-push)
      stdin_copy="$(mktemp)"
      cat >"$stdin_copy"
      "$here/commit-identity-guard.sh" pre-push "$@" <"$stdin_copy" || rc=$?
      ;;
  esac
  if [ "${{rc:-0}}" != 0 ]; then
    {{
      echo "Groundwork blocked this $name. Fix the message or identity, or opt out:"
      echo "  one command:  GROUNDWORK_COMMIT_GUARD=off git ..."
      echo "  everywhere:   groundwork-commit-guard off"
    }} >&2
    exit "$rc"
  fi
fi

common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
local_hook="$common/hooks/$name"
[ -x "$local_hook" ] && [ -f "$local_hook" ] || exit 0
if [ "$(cd "$(dirname "$local_hook")" && pwd -P)/$name" = "$(cd "$here" && pwd -P)/$name" ]; then exit 0; fi
if [ -n "$stdin_copy" ]; then
  "$local_hook" "$@" <"$stdin_copy"
else
  exec "$local_hook" "$@"
fi
'''


def home():
    return Path(os.environ.get('HOME') or Path.home())


def config_home():
    xdg = os.environ.get('XDG_CONFIG_HOME')
    return Path(xdg) if xdg and os.path.isabs(xdg) else home() / '.config'


def groundwork_dir():
    return config_home() / 'groundwork'


def hooks_dir():
    return groundwork_dir() / 'git-hooks'


def off_flag():
    return groundwork_dir() / 'commit-guard-off'


def denylist_path():
    configured = os.environ.get('GROUNDWORK_COMMIT_DENYLIST')
    return Path(configured) if configured else groundwork_dir() / 'commit-denylist'


def claude_settings():
    configured = os.environ.get('CLAUDE_CONFIG_DIR')
    return (Path(configured) if configured else home() / '.claude') / 'settings.json'


def say(message):
    print(f'Groundwork commit identity guard: {message}')


def write_atomic(path, text, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.groundwork-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def next_backup(path):
    number = 1
    while Path(f'{path}.bak.{number}').exists():
        number += 1
    return Path(f'{path}.bak.{number}')


def effective_hooks_path():
    """What git resolves for core.hooksPath outside any repository, or ''."""
    with tempfile.TemporaryDirectory(prefix='groundwork-hooks-') as neutral:
        result = subprocess.run(['git', 'config', '--get', 'core.hooksPath'], cwd=neutral,
                                capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def install_hook_files(guard):
    changed = False
    hooks = hooks_dir()
    hooks.mkdir(parents=True, exist_ok=True)
    dispatch = hooks / 'dispatch'
    if dispatch.is_symlink() or not dispatch.is_file() or dispatch.read_text() != DISPATCHER:
        if dispatch.is_symlink():
            dispatch.unlink()
        write_atomic(dispatch, DISPATCHER, 0o755)
        changed = True
    elif not os.access(dispatch, os.X_OK):
        dispatch.chmod(0o755)
    links = [('commit-identity-guard.sh', str(guard))] + [(name, 'dispatch') for name in HOOK_NAMES]
    for name, target in links:
        link = hooks / name
        if link.is_symlink() and os.readlink(link) == target:
            continue
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        changed = True
    return changed


def owner_glob():
    return ''.join(f'[{c}{c.upper()}]' if c.upper() != c else c for c in OWNER)


def include_keys():
    glob = owner_glob()
    # The last prefix matches any scp-style host alias (git@github-personal:owner/repo).
    return [f'includeIf.hasconfig:remote.*.url:{prefix}{glob}/**.path' for prefix in (
        'https://github.com/', 'git@github.com:', 'ssh://git@github.com/', '*:')]


def git_version():
    result = subprocess.run(['git', '--version'], capture_output=True, text=True)
    parts = result.stdout.split()
    try:
        major, minor = parts[2].split('.')[:2]
        return int(major), int(minor)
    except (IndexError, ValueError):
        return None


def config_value(file, key):
    result = subprocess.run(['git', 'config', '--file', str(file), '--get', key],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def set_hooks_path():
    """Scope core.hooksPath to repositories with an owned GitHub remote."""
    hooks = str(hooks_dir())
    target = config_home() / 'git' / 'config'
    include = groundwork_dir() / 'commit-guard.gitconfig'
    changed = False
    # Undo a global hooksPath an earlier version of this installer wrote.
    if target.is_file() and config_value(target, 'core.hooksPath') == hooks:
        subprocess.run(['git', 'config', '--file', str(target), '--unset', 'core.hooksPath'], check=True)
        changed = True
    version = git_version()
    current = effective_hooks_path()
    if version is None or version < MIN_GIT:
        say('git 2.36 or newer is needed to scope the hooks to your repositories (includeIf hasconfig); '
            'skipped hook activation.')
        return changed
    if current:
        say(f'core.hooksPath is already set to {current}; left alone, so the guard hooks are not active. '
            f'Chain {hooks}/commit-msg and {hooks}/pre-push from it to use them.')
        return changed
    text = f'[core]\n\thooksPath = {hooks}\n'
    if not include.is_file() or include.read_text() != text:
        write_atomic(include, text, 0o644)
        changed = True
    target.parent.mkdir(parents=True, exist_ok=True)
    for key in include_keys():
        if config_value(target, key) != str(include):
            subprocess.run(['git', 'config', '--file', str(target), key, str(include)], check=True)
            changed = True
    return changed


def ensure_denylist():
    path = denylist_path()
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    say(f'created an empty denylist at {path} (one case-insensitive substring per line).')
    return True


def merge_attribution():
    """Set attribution.commit and attribution.pr to "" only where the user has not set them."""
    path = claude_settings()
    real = Path(os.path.realpath(path))
    document = {}
    existed = real.exists()
    if existed:
        try:
            document = json.loads(real.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            document = None
        if not isinstance(document, dict):
            say(f'{path} is not a JSON object; left untouched. Set "attribution": {{"commit": "", "pr": ""}} yourself.')
            return False
    attribution = document.get('attribution', {})
    if not isinstance(attribution, dict):
        return False
    missing = [key for key in ('commit', 'pr') if key not in attribution]
    if not missing:
        return False
    for key in missing:
        attribution[key] = ''
    document['attribution'] = attribution
    mode = real.stat().st_mode & 0o777 if existed else 0o644
    if existed:
        shutil.copy2(real, next_backup(real))
    write_atomic(real, json.dumps(document, indent=2, ensure_ascii=False) + '\n', mode)
    say(f'set Claude Code attribution ({", ".join(missing)}) to empty in {path}'
        + ('; a numbered .bak.N copy was kept.' if existed else '.'))
    return True


def apply(guard):
    if off_flag().exists():
        say(f'opted out ({off_flag()} exists); nothing installed or changed.')
        return 0
    if not guard.is_file():
        say(f'{guard} is not in this checkout (is the core submodule checked out?); skipped.')
        return 0
    try:
        changed = install_hook_files(guard)
        changed = set_hooks_path() or changed
        changed = ensure_denylist() or changed
        changed = merge_attribution() or changed
    except (OSError, subprocess.CalledProcessError) as error:
        print(f'Groundwork commit identity guard: not fully applied ({error}).', file=sys.stderr)
        return 1
    if changed:
        say(f"installed; active in repositories with a remote under github.com/{OWNER}. Run 'groundwork-commit-guard doctor' to check.")
    return 0


def bypassing_repos(limit=200):
    """Owned repositories under the code directory whose own core.hooksPath bypasses the guard."""
    root = Path(os.environ.get('GROUNDWORK_CODE_DIR') or home() / 'code')
    found = []
    if not root.is_dir():
        return found
    patterns = (f':{OWNER}/', f'/{OWNER}/')
    seen = 0
    for base, dirs, files in os.walk(root):
        depth = len(Path(base).relative_to(root).parts)
        if '.git' in dirs or '.git' in files:
            dirs[:] = []
            seen += 1
            local = subprocess.run(['git', '-C', base, 'config', '--local', '--get', 'core.hooksPath'],
                                   capture_output=True, text=True)
            urls = subprocess.run(['git', '-C', base, 'config', '--local', '--get-regexp', r'^remote\..*\.url$'],
                                  capture_output=True, text=True)
            if local.returncode == 0 and any(p in urls.stdout.lower() for p in patterns):
                found.append(base)
        elif depth >= 2:
            dirs[:] = []
        if seen >= limit:
            break
    return sorted(found)


def doctor():
    print('Commit identity guard')
    guard = hooks_dir() / 'commit-identity-guard.sh'
    if off_flag().exists():
        print(f'  note  opted out: {off_flag()} exists, so the hooks skip the guard. Run: groundwork-commit-guard on')
    if not os.access(guard, os.X_OK):
        print(f'  warn  not installed at {guard}; run groundwork-update.')
        return 0
    current = effective_hooks_path()
    target = config_home() / 'git' / 'config'
    scoped = (groundwork_dir() / 'commit-guard.gitconfig').is_file() and all(
        config_value(target, key) for key in include_keys())
    if current:
        print(f'  warn  core.hooksPath is {current} for every repository, so the guard hooks are not scoped or active; '
              f'chain {hooks_dir()}/commit-msg and {hooks_dir()}/pre-push from it.')
    elif scoped:
        print('  ok    hooks apply to repositories with a github.com remote owned by this user; '
              'other repositories are untouched')
    else:
        print('  warn  the owned-repository hook scope is not set up (needs git 2.36 or newer); '
              'run groundwork-update.')
    print('  note  not guarded: a push straight to an owned URL from a repository with no owned remote, '
          'and owned repositories with their own core.hooksPath (husky, lefthook), and remote URLs with userinfo (https://user@github.com/...).')
    for repo in bypassing_repos():
        print(f'  warn  guard bypassed by its own core.hooksPath: {repo}')
    result = subprocess.run([str(guard), 'doctor'], capture_output=True, text=True)
    for line in (result.stdout + result.stderr).splitlines():
        print('  ' + line)
    return 0


def set_opt_out(enabled):
    flag = off_flag()
    if enabled:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        say(f'off. The hooks skip the guard while {flag} exists. Turn it back on with: groundwork-commit-guard on')
    else:
        flag.unlink(missing_ok=True)
        say('on. Run groundwork-update to install anything that was skipped while it was off.')
    return 0


def install_command():
    """Copy this helper to ~/.local/bin/groundwork-commit-guard; never replace a foreign file."""
    target = home() / '.local/bin/groundwork-commit-guard'
    content = Path(__file__).read_text()
    if target.is_symlink() or (target.exists() and MARKER not in target.read_text(errors='replace')):
        say(f'{target} belongs to another setup; left alone.')
        return
    if target.exists() and target.read_text() == content and os.access(target, os.X_OK):
        return
    write_atomic(target, content, 0o755)


def main(argv):
    args = argv[1:]
    if args[:1] == ['apply'] and len(args) in (1, 3) and (len(args) == 1 or args[1] == '--guard'):
        guard = Path(args[2]) if len(args) == 3 else Path(__file__).resolve().parents[1] / 'core/commit-guard/commit-identity-guard.sh'
        status = apply(guard)
        if status == 0:
            try:
                install_command()
            except OSError as error:
                print(f'Groundwork commit identity guard: command not installed ({error}).', file=sys.stderr)
                return 1
        return status
    if args == ['doctor']:
        return doctor()
    if args == ['off']:
        return set_opt_out(True)
    if args == ['on']:
        return set_opt_out(False)
    print(f'usage: {Path(argv[0]).name} apply [--guard PATH] | doctor | off | on', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
