"""Uitlezen van de huidige DHCP-klanten van dnsmasq."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .runner import CommandRunner

LEASE_FILE = "/var/lib/misc/dnsmasq.leases"


@dataclass
class Lease:
    mac: str
    ip: str
    name: str = ""
    since: str = ""
    via: str = ""

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


def read_wifi_macs(
    runner: CommandRunner, interfaces: Sequence[str]
) -> tuple[set[str], bool]:
    """MAC's die nu bij een van de wifi-radio's zijn geassocieerd.

    Tweede waarde geeft aan of de uitleesopdracht is gelukt; zo niet dan is
    'Kabel' geen betrouwbare conclusie.
    """
    if not interfaces:
        return set(), True
    macs: set[str] = set()
    checked = False
    for iface in interfaces:
        result = runner.run(["iw", "dev", iface, "station", "dump"])
        if not result.ok:
            continue
        checked = True
        for line in result.stdout.splitlines():
            fields = line.strip().split()
            if len(fields) >= 2 and fields[0] == "Station":
                macs.add(fields[1].lower())
    return macs, checked
