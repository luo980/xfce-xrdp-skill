#!/usr/bin/env python3
"""Verify live Fail2ban nftables actions with documentation-only test IPs.

For the paired Debian/Ubuntu installer with Fail2ban >= 1.0.2.
Run as root after install-fail2ban.sh. This tests the ban/unban actions and
kernel rules, not log matching or network traffic. Existing bans are left alone.
"""

import argparse
import fcntl
import ipaddress
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time


TABLE = "f2b-table"
CHAIN = "f2b-chain"
TEST_V4 = ipaddress.ip_network("192.0.2.0/24")
TEST_V6 = ipaddress.ip_network("2001:db8::/32")
COMMAND_TIMEOUT = 15


class VerificationError(RuntimeError):
    pass


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True,
                            timeout=COMMAND_TIMEOUT, env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        raise VerificationError(f"{' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def f2b(*args):
    return run("fail2ban-client", *args)


def banned(jail):
    return [ipaddress.ip_network(token, strict=False)
            for token in f2b("get", jail, "banip").split()]


def nft_table():
    # A table is normally created on the first ban. Do not treat other nft
    # errors, such as insufficient permissions, as an empty ruleset.
    tables = json.loads(run("nft", "-j", "list", "tables"))["nftables"]
    if not any(item.get("table", {}).get("family") == "inet"
               and item["table"].get("name") == TABLE for item in tables):
        return []
    return json.loads(run("nft", "-j", "-n", "list", "table", "inet", TABLE))["nftables"]


def items(snapshot, kind):
    return [item[kind] for item in snapshot if kind in item
            and item[kind].get("family") == "inet"
            and item[kind].get("table", TABLE) == TABLE]


def contains_element(value, address):
    """Handle nft singleton, prefix, range and element-with-metadata forms."""
    if isinstance(value, str):
        try:
            return address in ipaddress.ip_network(value, strict=False)
        except ValueError:
            return False
    if isinstance(value, dict):
        if "elem" in value:
            return contains_element(value["elem"], address)
        if "val" in value:
            return contains_element(value["val"], address)
        if "prefix" in value:
            p = value["prefix"]
            return contains_element(f"{p['addr']}/{p['len']}", address)
        if "range" in value:
            low, high = map(ipaddress.ip_address, value["range"])
            return low.version == address.version and int(low) <= int(address) <= int(high)
    return False


def set_contains(snapshot, set_name, address, exact=False):
    def matches(value):
        if not exact:
            return contains_element(value, address)
        while isinstance(value, dict) and ("elem" in value or "val" in value):
            value = value.get("elem", value.get("val"))
        if isinstance(value, dict) and "prefix" in value:
            prefix = value["prefix"]
            value = f"{prefix['addr']}/{prefix['len']}"
        if isinstance(value, str):
            try:
                network = ipaddress.ip_network(value, strict=False)
                return network.num_addresses == 1 and address in network
            except ValueError:
                return False
        return False

    for entry in items(snapshot, "set"):
        if entry.get("name") == set_name:
            if any(matches(v) for v in entry.get("elem", [])):
                return True
    # Support separate element objects as well as inline set contents.
    for entry in items(snapshot, "element"):
        if entry.get("name") == set_name:
            if any(matches(v) for v in entry.get("elem", [])):
                return True
    return False


def connected_and_local_addresses():
    addresses = set()
    for name in ("SSH_CONNECTION", "SSH_CLIENT"):
        if os.environ.get(name):
            addresses.add(ipaddress.ip_address(os.environ[name].split()[0].split("%")[0]))
    for interface in json.loads(run("ip", "-j", "address", "show")):
        for entry in interface.get("addr_info", []):
            addresses.add(ipaddress.ip_address(entry["local"].split("%")[0]))
    for line in run("ss", "-Htn", "state", "established").splitlines():
        fields = line.split()
        if len(fields) < 4:
            raise VerificationError("Cannot parse active TCP connections; no bans were attempted.")
        for endpoint in fields[-2:]:
            host = endpoint.rsplit(":", 1)[0].strip("[]").split("%")[0]
            addresses.add(ipaddress.ip_address(host))
    return addresses


def inspect_action(jail, expected_name, expected_port):
    text = f2b("get", jail, "actions")
    heading = f"The jail {jail} has the following actions:\n"
    if not text.startswith(heading):
        raise VerificationError(f"Cannot read action list for {jail}: {text}")
    action_ids = [s.strip() for s in text[len(heading):].split(",")]
    if len(action_ids) != 1 or not re.fullmatch(r"nftables(?:[-_a-zA-Z0-9.]*)", action_ids[0]):
        raise VerificationError(f"{jail}: expected one nftables action, found {action_ids}")
    action = action_ids[0]
    expected = {"name": expected_name, "port": str(expected_port), "protocol": "tcp",
                "table": TABLE, "table_family": "inet", "chain": CHAIN,
                "type": "multiport", "chain_hook": "input", "chain_type": "filter"}
    for key, value in expected.items():
        actual = f2b("get", jail, "action", action, key)
        if actual != value:
            raise VerificationError(f"{jail}/{action}: {key}={actual!r}; expected {value!r}")
    print(f"ACTION OK: {jail} -> {action}, name={expected_name}, TCP {expected_port}", flush=True)


def check_rule(snapshot, set_name, version, port):
    sets = [s for s in items(snapshot, "set") if s.get("name") == set_name]
    expected_type = "ipv4_addr" if version == 4 else "ipv6_addr"
    if len(sets) != 1 or sets[0].get("type") != expected_type:
        raise VerificationError(f"{set_name}: missing or unexpected nftables set type")
    chains = [c for c in items(snapshot, "chain") if c.get("name") == CHAIN]
    if len(chains) != 1 or chains[0].get("type") != "filter" or chains[0].get("hook") != "input":
        raise VerificationError("Fail2ban chain is not a filter base chain on the input hook")
    family = "ip" if version == 4 else "ip6"
    referenced = []
    for rule in items(snapshot, "rule"):
        expressions = rule.get("expr", [])
        if any(expr.get("match", {}).get("right") == "@" + set_name for expr in expressions):
            referenced.append(rule)
    if len(referenced) != 1 or referenced[0].get("chain") != CHAIN:
        raise VerificationError(f"{set_name}: expected exactly one rule in {CHAIN}")
    source_matches = port_matches = verdicts = 0
    for expr in referenced[0]["expr"]:
        if "match" in expr:
            match = expr["match"]
            if match.get("op") not in ("==", "in"):
                raise VerificationError(f"{set_name}: unexpected match operator")
            left, right = match.get("left"), match.get("right")
            if left == {"payload": {"protocol": family, "field": "saddr"}} and right == "@" + set_name:
                source_matches += 1
            elif left == {"payload": {"protocol": "tcp", "field": "dport"}} and right in (port, {"set": [port]}):
                port_matches += 1
            else:
                raise VerificationError(f"{set_name}: unexpected address/port match: {match}")
        elif "reject" in expr or "drop" in expr:
            verdicts += 1
        elif "counter" not in expr:
            raise VerificationError(f"{set_name}: unexpected rule expression: {expr}")
    if (source_matches, port_matches, verdicts) != (1, 1, 1):
        raise VerificationError(f"{set_name}: missing exact TCP {port}, source-set or blocking verdict")


def wait_until(check, description, seconds=10):
    deadline = time.monotonic() + seconds
    last_error = "condition not met"
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (VerificationError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise VerificationError(f"Timed out waiting for {description}: {last_error}")


def select_address(version, existing, snapshot, avoid):
    network = TEST_V4 if version == 4 else TEST_V6
    set_names = [s["name"] for s in items(snapshot, "set")]
    rng = random.SystemRandom()
    for _ in range(1000):
        offset = rng.randrange(1, 255) if version == 4 else rng.getrandbits(96)
        address = ipaddress.ip_address(int(network.network_address) + offset)
        if (address not in avoid and not any(address in net for net in existing)
                and not any(set_contains(snapshot, name, address) for name in set_names)):
            return address
    raise VerificationError(f"No unused IPv{version} documentation address is available")


def interrupted(signum, _frame):
    raise VerificationError(f"Interrupted by signal {signum}; cleaning up test bans")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--xrdp-port", type=int, default=13389)
    args = parser.parse_args()
    if any(not 1 <= p <= 65535 for p in (args.ssh_port, args.xrdp_port)):
        parser.error("Ports must be between 1 and 65535")
    if args.ssh_port == args.xrdp_port:
        parser.error("SSH and RDP must use different ports")
    targets = (("sshd", "remote-sshd", args.ssh_port),
               ("remote-xrdp", "remote-xrdp", args.xrdp_port))
    if os.geteuid() != 0:
        raise VerificationError("Run with sudo python3 verify-fail2ban.py")
    for command in ("fail2ban-client", "nft", "ip", "ss"):
        if not shutil.which(command):
            raise VerificationError(f"Missing command: {command}")
    lock_fd = os.open("/run/remote-desktop-fail2ban-verify.lock",
                      os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise VerificationError("Another verification is already running") from exc
    f2b("ping")
    original_bans = {}
    for jail, name, port in targets:
        inspect_action(jail, name, port)
        original_bans[jail] = banned(jail)
    avoid = connected_and_local_addresses()
    all_existing = [net for bans in original_bans.values() for net in bans]
    snapshot = nft_table()
    tests = []
    for jail, name, port in targets:
        for version in (4, 6):
            address = select_address(version, all_existing, snapshot, avoid)
            avoid.add(address)
            set_name = ("addr-set-" if version == 4 else "addr6-set-") + name
            tests.append((jail, set_name, address, port))
    owned = []
    cleanup_errors = []
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        for jail, set_name, address, port in tests:
            # Recheck immediately before changing state. Never unban a preexisting IP.
            if address in connected_and_local_addresses():
                raise VerificationError(f"{address} is now an active/local address; left untouched")
            if any(address in net for net in banned(jail)) or set_contains(nft_table(), set_name, address):
                raise VerificationError(f"{jail}: {address} became banned before this test; left untouched")
            owned.append((jail, set_name, address))  # Includes an uncertain command timeout.
            count = f2b("set", jail, "banip", str(address))
            if count == "0":
                owned.pop()  # Another actor already owned it; do not undo their ban.
                raise VerificationError(f"{jail}: test IP was already banned concurrently; left untouched")
            if count != "1":
                raise VerificationError(f"{jail}: unexpected ban result: {count}")

            def verify_ban():
                live = nft_table()
                if not any(address in net for net in banned(jail)) or not set_contains(live, set_name, address, exact=True):
                    return False
                check_rule(live, set_name, address.version, port)
                return True

            wait_until(verify_ban, f"{jail} IPv{address.version} kernel ban")
            print(f"BAN OK: {jail}, {address}, {set_name}, TCP {port}", flush=True)
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, signal.SIG_IGN)
        for jail, set_name, address in reversed(owned):
            try:
                f2b("set", jail, "unbanip", str(address))
                wait_until(lambda: not any(address in n for n in banned(jail))
                           and not set_contains(nft_table(), set_name, address),
                           f"removal of test ban {jail}/{address}")
                print(f"CLEANUP OK: {jail}, {address} removed", flush=True)
            except Exception as exc:
                cleanup_errors.append(f"{jail}/{address}: {exc}")
        os.close(lock_fd)
        if cleanup_errors:
            for error in cleanup_errors:
                print(f"CLEANUP FAILED: {error}", file=sys.stderr)
            raise VerificationError("Test ban cleanup failed; use the exact jail/IP pairs printed above")
    print(f"PASS: SSH {args.ssh_port} and xrdp {args.xrdp_port} IPv4/IPv6 nftables actions verified; all test bans removed.")


if __name__ == "__main__":
    try:
        main()
    except (VerificationError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
