#!/usr/bin/env python3
"""Report apps Groundwork installs itself that Omarchy now provides.

    omarchy-feed-watch.py report [--json] [--brief] [--online]
    omarchy-feed-watch.py dismiss <app>
    omarchy-feed-watch.py undismiss <app>

The apps and their Omarchy candidates come from omarchy-feed-watch.json at
the repo root. For each app this checks, in the order AGENTS.md "Omarchy
first" gives: the pacman packages (`omarchy pkg add`), Omarchy's own
`omarchy-install-*` commands on PATH, and the pkgs.omarchy.org edge feed (the
`omarchy` sync database). It reads the local sync databases and runs only
`pacman -Si` when one is missing. Nothing touches the network unless
`--online` is given, and then only to download the feed database to a
temporary directory.

It only reports and offers (decision 0014). It never installs, removes, or
migrates anything. `dismiss` stores the routes it saw in
$XDG_STATE_HOME/groundwork/feed-watch.json so later reports stop offering
them; a route Omarchy adds later is offered again. A migration declined at
setup (`declined_migrations` in omarchy.json) also counts as dismissed.
"""
import argparse
import datetime
import io
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

REPO = Path(__file__).resolve().parents[1]
TABLE = REPO / 'omarchy-feed-watch.json'
SYNC_DIR = Path('/var/lib/pacman/sync')
FEED_URL = 'https://pkgs.omarchy.org/edge/{arch}/omarchy.db'
FEED_REPO = 'omarchy'
STATE_FILE = 'feed-watch.json'
APP_ID = re.compile(r'^[a-z0-9][a-z0-9-]*$')
PACMAN_SECONDS = 30
DOWNLOAD_SECONDS = 30
HEADER_BYTES = 4096


def xdg_base(name, default):
    """Same rule as groundwork-update.py: empty or unset means the default."""
    value = os.environ.get(name)
    return Path(value) if value else Path(default)


def default_state_dir():
    return xdg_base('XDG_STATE_HOME', Path.home() / '.local/state') / 'groundwork'


def default_config():
    return xdg_base('XDG_CONFIG_HOME', Path.home() / '.config') / 'groundwork/omarchy.json'


def script_command():
    return shlex.quote(str(REPO / 'scripts/omarchy-feed-watch.py'))


class TableError(Exception):
    pass


def load_table(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise TableError(f'{path}: {error}') from None
    apps = value.get('apps') if isinstance(value, dict) else None
    if not isinstance(apps, list) or value.get('schema') != 1:
        raise TableError(f'{path}: expected schema 1 with an apps list')
    seen = set()
    for app in apps:
        app_id = app.get('id') if isinstance(app, dict) else None
        if not isinstance(app_id, str) or not APP_ID.match(app_id) or app_id in seen:
            raise TableError(f'{path}: app id {app_id!r} is missing, repeated, or not lowercase letters, digits, and hyphens')
        seen.add(app_id)
        for key in ('name', 'groundwork_method', 'offer'):
            if not isinstance(app.get(key), str) or not app[key]:
                raise TableError(f'{path}: {app_id} needs {key}')
        for candidate in app.get('candidates', []):
            if not isinstance(candidate, dict) or ('installer' in candidate) == ('package' in candidate):
                raise TableError(f'{path}: {app_id} has a candidate that is not exactly one installer or package')
        if not isinstance(app.get('self_install'), dict):
            raise TableError(f'{path}: {app_id} needs self_install')
    return apps


# Package databases ---------------------------------------------------------

def parse_desc(text):
    fields, key = {}, None
    for line in text.splitlines():
        if line.startswith('%') and line.endswith('%') and len(line) > 2:
            key = line[1:-1]
            fields[key] = []
        elif key and line:
            fields[key].append(line)
    return {key: values[0] for key, values in fields.items() if values}


def open_db(path):
    """A pacman sync database as a tarfile. Python 3.14 reads zstd itself."""
    try:
        return tarfile.open(path, 'r:*')
    except tarfile.TarError:
        zstd = shutil.which('zstd')
        if not zstd:
            raise
        data = subprocess.run([zstd, '-dc', str(path)], capture_output=True, check=True,
                              timeout=PACMAN_SECONDS).stdout
        return tarfile.open(fileobj=io.BytesIO(data), mode='r:')


def read_db(path, names):
    """{name: {'version', 'arch'}} for the wanted names present in one sync database."""
    found = {}
    with open_db(path) as db:
        for member in db:
            parts = member.name.split('/')
            if len(parts) != 2 or parts[1] != 'desc' or not member.isfile():
                continue
            # Entries are <name>-<pkgver>-<pkgrel>/desc. Read only those that may match.
            if parts[0].rsplit('-', 2)[0] not in names:
                continue
            desc = parse_desc(db.extractfile(member).read().decode('utf-8', 'replace'))
            if desc.get('NAME') in names:
                found[desc['NAME']] = {'version': desc.get('VERSION', ''), 'arch': desc.get('ARCH', '')}
    return found


def pacman_si(repo, name, pacman='pacman'):
    """(True, info), (False, None) when pacman says it is absent, or (None, None) when unknown."""
    if not shutil.which(pacman):
        return None, None
    try:
        result = subprocess.run([pacman, '-Si', f'{repo}/{name}'], capture_output=True, text=True,
                                timeout=PACMAN_SECONDS, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if result.returncode == 0:
        info = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition(':')
            info[key.strip()] = value.strip()
        return True, {'version': info.get('Version', ''), 'arch': info.get('Architecture', '')}
    if 'was not found' in result.stderr and 'does not exist' not in result.stderr:
        return False, None
    return None, None


class Feed:
    """Looks packages up per repo: local sync db, else the downloaded feed, else pacman -Si."""

    def __init__(self, sync_dir=SYNC_DIR, online_db=None, pacman='pacman'):
        self.sync_dir = Path(sync_dir)
        self.online_db = online_db
        self.pacman = pacman
        self.sources = {}

    def db_path(self, repo):
        if repo == FEED_REPO and self.online_db:
            return Path(self.online_db), 'downloaded'
        return self.sync_dir / f'{repo}.db', 'sync database'

    def lookup(self, wanted):
        """wanted: {repo: set(names)} -> {repo: {name: info}}, plus None for a repo that could not be read."""
        results = {}
        for repo, names in sorted(wanted.items()):
            path, kind = self.db_path(repo)
            if path.is_file():
                try:
                    results[repo] = read_db(path, set(names))
                    synced = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
                    self.sources[repo] = {'via': kind, 'path': str(path), 'synced': synced}
                    continue
                except (OSError, tarfile.TarError, subprocess.SubprocessError) as error:
                    self.sources[repo] = {'via': kind, 'path': str(path), 'error': str(error)}
            found, unknown = {}, False
            for name in sorted(names):
                present, info = pacman_si(repo, name, self.pacman)
                if present is None:
                    unknown = True
                elif present:
                    found[name] = info
            if unknown:
                results[repo] = None
                self.sources.setdefault(repo, {}).update({'via': 'unavailable'})
            else:
                results[repo] = found
                self.sources[repo] = {'via': 'pacman -Si'}
        return results


def download_feed(url, directory):
    target = Path(directory) / 'omarchy.db'
    with urllib.request.urlopen(url, timeout=DOWNLOAD_SECONDS) as response, open(target, 'wb') as out:
        shutil.copyfileobj(response, out)
    return target


# Omarchy installers --------------------------------------------------------

def installers_on_path(path_value=None):
    """{name: path} for every omarchy-install-* on PATH, first match wins."""
    found = {}
    for directory in (path_value if path_value is not None else os.environ.get('PATH', '')).split(os.pathsep):
        try:
            entries = sorted(Path(directory).iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith('omarchy-install-') and entry.name not in found \
                    and entry.is_file() and os.access(entry, os.X_OK):
                found[entry.name] = str(entry)
    return found


def hidden_installer(path):
    """Omarchy marks helper installers that are not meant to be run directly."""
    try:
        with open(path, 'rb') as handle:
            head = handle.read(HEADER_BYTES).decode('utf-8', 'replace')
    except OSError:
        return False
    return '# omarchy:hidden=true' in head


# Machine probe -------------------------------------------------------------

def self_install_present(probe):
    """True or False when the table says how to see Groundwork's own install here, else None."""
    if not probe:
        return None
    if 'flatpak' in probe:
        return (Path.home() / '.local/share/flatpak/app' / probe['flatpak']).is_dir()
    if 'path' in probe:
        return Path(os.path.expanduser(probe['path'])).exists()
    return None


# State ---------------------------------------------------------------------

class StateError(Exception):
    pass


def read_state(state_dir):
    path = Path(state_dir) / STATE_FILE
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return {'dismissed': {}}
    except (OSError, ValueError) as error:
        raise StateError(f'{path}: {error}') from None
    dismissed = value.get('dismissed') if isinstance(value, dict) else None
    if not isinstance(dismissed, dict) or not all(
            isinstance(routes, list) and all(isinstance(r, str) for r in routes) for routes in dismissed.values()):
        raise StateError(f'{path}: expected {{"dismissed": {{app: [route, ...]}}}}')
    return {'dismissed': dismissed}


def write_state(state_dir, state):
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', dir=directory, prefix='.feed-watch.', delete=False)
    try:
        with handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write('\n')
        os.replace(handle.name, directory / STATE_FILE)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def declined_migrations(config):
    try:
        value = json.loads(Path(config).read_text())
    except (OSError, ValueError):
        return set()
    names = value.get('declined_migrations') if isinstance(value, dict) else None
    return {n for n in names if isinstance(n, str)} if isinstance(names, list) else set()


# Evaluation ----------------------------------------------------------------

def route_key(provider):
    if provider['how'] == 'installer':
        return f"installer:{provider['installer']}"
    return f"package:{provider['repo']}/{provider['name']}"


def wanted_packages(apps):
    wanted = {}
    for app in apps:
        for candidate in app.get('candidates', []):
            if 'package' in candidate:
                wanted.setdefault(candidate.get('repo', FEED_REPO), set()).add(candidate['package'])
            for name in candidate.get('requires_packages', []):
                wanted.setdefault(FEED_REPO, set()).add(name)
    return wanted


def evaluate(apps, packages, installers, state, declined, today):
    results = []
    for app in apps:
        providers, unknown, listed = [], False, set()
        for candidate in app.get('candidates', []):
            if 'installer' in candidate:
                name = candidate['installer']
                listed.add(name)
                if name not in installers:
                    continue
                needs = candidate.get('requires_packages', [])
                feed = packages.get(FEED_REPO)
                if needs and feed is None:
                    unknown = True
                    continue
                if any(n not in feed for n in needs):
                    continue
                command = candidate.get('command', name).replace('{repo}', shlex.quote(str(REPO)))
                providers.append({'how': 'installer', 'installer': name, 'path': installers[name],
                                  'command': command, 'menu': candidate.get('menu'), 'listed': True})
            else:
                repo, name = candidate.get('repo', FEED_REPO), candidate['package']
                found = packages.get(repo)
                if found is None:
                    unknown = True
                elif name in found:
                    providers.append({'how': 'package', 'repo': repo, 'name': name,
                                      'version': found[name]['version'], 'arch': found[name]['arch'],
                                      'command': f'omarchy pkg add {name}'})
        for keyword in app.get('installer_keywords', []):
            for name, path in sorted(installers.items()):
                if keyword in name and name not in listed and not hidden_installer(path):
                    listed.add(name)
                    providers.append({'how': 'installer', 'installer': name, 'path': path,
                                      'command': name, 'menu': None, 'listed': False})
        # An Omarchy installer comes before installing its package directly (AGENTS.md).
        providers.sort(key=lambda p: p['how'] != 'installer')
        provided = True if providers else (None if unknown else False)

        present = self_install_present(app.get('probe'))
        routes = sorted(route_key(p) for p in providers)
        dismissed_routes = set(state['dismissed'].get(app['id'], []))
        dismissed_by = None
        if app.get('migration_id') in declined:
            dismissed_by = 'declined_migrations'
        elif routes and set(routes) <= dismissed_routes:
            dismissed_by = 'dismiss'

        offer = None
        if providers and present is not False:
            offer = {'text': app['offer'], 'command': providers[0]['command'],
                     'dismiss': f"{script_command()} dismiss {app['id']}",
                     'dismissed': dismissed_by is not None, 'dismissed_by': dismissed_by}

        record = dict(app['self_install'])
        recheck = record.get('recheck')
        record['recheck_due'] = not recheck or recheck <= today
        results.append({
            'id': app['id'], 'name': app['name'], 'doc': app.get('doc'),
            'groundwork_method': app['groundwork_method'],
            'installed_by_groundwork': present,
            'provided': provided, 'providers': providers, 'routes': routes,
            'offer': offer, 'self_install': record,
        })
    return results


# Output --------------------------------------------------------------------

def describe(provider):
    if provider['how'] == 'installer':
        menu = f", {provider['menu']}" if provider.get('menu') else ''
        return f"{provider['installer']} (installer{menu})"
    return f"{provider['repo']}/{provider['name']} {provider['version']}"


def recheck_text(record):
    if not record.get('recheck'):
        return 'No recheck date on file.'
    when = 'was due' if record['recheck_due'] else 'by'
    return f"Recheck {when} {record['recheck']}."


def human_report(report, brief=False):
    lines = []
    if not brief:
        sources = '; '.join(
            f"{repo}: {s.get('path', s['via'])}" + (f" (synced {s['synced']})" if s.get('synced') else '')
            + (f" unreadable: {s['error']}" if s.get('error') else '')
            + (' unavailable, packages unknown' if s['via'] == 'unavailable' else '')
            for repo, s in sorted(report['sources'].items()))
        mode = 'online' if report['online'] else 'offline'
        lines.append(f"Omarchy feed watch, {report['checked']}, {mode}. {sources}.")
    for app in report['apps']:
        record, offer = app['self_install'], app['offer']
        if brief:
            if offer and not offer['dismissed']:
                keep = ('Groundwork keeps its own install for now' if record.get('needed')
                        else 'Groundwork no longer needs its own install')
                lines.append(f"{app['name']} ({app['id']}): Omarchy provides {describe(app['providers'][0])}. {keep}.")
            continue
        if app['provided'] is None:
            lines.append(f"{app['name']}: unknown, the package database could not be read. "
                         f"Groundwork's method: {app['groundwork_method']}.")
            continue
        if not app['provided']:
            line = f"{app['name']}: not provided by Omarchy. Groundwork's method stays: {app['groundwork_method']}. {recheck_text(record)}"
            if record['recheck_due'] and record.get('evidence'):
                line += f" Last evidence: {record['evidence']}"
            lines.append(line)
            continue
        lines.append(f"{app['name']}: Omarchy provides {'; '.join(describe(p) for p in app['providers'])}. "
                     f"Groundwork's method: {app['groundwork_method']}.")
        if app['installed_by_groundwork'] is False:
            lines.append('  Not installed by Groundwork on this machine, so nothing is offered.')
        elif offer and offer['dismissed']:
            how = ('declined at setup; ./install.sh --migrate-to-omarchy-installers asks again'
                   if offer['dismissed_by'] == 'declined_migrations'
                   else f"dismissed; {script_command()} undismiss {app['id']} shows it again")
            lines.append(f'  Offer {how}.')
        elif offer:
            lines.append(f"  Offer: {offer['text']} Command: {offer['command']}")
            lines.append(f"  To stop this offer: {offer['dismiss']}")
        state = 'still needed' if record.get('needed') else 'no longer needed'
        lines.append(f"  Self-install {state}: {record.get('evidence', 'no evidence on file.')} "
                     f"Checked {record.get('checked', 'never')}. {recheck_text(record)}")
    if brief and lines:
        lines.append(f"Details and commands: {script_command()} report. "
                     f"To stop one offer: {script_command()} dismiss <app>.")
    return '\n'.join(lines) + ('\n' if lines else '')


# CLI -----------------------------------------------------------------------

def build_report(args, online_db=None):
    apps = load_table(args.table)
    try:
        state = read_state(args.state_dir)
    except StateError as error:
        print(f'Ignoring unreadable dismissals: {error}', file=sys.stderr)
        state = {'dismissed': {}}
    feed = Feed(args.sync_dir, online_db=online_db, pacman=args.pacman)
    packages = feed.lookup(wanted_packages(apps))
    installers = installers_on_path()
    today = args.today or datetime.date.today().isoformat()
    results = evaluate(apps, packages, installers, state, declined_migrations(args.config), today)
    return {'schema': 1, 'checked': today, 'online': online_db is not None,
            'sources': feed.sources, 'apps': results}


def run_report(args):
    if args.online:
        arch = args.arch or platform.machine()
        url = args.feed_url or FEED_URL.format(arch=arch)
        with tempfile.TemporaryDirectory(prefix='omarchy-feed-watch.') as directory:
            try:
                db = download_feed(url, directory)
            except OSError as error:
                print(f'Could not download {url}: {error}', file=sys.stderr)
                return 1
            report = build_report(args, online_db=db)
    else:
        report = build_report(args)
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write('\n')
    else:
        sys.stdout.write(human_report(report, brief=args.brief))
    return 0


def find_app(report, app_id):
    for app in report['apps']:
        if app['id'] == app_id:
            return app
    known = ', '.join(a['id'] for a in report['apps'])
    print(f'No app {app_id!r} in the feed watch table. Known: {known}.', file=sys.stderr)
    return None


def run_dismiss(args):
    try:
        state = read_state(args.state_dir)
    except StateError as error:
        print(f'{error}. Fix or remove that file, then rerun. Nothing was changed.', file=sys.stderr)
        return 1
    report = build_report(args)
    app = find_app(report, args.app)
    if app is None:
        return 2
    if args.command == 'undismiss':
        if state['dismissed'].pop(args.app, None) is None:
            print(f"{app['name']} was not dismissed. Nothing was changed.")
            return 0
        write_state(args.state_dir, state)
        print(f"{app['name']} is offered again when Omarchy provides it.")
        return 0
    if not app['routes']:
        print(f"Omarchy does not provide {app['name']} now, so there is nothing to dismiss. Nothing was changed.",
              file=sys.stderr)
        return 1
    state['dismissed'][args.app] = app['routes']
    write_state(args.state_dir, state)
    print(f"Dismissed the offer for {app['name']} ({', '.join(app['routes'])}). "
          f"A route Omarchy adds later is offered again. Undo: {script_command()} undismiss {args.app}")
    return 0


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--table', default=os.environ.get('GROUNDWORK_FEED_WATCH_TABLE') or TABLE)
    common.add_argument('--state-dir', type=Path, default=None,
                        help='where dismissals are kept (default $XDG_STATE_HOME/groundwork)')
    common.add_argument('--config', type=Path, default=None,
                        help="setup choices with declined_migrations (default $XDG_CONFIG_HOME/groundwork/omarchy.json)")
    common.add_argument('--sync-dir', type=Path, default=SYNC_DIR, help=argparse.SUPPRESS)
    common.add_argument('--pacman', default='pacman', help=argparse.SUPPRESS)
    common.add_argument('--today', default=None, help=argparse.SUPPRESS)
    top = argparse.ArgumentParser(prog='omarchy-feed-watch.py', description=__doc__.split('\n\n')[0])
    commands = top.add_subparsers(dest='command')
    report = commands.add_parser('report', parents=[common], help='report what Omarchy provides (default)')
    report.add_argument('--json', action='store_true', help='print JSON instead of text')
    report.add_argument('--brief', action='store_true', help='only one line per offer not dismissed')
    report.add_argument('--online', action='store_true',
                        help='download the pkgs.omarchy.org edge feed database instead of the local copy')
    report.add_argument('--arch', default=None, help=argparse.SUPPRESS)
    report.add_argument('--feed-url', default=None, help=argparse.SUPPRESS)
    for name in ('dismiss', 'undismiss'):
        command = commands.add_parser(name, parents=[common],
                                      help=f'{name} the offer for one app')
        command.add_argument('app')
    return top


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith('--') and argv[0] not in ('-h', '--help'):
        argv.insert(0, 'report')
    args = parser().parse_args(argv)
    args.state_dir = args.state_dir or default_state_dir()
    args.config = args.config or default_config()
    try:
        if args.command == 'report':
            return run_report(args)
        return run_dismiss(args)
    except TableError as error:
        print(f'Feed watch table is invalid: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
