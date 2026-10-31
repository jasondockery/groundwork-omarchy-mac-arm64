"""Omarchy feed watch against fixture sync databases, PATHs, and HOMEs only (issue #18)."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts/omarchy-feed-watch.py'
TABLE = REPO / 'omarchy-feed-watch.json'
spec = importlib.util.spec_from_file_location('omarchy_feed_watch', SCRIPT)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def desc(name, version, arch='aarch64'):
    return f'%FILENAME%\n{name}-{version}-{arch}.pkg.tar.zst\n\n%NAME%\n{name}\n\n%VERSION%\n{version}\n\n%ARCH%\n{arch}\n\n'


def write_db(path, packages, mode='w:gz'):
    """A pacman sync database: one <name>-<version>/desc entry per package."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode) as db:
        for name, version in packages.items():
            directory = tarfile.TarInfo(f'{name}-{version}')
            directory.type = tarfile.DIRTYPE
            db.addfile(directory)
            data = desc(name, version).encode()
            entry = tarfile.TarInfo(f'{name}-{version}/desc')
            entry.size = len(data)
            db.addfile(entry, io.BytesIO(data))


def executable(path, text='#!/bin/sh\nexit 0\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


FIXTURE_TABLE = {
    'schema': 1,
    'apps': [
        {
            'id': 'viewer', 'name': 'Viewer', 'doc': 'docs/apps/viewer.md',
            'groundwork_method': 'user Flatpak org.example.Viewer',
            'probe': {'flatpak': 'org.example.Viewer'},
            'candidates': [
                {'installer': 'omarchy-install-viewer', 'command': 'omarchy-install-viewer --no-launch',
                 'menu': 'Install > Viewer'},
                {'package': 'viewer-bin'},
            ],
            'installer_keywords': ['viewer'],
            'offer': 'Move Viewer to Omarchy.',
            'self_install': {'needed': False, 'evidence': 'feed has viewer-bin', 'checked': '2026-10-01',
                             'recheck': '2026-12-01'},
        },
        {
            'id': 'vault', 'name': 'Vault', 'groundwork_method': 'user Flatpak org.example.Vault',
            'probe': {'flatpak': 'org.example.Vault'}, 'migration_id': 'vault-flatpak',
            'candidates': [
                {'installer': 'omarchy-install-and-launch', 'command': 'cd {repo} && ./install.sh --migrate-to-omarchy-installers',
                 'requires_packages': ['vault']},
            ],
            'offer': 'Setup moves it with your yes.',
            'self_install': {'needed': False, 'evidence': 'fallback only', 'checked': '2026-10-01', 'recheck': '2026-12-01'},
        },
        {
            'id': 'tube', 'name': 'Tube', 'groundwork_method': 'user Flatpak org.example.Tube',
            'candidates': [{'package': 'tube'}, {'package': 'tube-tool', 'repo': 'extra'}],
            'installer_keywords': ['tube'],
            'offer': 'Omarchy ships Tube now.',
            'self_install': {'needed': True, 'evidence': 'absent on 2026-10-24', 'checked': '2026-10-24',
                             'recheck': '2026-11-30'},
        },
    ],
}


class FeedWatchFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.sync = self.root / 'sync'
        self.state = self.root / 'state/groundwork'
        self.config = self.root / 'config/groundwork/omarchy.json'
        self.table = self.root / 'table.json'
        self.table.write_text(json.dumps(FIXTURE_TABLE))
        write_db(self.sync / 'omarchy.db', {'viewer-bin': '2.0.1-1', 'unrelated': '1-1'})
        write_db(self.sync / 'extra.db', {'other': '1-1'})

    def env(self):
        # Built from scratch: no real HOME, XDG directory, or PATH entry leaks in.
        return {
            'HOME': str(self.home),
            'XDG_STATE_HOME': str(self.root / 'state'),
            'XDG_CONFIG_HOME': str(self.root / 'config'),
            'XDG_CACHE_HOME': str(self.root / 'cache'),
            'XDG_DATA_HOME': str(self.root / 'data'),
            'GNUPGHOME': str(self.root / 'gnupg'),
            'PATH': str(self.bin),
            'LC_ALL': 'C',
        }

    def run_watch(self, *args, today='2026-10-31', extra=()):
        command = [sys.executable, '-B', str(SCRIPT), *args, '--table', str(self.table),
                   '--sync-dir', str(self.sync), '--today', today, *extra]
        return subprocess.run(command, capture_output=True, text=True, env=self.env(), timeout=60)

    def report(self, *extra, today='2026-10-31'):
        result = self.run_watch('report', '--json', today=today, extra=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return {app['id']: app for app in json.loads(result.stdout)['apps']}, json.loads(result.stdout)

    def install_flatpak(self, app_id):
        (self.home / '.local/share/flatpak/app' / app_id).mkdir(parents=True)


class DatabaseTests(FeedWatchFixture):
    def test_reads_only_the_wanted_packages_with_their_versions(self):
        found = watch.read_db(self.sync / 'omarchy.db', {'viewer-bin', 'missing'})
        self.assertEqual(found, {'viewer-bin': {'version': '2.0.1-1', 'arch': 'aarch64'}})

    def test_a_name_that_is_a_prefix_of_another_package_does_not_match(self):
        write_db(self.sync / 'omarchy.db', {'viewer-bin-extra': '1-1'})
        self.assertEqual(watch.read_db(self.sync / 'omarchy.db', {'viewer-bin'}), {})

    @unittest.skipUnless('zst' in getattr(tarfile, 'TarFile').OPEN_METH, 'this Python has no zstd tarfile')
    def test_reads_a_zstd_database_like_the_real_feed(self):
        write_db(self.sync / 'omarchy.db', {'viewer-bin': '3-1'}, mode='w:zst')
        self.assertEqual(watch.read_db(self.sync / 'omarchy.db', {'viewer-bin'})['viewer-bin']['version'], '3-1')


class ReportTests(FeedWatchFixture):
    def test_installer_comes_before_the_package_and_names_its_version(self):
        executable(self.bin / 'omarchy-install-viewer')
        self.install_flatpak('org.example.Viewer')
        apps, report = self.report()
        viewer = apps['viewer']
        self.assertTrue(viewer['provided'])
        self.assertEqual([p['how'] for p in viewer['providers']], ['installer', 'package'])
        self.assertEqual(viewer['providers'][1]['version'], '2.0.1-1')
        self.assertEqual(viewer['offer']['command'], 'omarchy-install-viewer --no-launch')
        self.assertFalse(viewer['offer']['dismissed'])
        self.assertFalse(viewer['self_install']['needed'])
        self.assertFalse(report['online'])
        self.assertEqual(report['sources']['omarchy']['via'], 'sync database')

    def test_package_alone_is_offered_with_omarchy_pkg_add(self):
        self.install_flatpak('org.example.Viewer')
        apps, _ = self.report()
        self.assertEqual(apps['viewer']['offer']['command'], 'omarchy pkg add viewer-bin')

    def test_not_provided_offers_nothing_and_keeps_the_self_install(self):
        apps, _ = self.report()
        tube = apps['tube']
        self.assertIs(tube['provided'], False)
        self.assertIsNone(tube['offer'])
        self.assertTrue(tube['self_install']['needed'])
        self.assertFalse(tube['self_install']['recheck_due'])

    def test_a_passed_recheck_date_is_reported_with_its_evidence(self):
        result = self.run_watch('report', today='2026-12-02')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Tube: not provided by Omarchy.', result.stdout)
        self.assertIn('Recheck was due 2026-11-30. Last evidence: absent on 2026-10-24', result.stdout)

    def test_app_not_installed_by_groundwork_here_is_reported_without_an_offer(self):
        apps, _ = self.report()
        self.assertIs(apps['viewer']['installed_by_groundwork'], False)
        self.assertIsNone(apps['viewer']['offer'])
        result = self.run_watch('report')
        self.assertIn('Not installed by Groundwork on this machine, so nothing is offered.', result.stdout)

    def test_a_package_in_another_repo_counts_as_omarchy_pkg(self):
        write_db(self.sync / 'extra.db', {'tube-tool': '5-1'})
        apps, _ = self.report()
        provider = apps['tube']['providers'][0]
        self.assertEqual((provider['repo'], provider['name'], provider['version']), ('extra', 'tube-tool', '5-1'))
        self.assertEqual(apps['tube']['offer']['command'], 'omarchy pkg add tube-tool')

    def test_an_unlisted_installer_matching_a_keyword_is_found_and_a_hidden_one_is_not(self):
        executable(self.bin / 'omarchy-install-tube-app')
        executable(self.bin / 'omarchy-install-tube-helper', '#!/bin/sh\n# omarchy:hidden=true\nexit 0\n')
        apps, _ = self.report()
        self.assertEqual([p['installer'] for p in apps['tube']['providers']], ['omarchy-install-tube-app'])
        self.assertEqual(apps['tube']['offer']['command'], 'omarchy-install-tube-app')

    def test_a_non_executable_installer_does_not_count(self):
        (self.bin / 'omarchy-install-tube').write_text('#!/bin/sh\n')
        apps, _ = self.report()
        self.assertIs(apps['tube']['provided'], False)

    def test_an_installer_whose_package_is_missing_from_the_feed_does_not_count(self):
        executable(self.bin / 'omarchy-install-and-launch')
        self.install_flatpak('org.example.Vault')
        apps, _ = self.report()
        self.assertIs(apps['vault']['provided'], False)
        write_db(self.sync / 'omarchy.db', {'vault': '1-1'})
        apps, _ = self.report()
        self.assertTrue(apps['vault']['provided'])
        self.assertTrue(apps['vault']['offer']['command'].endswith('&& ./install.sh --migrate-to-omarchy-installers'))
        self.assertIn(str(REPO), apps['vault']['offer']['command'])

    def test_a_migration_declined_at_setup_counts_as_dismissed(self):
        executable(self.bin / 'omarchy-install-and-launch')
        self.install_flatpak('org.example.Vault')
        write_db(self.sync / 'omarchy.db', {'vault': '1-1'})
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({'declined_migrations': ['vault-flatpak']}))
        apps, _ = self.report()
        self.assertEqual(apps['vault']['offer']['dismissed_by'], 'declined_migrations')
        brief = self.run_watch('report', '--brief')
        self.assertEqual(brief.stdout, '')
        full = self.run_watch('report')
        self.assertIn('declined at setup; ./install.sh --migrate-to-omarchy-installers asks again', full.stdout)

    def test_text_report_names_the_command_and_how_to_dismiss(self):
        executable(self.bin / 'omarchy-install-viewer')
        self.install_flatpak('org.example.Viewer')
        result = self.run_watch('report')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Viewer: Omarchy provides omarchy-install-viewer (installer, Install > Viewer); '
                      'omarchy/viewer-bin 2.0.1-1.', result.stdout)
        self.assertIn('Command: omarchy-install-viewer --no-launch', result.stdout)
        self.assertIn('dismiss viewer', result.stdout)
        self.assertIn('Self-install no longer needed: feed has viewer-bin', result.stdout)

    def test_report_is_the_default_command(self):
        self.assertEqual(self.run_watch('--json').returncode, 0)

    def test_report_writes_nothing(self):
        executable(self.bin / 'omarchy-install-viewer')
        self.install_flatpak('org.example.Viewer')
        before = sorted(p for p in self.root.rglob('*'))
        self.run_watch('report')
        self.run_watch('report', '--json')
        self.assertEqual(sorted(p for p in self.root.rglob('*')), before)


class FallbackTests(FeedWatchFixture):
    def test_missing_database_falls_back_to_pacman_si(self):
        (self.sync / 'omarchy.db').unlink()
        executable(self.bin / 'pacman', '#!/bin/sh\n'
                   'case "$2" in\n'
                   '  omarchy/viewer-bin) printf "Repository      : omarchy\\nName            : viewer-bin\\nVersion         : 9-1\\nArchitecture    : aarch64\\n" ;;\n'
                   '  *) echo "error: package \'$2\' was not found" >&2; exit 1 ;;\n'
                   'esac\n')
        self.install_flatpak('org.example.Viewer')
        apps, report = self.report()
        self.assertEqual(apps['viewer']['providers'][0]['version'], '9-1')
        self.assertEqual(report['sources']['omarchy']['via'], 'pacman -Si')

    def test_without_database_or_pacman_packages_are_unknown(self):
        (self.sync / 'omarchy.db').unlink()
        apps, report = self.report()
        self.assertIsNone(apps['tube']['provided'])
        self.assertEqual(report['sources']['omarchy']['via'], 'unavailable')
        result = self.run_watch('report')
        self.assertIn('Tube: unknown, the package database could not be read.', result.stdout)

    def test_an_unknown_repo_in_pacman_is_unknown_not_absent(self):
        (self.sync / 'extra.db').unlink()
        executable(self.bin / 'pacman', "#!/bin/sh\necho \"error: repository 'extra' does not exist\" >&2\n"
                   "echo \"error: package '$2' was not found\" >&2\nexit 2\n")
        apps, _ = self.report()
        self.assertIsNone(apps['tube']['provided'])

    def test_online_reads_the_downloaded_feed_and_leaves_no_copy(self):
        feed = self.root / 'feed/omarchy.db'
        write_db(feed, {'tube': '0.26.0-1'})
        apps, report = self.report('--online', '--feed-url', feed.as_uri())
        self.assertEqual(apps['tube']['providers'][0]['version'], '0.26.0-1')
        self.assertTrue(report['online'])
        self.assertEqual(report['sources']['omarchy']['via'], 'downloaded')
        self.assertFalse(Path(report['sources']['omarchy']['path']).exists())

    def test_online_failure_exits_nonzero_with_the_url(self):
        result = self.run_watch('report', extra=('--online', '--feed-url', (self.root / 'none.db').as_uri()))
        self.assertEqual(result.returncode, 1)
        self.assertIn('Could not download', result.stderr)


class DismissTests(FeedWatchFixture):
    def setUp(self):
        super().setUp()
        executable(self.bin / 'omarchy-install-viewer')
        self.install_flatpak('org.example.Viewer')

    def test_dismiss_stores_the_routes_in_the_state_dir_only(self):
        home_before = sorted(self.home.rglob('*'))
        result = self.run_watch('dismiss', 'viewer', extra=('--state-dir', str(self.state)))
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((self.state / 'feed-watch.json').read_text())
        self.assertEqual(state, {'dismissed': {'viewer': ['installer:omarchy-install-viewer',
                                                          'package:omarchy/viewer-bin']}})
        self.assertEqual(sorted(self.home.rglob('*')), home_before)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ['feed-watch.json'])

    def test_default_state_dir_follows_xdg_state_home(self):
        self.assertEqual(self.run_watch('dismiss', 'viewer').returncode, 0)
        self.assertTrue((self.root / 'state/groundwork/feed-watch.json').is_file())

    def test_dismissed_offer_is_quiet_in_brief_and_named_in_the_full_report(self):
        brief = self.run_watch('report', '--brief')
        self.assertIn('Viewer (viewer): Omarchy provides omarchy-install-viewer', brief.stdout)
        self.assertIn('dismiss <app>', brief.stdout)
        self.run_watch('dismiss', 'viewer')
        apps, _ = self.report()
        self.assertTrue(apps['viewer']['offer']['dismissed'])
        self.assertEqual(self.run_watch('report', '--brief').stdout, '')
        self.assertIn('undismiss viewer', self.run_watch('report').stdout)

    def test_a_route_omarchy_adds_later_is_offered_again(self):
        self.run_watch('dismiss', 'viewer')
        executable(self.bin / 'omarchy-install-viewer-pro')
        apps, _ = self.report()
        self.assertFalse(apps['viewer']['offer']['dismissed'])

    def test_a_version_bump_does_not_bring_the_offer_back(self):
        self.run_watch('dismiss', 'viewer')
        write_db(self.sync / 'omarchy.db', {'viewer-bin': '2.0.2-1'})
        apps, _ = self.report()
        self.assertTrue(apps['viewer']['offer']['dismissed'])

    def test_undismiss_restores_the_offer(self):
        self.run_watch('dismiss', 'viewer')
        result = self.run_watch('undismiss', 'viewer')
        self.assertEqual(result.returncode, 0, result.stderr)
        apps, _ = self.report()
        self.assertFalse(apps['viewer']['offer']['dismissed'])

    def test_unknown_app_is_refused(self):
        result = self.run_watch('dismiss', 'nope')
        self.assertEqual(result.returncode, 2)
        self.assertIn("No app 'nope'", result.stderr)
        self.assertFalse(self.state.exists())

    def test_nothing_to_dismiss_when_omarchy_does_not_provide_it(self):
        result = self.run_watch('dismiss', 'tube')
        self.assertEqual(result.returncode, 1)
        self.assertIn('nothing to dismiss', result.stderr)
        self.assertFalse(self.state.exists())

    def test_unreadable_state_stops_dismiss_and_only_warns_in_report(self):
        self.state.mkdir(parents=True)
        (self.state / 'feed-watch.json').write_text('{not json')
        result = self.run_watch('dismiss', 'viewer')
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.state / 'feed-watch.json').read_text(), '{not json')
        report = self.run_watch('report')
        self.assertEqual(report.returncode, 0)
        self.assertIn('Ignoring unreadable dismissals', report.stderr)
        self.assertIn('Command: omarchy-install-viewer --no-launch', report.stdout)


class TableTests(unittest.TestCase):
    def test_repo_table_is_valid_and_its_docs_exist(self):
        apps = watch.load_table(TABLE)
        self.assertTrue(apps)
        for app in apps:
            if app.get('doc'):
                self.assertTrue((REPO / app['doc']).is_file(), app['doc'])
            record = app['self_install']
            self.assertIn('needed', record)
            self.assertRegex(record.get('checked', ''), r'^\d{4}-\d{2}-\d{2}$', app['id'])
            self.assertRegex(record.get('recheck', ''), r'^\d{4}-\d{2}-\d{2}$', app['id'])
            self.assertTrue(record.get('evidence'), app['id'])

    def test_migration_ids_in_the_table_exist_in_apply_layer(self):
        layer = (REPO / 'scripts/apply-layer.sh').read_text()
        for app in watch.load_table(TABLE):
            if 'migration_id' in app:
                self.assertIn(app['migration_id'], layer)

    def test_invalid_table_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'table.json'
            for value in ({'schema': 1, 'apps': [{'id': 'Bad'}]},
                          {'schema': 2, 'apps': []},
                          [],
                          {'schema': 1, 'apps': [{'id': 'a', 'name': 'A', 'groundwork_method': 'm', 'offer': 'o',
                                                  'self_install': {}, 'candidates': [{'installer': 'x', 'package': 'y'}]}]}):
                path.write_text(json.dumps(value))
                with self.assertRaises(watch.TableError):
                    watch.load_table(path)


if __name__ == '__main__':
    unittest.main()
