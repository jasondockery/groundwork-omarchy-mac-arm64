#!/usr/bin/env python3
# Groundwork doctor (decision 0021)
"""Check this machine for problems with a known fix and print that fix. Changes nothing.

  groundwork-doctor [--brief]

Checks:
  host tools  lsof, ps with `-o lstart=`, rg, jq, shellcheck, ssh, and git, the
              binaries common scripts and test harnesses assume (issue #130).
              Each missing tool is printed with the exact command that installs it.
  temp storage  /tmp's backing (tmpfs or disk), the tmp.mount mask (or that its
              state is unknown), a reboot still pending after the mask, an
              /etc/fstab /tmp line or a custom /etc/systemd/system/tmp.mount
              unit (setup skips the mask for either), upstream tmpfiles ageing
              of /tmp and a masked systemd-tmpfiles-clean.timer, and the scratch roots
              omarchy.json names (decision 0022, issue #138). A checkout without
              the temp-storage helper reports that gap.
  antigravity agy from the antigravity-cli package (issue 162). Missing agy is
              reported when the setup choice is on. A version older than 1.5.1
              cannot satisfy T3 Code's Antigravity provider. 1.5.1 through
              1.5.11 still runs; 1.5.12 or newer is recommended. The probe is
              `agy --version` only. `--without-antigravity` skips this check.
  storage     TRIM through LUKS and the plocate prune drop-in (decision 0023).
              Each gap is printed with the command that fixes it. A directory
              property cannot choose a zstd level, so that check does not set
              one. A checkout without the storage helper reports that gap.
              Nothing here enables discards, starts a timer, or sets a property.
              --help and bad usage do not need either helper.

--brief prints only problems, one line each, and nothing when every check
passes. update-all runs it that way, after the update, next to its other reports.

Exit status: 0 when every check passes (or with --brief), 1 when a check
fails, 2 for bad usage.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

# (command, Arch package, install command). Setup and update-all install lsof
# and shellcheck; Omarchy ships the rest. shellcheck has no aarch64 build in
# Arch Linux ARM or the Omarchy feed (checked 2026-11-03), so it comes from the
# AUR's shellcheck-bin, upstream's static release. See decision 0021.
HOST_TOOLS = (
    ('lsof', 'lsof', 'sudo pacman -S --needed lsof'),
    ('ps', 'procps-ng', 'sudo pacman -S --needed procps-ng'),
    ('rg', 'ripgrep', 'sudo pacman -S --needed ripgrep'),
    ('jq', 'jq', 'sudo pacman -S --needed jq'),
    ('shellcheck', 'shellcheck-bin', 'omarchy pkg aur add shellcheck-bin'),
    ('ssh', 'openssh', 'sudo pacman -S --needed openssh'),
    ('git', 'git', 'sudo pacman -S --needed git'),
)
PS_PROBE_SECONDS = 5
# T3 Code's Antigravity provider needs agy 1.5.1. 1.5.12 or newer is what
# current Omarchy feeds ship and what this layer recommends (issue 162).
AGY_MINIMUM = (1, 5, 1)
AGY_RECOMMENDED = (1, 5, 12)
AGY_PROBE_SECONDS = 5
AGY_VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)')


def load_storage():
    """The storage helper beside this script, or None when this checkout does not have it."""
    path = Path(__file__).with_name('groundwork-storage.py')
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location('groundwork_storage', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STORAGE = load_storage()


def ps_supports_lstart(ps, environ):
    """True when this ps prints a start time for `-o lstart=` (procps-ng does; busybox does not)."""
    try:
        result = subprocess.run([ps, '-o', 'lstart=', '-p', str(os.getpid())], env=environ,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                errors='replace', timeout=PS_PROBE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() != ''


def host_tool_problems(environ):
    """One line per missing or unusable host tool; empty when all are present."""
    path = environ.get('PATH', '')
    problems = []
    for command, package, install in HOST_TOOLS:
        found = shutil.which(command, path=path)
        if not found:
            problems.append(f'{command} is missing ({package}). Install it with: {install}')
        elif command == 'ps' and not ps_supports_lstart(found, environ):
            problems.append(f'{found} does not support `ps -o lstart=`, which lock-liveness checks read. '
                            f'Install {package} with: {install}')
    return problems


def antigravity_opted_out(environ):
    """True only when omarchy.json saves antigravity as false. A missing or broken file stays on."""
    home = environ.get('HOME') or ''
    if not home:
        return False
    config_home = environ.get('XDG_CONFIG_HOME') or str(Path(home) / '.config')
    path = Path(config_home) / 'groundwork' / 'omarchy.json'
    try:
        if path.is_symlink() or not path.is_file():
            return False
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        return False
    return isinstance(value, dict) and value.get('antigravity') is False


def parse_agy_version(text):
    """The first x.y.z in `text`, or None."""
    match = AGY_VERSION.search(text or '')
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def read_agy_version(agy, environ):
    """Version tuple from `agy --version`, or None when it cannot be read.

    stdin is closed and the wait is bounded. This does not pass --help.
    """
    try:
        result = subprocess.run([agy, '--version'], env=environ, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, errors='replace',
                                timeout=AGY_PROBE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_agy_version(result.stdout + '\n' + result.stderr)


def antigravity_report(environ):
    """(problems, summary), or None when Antigravity is opted out."""
    if antigravity_opted_out(environ):
        return None
    found = shutil.which('agy', path=environ.get('PATH', ''))
    if not found:
        return (['agy is missing (antigravity-cli). Install it with: omarchy-install-ai-cli agy'], '')
    version = read_agy_version(found, environ)
    if version is None:
        return ([f'{found} is installed but its version could not be read from agy --version.'], '')
    rendered = '.'.join(str(part) for part in version)
    if version < AGY_MINIMUM:
        return ([f'agy {rendered} is older than 1.5.1, which T3 Code\'s Antigravity provider needs. '
                 'Update it with: omarchy update'], '')
    if version < AGY_RECOMMENDED:
        return ([f'agy {rendered} runs, and 1.5.12 or newer is recommended. Update it with: omarchy update'], '')
    return ([], f'agy {rendered}')


def temp_storage(environ):
    """(problems, summary) from scripts/groundwork-tmp.py, which sits next to this file."""
    path = Path(__file__).resolve().parent / 'groundwork-tmp.py'
    if not path.is_file():
        return ['groundwork-tmp.py is missing from this checkout.'], ''
    spec = importlib.util.spec_from_file_location('groundwork_tmp', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.report(environ)
    except (module.TmpError, OSError) as error:
        return [f'The check could not run: {error}'], ''


def storage_report(environ, brief):
    """(text, failed). A missing helper is a reported problem, not a traceback."""
    if STORAGE is None:
        line = 'groundwork-storage.py is missing from this checkout.'
        if brief:
            return f'Storage defaults: {line}\n', True
        return f'Storage defaults: 1 problem\n  {line}\n', True
    machine = STORAGE.machine_from(environ)
    if machine is None:
        return '', False
    if machine is STORAGE.REFUSED:
        line = 'GROUNDWORK_STORAGE_ROOT and INSTALL_FIXTURE are test-only. Unset them and rerun.'
        if brief:
            return f'Storage defaults: {line}\n', True
        return f'Storage defaults: 1 problem\n  {line}\n', True
    machine.deadline = STORAGE.Deadline(STORAGE.DOCTOR_SECONDS)
    return STORAGE.format_report(STORAGE.gather(machine), brief=brief)


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(prog='groundwork-doctor',
                                     description='Check this machine and print the fix for each problem. Changes nothing.')
    parser.add_argument('--brief', action='store_true', help='print only problems, one line each; exit 0 unless usage is wrong')
    args = parser.parse_args(argv)
    environ = dict(os.environ if environ is None else environ)
    names = ', '.join('ps -o lstart=' if command == 'ps' else command for command, _, _ in HOST_TOOLS)
    sections = [('Host tools', host_tool_problems(environ), names), ('Temp storage', *temp_storage(environ))]
    antigravity = antigravity_report(environ)
    if antigravity is not None:
        sections.append(('Antigravity', *antigravity))
    status = 0
    for label, problems, summary in sections:
        if args.brief:
            for line in problems:
                print(f'{label}: {line}')
        elif problems:
            status = 1
            print(f'{label}: {len(problems)} problem{"s" if len(problems) != 1 else ""}')
            for line in problems:
                print(f'  {line}')
        else:
            print(f'{label}: ok ({summary})')
    storage_text, storage_failed = storage_report(environ, brief=args.brief)
    if storage_text:
        print(storage_text, end='')
    if storage_failed and not args.brief:
        status = 1
    return status


if __name__ == '__main__':
    sys.exit(main())
