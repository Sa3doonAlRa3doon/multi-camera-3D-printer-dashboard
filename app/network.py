"""Network-address discovery without hard-coded host addresses."""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess

import psutil


def lan_addresses() -> list[str]:
    found: set[str] = set()
    for interface in psutil.net_if_addrs().values():
        for address in interface:
            if address.family != socket.AF_INET:
                continue
            try:
                ip = ipaddress.ip_address(address.address)
            except ValueError:
                continue
            if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
                found.add(str(ip))
    return sorted(found, key=lambda value: tuple(int(part) for part in value.split(".")))


def tailscale_addresses() -> list[str]:
    executable = shutil.which("tailscale")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [executable, "ip", "-4"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    addresses: list[str] = []
    for line in result.stdout.splitlines():
        value = line.strip()
        try:
            if ipaddress.ip_address(value).version == 4:
                addresses.append(value)
        except ValueError:
            continue
    return list(dict.fromkeys(addresses))
