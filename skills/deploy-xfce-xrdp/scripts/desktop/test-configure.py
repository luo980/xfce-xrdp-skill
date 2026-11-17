#!/usr/bin/env python3
"""Offline tests: render configs in a temporary tree; never invoke system tools."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SPEC = importlib.util.spec_from_file_location('desktop', Path(__file__).with_name('configure-desktop.py'))
desktop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desktop)


def options(**kwargs):
    values = dict(user='desktopuser', label='R&D Workspace', rdp_port=14489,
                  bind_address='0.0.0.0', backup_dir=None)
    values.update(kwargs)
    return argparse.Namespace(**values)


class ConfigurationTests(unittest.TestCase):
    def test_repeated_xorg_params_and_managed_key_replacement(self):
        source = '[Globals]\nport=3389\nport=3390\n[Xorg]\nparam=Xorg\nparam=-config\nparam=xrdp/xorg.conf'
        output = desktop.render_ini(source, {'Globals': {'port': 'tcp://127.0.0.1:14489'}})
        self.assertEqual(output.count('port='), 1)
        self.assertIn('port=tcp://127.0.0.1:14489\n', output)
        self.assertEqual([line for line in source.splitlines() if line.startswith('param=')],
                         [line for line in output.splitlines() if line.startswith('param=')])
        with self.assertRaises(ValueError):
            desktop.render_ini('[Globals]\nport=1\n[Globals]\nport=2\n', {'Globals': {'port': '3'}})

    def test_rejects_config_injection_and_invalid_options(self):
        for changes in ({'user': '../root'}, {'label': 'desktop\nport=3389'},
                        {'label': 'desktop;echo x'}, {'bind_address': 'host.example'},
                        {'bind_address': '::1'}, {'bind_address': '224.0.0.1'},
                        {'rdp_port': 0}, {'rdp_port': 65536},
                        {'backup_dir': '/tmp/backup'}, {'backup_dir': '/var/backups/../evil'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                desktop.validate_options(options(**changes))
        desktop.validate_options(options())
        desktop.validate_options(options(label='开发桌面 & Lab'))

    def test_fresh_render_for_both_distribution_schemas(self):
        for distro, version in [('debian', '0.9.21.1-1+deb12u2'), ('ubuntu', '0.10.1-4.1ubuntu0.1')]:
            with self.subTest(distro=distro), tempfile.TemporaryDirectory() as root:
                root = Path(root).resolve()
                home = root / 'home' / 'desktopuser'
                home.mkdir(parents=True)
                backup = root / 'backup'
                backup.mkdir(mode=0o700)
                marker = root / 'var/lib/xfce-xrdp-desktop/desktopuser.json'
                xrdp = root / 'etc/xrdp'
                xrdp.mkdir(parents=True)
                original = '[Globals]\nport=3389\n[Xorg]\nparam=Xorg\nparam=-config\nparam=xrdp/xorg.conf\n'
                (xrdp / 'xrdp.ini').write_text(original)
                (xrdp / 'sesman.ini').write_text('[Globals]\n' + ('ListenAddress=0.0.0.0\nListenPort=3350\n' if distro == 'debian' else 'ListenPort=sesman.socket\n'))
                original_stat = Path.stat
                def safe_path(value):
                    value = str(value)
                    if value.startswith(('/etc/', '/usr/', '/var/')):
                        return root / value.lstrip('/')
                    return Path(value)
                def safe_stat(value, *args, **kwargs):
                    result = original_stat(value, *args, **kwargs)
                    if str(value) == str(backup):
                        fields = list(result)
                        fields[4] = 0
                        return os.stat_result(fields)
                    return result
                calls = []
                def fake_run(argv, **kwargs):
                    calls.append(argv)
                    if argv[0] == 'rsvg-convert':
                        Path(argv[argv.index('-o') + 1]).write_bytes(b'fixture-png')
                    elif argv[0] != 'usermod':
                        raise AssertionError('Unexpected system command: ' + repr(argv))
                    return subprocess.CompletedProcess(argv, 0)
                args = options(backup_dir=str(backup))
                account = SimpleNamespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
                with patch.object(desktop, 'Path', side_effect=safe_path), \
                     patch.object(desktop, 'checked_result', return_value=version), \
                     patch.object(desktop.shutil, 'which', return_value='/fixture/program'), \
                     patch.object(desktop.subprocess, 'run', side_effect=fake_run), \
                     patch.object(desktop.os, 'chown'), patch.object(Path, 'stat', safe_stat):
                    desktop.configure(args, distro, account, marker)
                output = (xrdp / 'xrdp.ini').read_text()
                self.assertIn('port=tcp://0.0.0.0:14489', output)
                self.assertEqual(output.count('param='), 3)
                self.assertIn('rdpsnd=false', output)
                sesman = (xrdp / 'sesman.ini').read_text()
                if distro == 'debian':
                    self.assertIn('ListenAddress=127.0.0.1', sesman)
                    self.assertIn('ListenPort=3350', sesman)
                else:
                    self.assertIn('ListenPort=sesman.socket', sesman)
                    self.assertNotIn('ListenAddress=', sesman)
                    self.assertIn('[ChansrvLogging]', sesman)
                xml_files = list((home / '.config/xfce4/xfconf/xfce-perchannel-xml').glob('*.xml'))
                self.assertGreater(len(xml_files), 5)
                for xml in xml_files:
                    ET.parse(xml)
                self.assertIn('button-title=R&D Workspace\n', (home / '.config/xfce4/panel/whiskermenu-1.rc').read_text())
                self.assertTrue((home / '.xsession').read_text().startswith('#!/bin/sh\nunset SESSION_MANAGER\n'))
                self.assertIn('\nType=Application\n', (home / '.config/autostart/remote-desktop-session.desktop').read_text())
                self.assertIn('\nName=Default\n', (home / '.config/fcitx5/profile').read_text())
                self.assertTrue(marker.exists())
                self.assertTrue(any(p.read_text() == original for p in backup.rglob('xrdp.ini')))
                self.assertEqual([c[0] for c in calls], ['usermod', 'rsvg-convert'])


if __name__ == '__main__':
    unittest.main()
