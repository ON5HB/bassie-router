"""Uitlezen van de huidige DHCP-klanten van dnsmasq."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .runner import CommandRunner

LEASE_FILE = "/var/lib/misc/dnsmasq.leases"


@dataclass
class Lease:
    mac: str
    ip: str
    name: str = ""
    since: str = ""

    @property
    def pin_name(self) -> str:
        """'printer.local' wordt 'printer'; het domein komt van dnsmasq."""
        return self.name.split(".", 1)[0]


def _moment(epoch: str) -> str:
    try:
        return datetime.fromtimestamp(int(epoch)).strftime("%d-%m %H:%M")
    except (ValueError, OSError, OverflowError):
        return ""


def parse_leases(text: str) -> list[Lease]:
    """Leest het standaard dnsmasq-leasebestand (epoch mac ip naam client-id)."""
    leases: list[Lease] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        epoch, mac, ip = fields[:3]
        if ":" not in mac or "." not in ip:
            continue
        leases.append(Lease(
            mac=mac.lower(),
            ip=ip,
            name=fields[3] if len(fields) > 3 else "",
            since=_moment(epoch),
        ))
    return leases


def read_leases(runner: CommandRunner, path: str = LEASE_FILE) -> list[Lease]:
    """Geeft een lege lijst als het bestand er nog niet is."""
    result = runner.run(["cat", path])
    if not result.ok:
        return []
    return parse_leases(result.stdout)
