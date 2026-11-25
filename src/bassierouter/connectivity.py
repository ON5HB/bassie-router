"""Live controles of de router nog kan routen en DNS kan opvragen.

Deze checks draaien op de router zelf en gebruiken dezelfde commando's als een
beheerder handmatig zou typen. Ze zijn informatief: een mislukte ping maakt een
configuratie niet ongeldig, maar toont wél duidelijk waar de verbinding stopt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .config import Config
from .runner import CommandRunner, LocalRunner


@dataclass(frozen=True)
class ConnectivityCheck:
    name: str
    ok: bool
    detail: str = ""


def _first_ip(stdout: str) -> str:
    fields = stdout.split()
    for index, field in enumerate(fields[:-1]):
        if field == "inet":
            return fields[index + 1].split("/", 1)[0]
    return ""


def _first_via(stdout: str) -> str:
    fields = stdout.split()
    for index, field in enumerate(fields[:-1]):
        if field == "via":
            return fields[index + 1]
    return ""


def _link_state(stdout: str) -> str:
    fields = stdout.split()
    return fields[1] if len(fields) > 1 else ""


def _route_iface_ok(route: str, cfg: Config) -> bool:
    if not route.strip() or "linkdown" in route:
        return False
    if cfg.interfaces.wan_mode == "pppoe":
        return "ppp" in route
    return cfg.interfaces.wan_iface in route


def run_checks(cfg: Config, runner: CommandRunner | None = None) -> list[ConnectivityCheck]:
    """Controleer de huidige verbinding; nooit dodelijk bij een fout."""
    runner = runner or LocalRunner()
    checks: list[ConnectivityCheck] = []
    bridge = cfg.interfaces.lan_bridge

    result = runner.run([
        "ip", "-4", "-o", "addr", "show", "dev", bridge, "scope", "global"
    ])
    lan_ip = _first_ip(result.stdout)
    checks.append(ConnectivityCheck(
        "LAN-bridge heeft IPv4-adres",
        bool(lan_ip),
        lan_ip or f"geen adres op {bridge}",
    ))

    route_result = runner.run(["ip", "-4", "route", "show", "default"])
    default_route = next(
        (line.strip() for line in route_result.stdout.splitlines() if line.strip()), ""
    )
    checks.append(ConnectivityCheck(
        "Default route via WAN",
        _route_iface_ok(default_route, cfg),
        default_route or "geen default route",
    ))

    link = runner.run(["ip", "-br", "link", "show", "dev", cfg.interfaces.wan_iface])
    state = _link_state(link.stdout)
    checks.append(ConnectivityCheck(
        "WAN-link actief",
        state.upper() == "UP",
        f"{cfg.interfaces.wan_iface}: {state or 'onbekend'}",
    ))

    if cfg.pppoe_client.enabled and cfg.interfaces.wan_mode == "pppoe":
        modem = cfg.interfaces.wan_static_gateway
        if not modem:
            nic_routes = runner.run([
                "ip", "-4", "route", "show", "dev", cfg.interfaces.wan_iface
            ])
            modem = _first_via(nic_routes.stdout)
        modem_ping = (
            runner.run(["ping", "-4", "-c", "1", "-W", "2", "-q", "-n", modem])
            if modem else None
        )
        checks.append(ConnectivityCheck(
            "DSL-modem bereikbaar",
            bool(modem_ping and modem_ping.ok and modem_ping.stdout.strip()),
            f"ping {modem}" if modem else "geen modem-adres ingesteld",
        ))

    gateway = (
        cfg.interfaces.wan_static_gateway
        if cfg.interfaces.wan_mode == "static"
        else _first_via(default_route)
    )
    if gateway:
        argv: Sequence[str] = ["ping", "-4", "-c", "1", "-W", "2", "-q", "-n"]
        if lan_ip:
            argv = [*argv, "-I", lan_ip]
        ping = runner.run([*argv, gateway])
        checks.append(ConnectivityCheck(
            "LAN kan gateway bereiken",
            ping.ok and bool(ping.stdout.strip()),
            f"ping {gateway}" + (f" vanaf {lan_ip}" if lan_ip else ""),
        ))
    else:
        checks.append(ConnectivityCheck(
            "LAN kan gateway bereiken",
            False,
            "geen gateway bekend (ppp0 heeft vaak geen 'via')",
        ))

    targets = [s for s in cfg.interfaces.dns_upstream if s] or ["1.1.1.1"]
    target = targets[0]
    ping = runner.run(["ping", "-4", "-c", "1", "-W", "2", "-q", "-n", target])
    checks.append(ConnectivityCheck(
        "Internet bereikbaar",
        ping.ok and bool(ping.stdout.strip()),
        f"ping {target}",
    ))

    dns = runner.run(["getent", "ahostsv4", "debian.org"])
    checks.append(ConnectivityCheck(
        "DNS werkt",
        dns.ok and bool(dns.stdout.strip()),
        "debian.org",
    ))

    nft = runner.run(["nft", "list", "table", "ip", "bassie"])
    checks.append(ConnectivityCheck(
        "nftables-regels actief",
        nft.ok and "table ip bassie" in nft.stdout,
        "table ip bassie",
    ))

    if cfg.interfaces.dhcp_enabled:
        unit = runner.run(["systemctl", "is-active", "dnsmasq.service"])
        checks.append(ConnectivityCheck(
            "DHCP-server actief",
            unit.ok and unit.stdout.strip() == "active",
            "dnsmasq.service",
        ))

    if cfg.pppoe_client.enabled and cfg.interfaces.wan_mode == "pppoe":
        ppp = runner.run(["ip", "-4", "-o", "addr", "show", "dev", "ppp0"])
        ppp_ip = _first_ip(ppp.stdout)
        checks.append(ConnectivityCheck(
            "PPPoE-verbinding (ppp0) actief",
            bool(ppp_ip),
            ppp_ip or "ppp0 heeft nog geen IP",
        ))
    return checks


def report(cfg: Config, runner: CommandRunner | None = None) -> str:
    checks = run_checks(cfg, runner)
    lines = ["Bassie-Router verbindingschecks:"]
    for check in checks:
        status = "OK  " if check.ok else "FAIL"
        detail = f" — {check.detail}" if check.detail else ""
        lines.append(f"  [{status}] {check.name}{detail}")
    failed = sum(1 for check in checks if not check.ok)
    lines.append(f"{len(checks) - failed}/{len(checks)} checks geslaagd.")
    return "\n".join(lines)
