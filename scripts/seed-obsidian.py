#!/usr/bin/env python3
"""Seed Vim mode in a local vault without replacing existing Obsidian state."""
import argparse
import json
from pathlib import Path
import sys


def seed(vault):
    vault = Path(vault).expanduser().absolute()
    if '..' in vault.parts:
        raise ValueError('Use an absolute vault path without .. components.')
    target = vault / '.obsidian' / 'app.json'
    # Refuse indirect destinations rather than modifying a vault through a
    # symlink, including a dangling one. Existing app choices always win.
    for path in (*reversed(target.parents), target):
        if path.is_symlink():
            raise ValueError(f'Refusing a symlink in the vault path: {path}')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open('x') as handle:
            json.dump({'vimMode': True}, handle)
            handle.write('\n')
    except FileExistsError:
        if not target.is_file():
            raise ValueError(f'Expected a regular app settings file: {target}')
        print('Existing Obsidian settings preserved.')
    print(f'Open this folder as a vault in Obsidian: {vault}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vault', nargs='?',
                        default=str(Path.home() / 'Documents/Obsidian/my_obsidian_vault'))
    args = parser.parse_args()
    try:
        seed(args.vault)
    except (OSError, ValueError) as error:
        print(f'Obsidian setup stopped: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
