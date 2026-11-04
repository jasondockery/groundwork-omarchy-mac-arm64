#!/usr/bin/env python3
# Groundwork doctor (decision 0021)
"""Check this machine for problems with a known fix and print that fix. Changes nothing.

  groundwork-doctor [--brief]

Checks:
  host tools  lsof, ps with `-o lstart=`, rg, jq, shellcheck, ssh, and git, the
              binaries common scripts and test harnesses assume (issue #130).
              Each missing tool is printed with the exact command that installs it.

--brief prints only problems, one line each, and nothing when every check
passes. update-all runs it that way, after the update, next to its other reports.

Exit status: 0 when every check passes (or with --brief), 1 when a check
fails, 2 for bad usage.
"""
import argparse
import os
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


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(prog='groundwork-doctor',
                                     description='Check this machine and print the fix for each problem. Changes nothing.')
    parser.add_argument('--brief', action='store_true', help='print only problems and always exit 0')
    args = parser.parse_args(argv)
    environ = dict(os.environ if environ is None else environ)
    problems = host_tool_problems(environ)
    if args.brief:
        for line in problems:
            print(f'Host tools: {line}')
        return 0
    if problems:
        print(f'Host tools: {len(problems)} problem{"s" if len(problems) != 1 else ""}')
        for line in problems:
            print(f'  {line}')
        return 1
    names = ', '.join('ps -o lstart=' if command == 'ps' else command for command, _, _ in HOST_TOOLS)
    print(f'Host tools: ok ({names})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
