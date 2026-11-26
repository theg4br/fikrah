#!/usr/bin/env python3
"""Render private operator parameters. This command never provisions resources."""

import argparse
import datetime as dt
import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUBSCRIPTION = "0de07754-9825-4fc4-b0b8-eb147c12d6f5"


def caller_cidr(value: str) -> str:
    network = ipaddress.IPv4Network(value, strict=True)
    if value != str(network) or network.prefixlen != 32 or not network.network_address.is_global:
        raise ValueError("SSH must allow one public IPv4 address with /32")
    return str(network)


def parameters(cidr: str, public_key: Path, dns_label: str) -> dict:
    cidr = caller_cidr(cidr)
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,61}[a-z0-9]", dns_label):
        raise ValueError("DNS label must be 3–63 lowercase characters, starting with a letter")
    key = public_key.read_text().strip()
    if not key.startswith(("ssh-ed25519 ", "ssh-rsa ")) or "PRIVATE KEY" in key or "\n" in key:
        raise ValueError("Supply an OpenSSH RSA or Ed25519 public key only")
    subprocess.run(["ssh-keygen", "-lf", str(public_key)], check=True, capture_output=True)
    template = json.loads((REPO / "infra/main.parameters.json").read_text())
    values = template["parameters"]
    for name, value in {
        "adminSourceCidr": cidr, "adminSshPublicKey": key, "dnsLabel": dns_label,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    }.items():
        values[name] = {"value": value}
    return template


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-public-key", type=Path, required=True)
    parser.add_argument("--admin-cidr", required=True)
    parser.add_argument("--dns-label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if output.is_relative_to(REPO) and not output.is_relative_to(REPO / ".copilot-azure"):
            raise ValueError("Write operator parameters outside Git or under ignored .copilot-azure")
        account = json.loads(subprocess.check_output(["az", "account", "show", "-o", "json"], text=True))
        if account["id"] != SUBSCRIPTION or account["state"] != "Enabled":
            raise ValueError("Active Azure subscription differs from the approved target")
        data = parameters(args.admin_cidr, args.ssh_public_key, args.dns_label)
        os.umask(0o077)
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with output.open("x") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        parser.exit(1, "Parameter generation refused; check subscription, public key, /32 and output path.\n")
    print("Private parameters generated. No Azure resources created.")


if __name__ == "__main__":
    main()
