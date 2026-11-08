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
              omarchy.json names (decision 0022, issue #138).

--brief prints only problems, one line each, and nothing when every check
passes. update-all runs it that way, after the update, next to its other reports.

Exit status: 0 when every check passes (or with --brief), 1 when a check
fails, 2 for bad usage.
"""
import argparse
import importlib.util
import os
from pathlib import Path
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


def temp_storage(environ):
    """(problems, summary) from scripts/groundwork-tmp.py, which sits next to this file."""
    spec = importlib.util.spec_from_file_location('groundwork_tmp', Path(__file__).resolve().parent / 'groundwork-tmp.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.report(environ)
    except (module.TmpError, OSError) as error:
        return [f'The check could not run: {error}'], ''


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(prog='groundwork-doctor',
                                     description='Check this machine and print the fix for each problem. Changes nothing.')
    parser.add_argument('--brief', action='store_true', help='print only problems, one line each; exit 0 unless usage is wrong')
    args = parser.parse_args(argv)
    environ = dict(os.environ if environ is None else environ)
    names = ', '.join('ps -o lstart=' if command == 'ps' else command for command, _, _ in HOST_TOOLS)
    sections = [('Host tools', host_tool_problems(environ), names), ('Temp storage', *temp_storage(environ))]
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
    return status


if __name__ == '__main__':
    sys.exit(main())
