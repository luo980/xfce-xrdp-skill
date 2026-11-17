"""Offline invariants: no SSH, sudo, package installs or service changes."""
import ast
import importlib.util
import ipaddress
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/deploy-xfce-xrdp/scripts"
spec = importlib.util.spec_from_file_location("verify_f2b", SCRIPTS / "verify-fail2ban.py")
f2b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f2b)


class ScriptChecks(unittest.TestCase):
    def test_syntax_and_help(self):
        for path in SCRIPTS.rglob("*.sh"):
            with self.subTest(path=path):
                subprocess.run(["bash", "-n", str(path)], check=True, capture_output=True)
                subprocess.run(["bash", str(path), "--help"], check=True, capture_output=True, timeout=10)
        for path in SCRIPTS.rglob("*.py"):
            ast.parse(path.read_text())

    def test_rdp_permissions_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, str(SCRIPTS / "make-rdp-client.py"), "--host", "desktop.example.net",
                   "--port", "14489", "--name", "workstation", "--user", "desktopuser", "--output-dir", tmp]
            subprocess.run(cmd, check=True, capture_output=True)
            rdp = Path(tmp) / "workstation.rdp"
            launcher = Path(tmp) / "Connect-workstation.command"
            content = rdp.read_text()
            self.assertIn("full address:s:desktop.example.net:14489\n", content)
            self.assertIn("redirectclipboard:i:1\n", content)
            self.assertIn("audiomode:i:2\n", content)
            self.assertIn("prompt for credentials:i:1\n", content)
            self.assertNotIn("password", content.lower())
            self.assertEqual(rdp.stat().st_mode & 0o777, 0o600)
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o700)
            subprocess.run(["bash", "-n", str(launcher)], check=True)
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertEqual(rdp.read_text(), content)

    def test_reject_client_config_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            for option, value in [("--host", "example.net\npassword 51:b:BAD"),
                                  ("--name", "$(touch injected)"), ("--name", "../escape"),
                                  ("--user", "name\nredirectdrives:i:1"), ("--port", "65536")]:
                with self.subTest(option=option, value=value):
                    cmd = [sys.executable, str(SCRIPTS / "make-rdp-client.py"),
                           "--host", "desktop.example.net", "--name", "workstation", "--output-dir", tmp,
                           option, value]
                    self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
                    self.assertEqual(list(Path(tmp).iterdir()), [])


class NftChecks(unittest.TestCase):
    def snapshot(self, version=4, port=14489):
        family = "ip" if version == 4 else "ip6"
        name = "addr-set-test" if version == 4 else "addr6-set-test"
        address = "192.0.2.8" if version == 4 else "2001:db8::8"
        objects = [
            {"chain": {"family": "inet", "table": "f2b-table", "name": "f2b-chain", "hook": "input", "type": "filter"}},
            {"set": {"family": "inet", "table": "f2b-table", "name": name,
                     "type": "ipv4_addr" if version == 4 else "ipv6_addr", "elem": [address]}},
            {"rule": {"family": "inet", "table": "f2b-table", "chain": "f2b-chain", "expr": [
                {"match": {"op": "==", "left": {"payload": {"protocol": family, "field": "saddr"}}, "right": "@" + name}},
                {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": {"set": [port]}}},
                {"reject": None},
            ]}},
        ]
        return objects, name, ipaddress.ip_address(address)

    def test_exact_custom_port_rule_both_families(self):
        for version in (4, 6):
            objects, name, address = self.snapshot(version)
            f2b.check_rule(objects, name, version, 14489)
            self.assertTrue(f2b.set_contains(objects, name, address, exact=True))
            with self.assertRaises(f2b.VerificationError):
                f2b.check_rule(objects, name, version, 13389)

    def test_missing_verdict_rejected(self):
        objects, name, _ = self.snapshot()
        objects[-1]["rule"]["expr"].pop()
        with self.assertRaises(f2b.VerificationError):
            f2b.check_rule(objects, name, 4, 14489)

    def test_prefix_is_not_exact_test_ban(self):
        objects, name, address = self.snapshot()
        objects[1]["set"]["elem"] = [{"prefix": {"addr": "192.0.2.0", "len": 24}}]
        self.assertTrue(f2b.set_contains(objects, name, address))
        self.assertFalse(f2b.set_contains(objects, name, address, exact=True))


if __name__ == "__main__":
    unittest.main()
