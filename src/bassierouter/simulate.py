"""Controleert het gegenereerde routerbeleid zonder netwerkapparaat aan te raken.

Dit is de 'droge' simulatie: hij rendert dezelfde bestanden als `apply` en
verifieert dat LAN -> WAN kan, dat NAT aan staat en dat de GUI nooit op WAN
luistert. De echte dataplaan-test (ping via network namespaces) staat in
`simulate-routing.sh` en vereist root.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .config import Config
from .nft import render_nft
from .services import (
    render_client_options,
    render_dnsmasq,
    render_lan_dhcp_script,
    render_links_script,
    render_motd_ip_script,
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def simulate(cfg: Config, *, nic_only: bool = False) -> list[Check]:
    """Geeft een lijst met controles voor de huidige configuratie.

    Met `nic_only=True` doen we alsof PPPoE uitstaat: de WAN-NIC (DHCP of
    statisch) is dan de uplink. Dat is de veilige eerste test op een bestaand
    netwerk; PPPoE komt pas later aan de beurt.
    """
    bridge = cfg.interfaces.lan_bridge
    mode = cfg.interfaces.wan_mode
    sim_cfg = cfg
    if nic_only and mode == "pppoe":
        mode = "static" if cfg.interfaces.wan_static_ip else "dhcp"
        sim_cfg = deepcopy(cfg)
        sim_cfg.interfaces.wan_mode = mode
        sim_cfg.pppoe_client.enabled = False
    rules = render_nft(sim_cfg)
    links = render_links_script(sim_cfg)
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, bool(ok), detail))

    add("LAN -> WAN wordt doorgestuurd", "iifname $LAN oifname $WAN accept" in rules)
    add(
        "NAT (masquerade) naar WAN staat aan",
        'oifname $WAN masquerade comment "NAT naar WAN/PPPoE"' in rules,
    )
    add(
        "WAN -> LAN is dicht behalve poortforwards",
        "type filter hook forward priority filter; policy drop;" in rules,
    )
    gui_rule = f'iifname $WAN tcp dport {cfg.gui.port} accept'
    if not cfg.bootstrapped and cfg.firewall.allow_gui_bootstrap:
        add(
            "GUI tijdelijk bereikbaar via WAN (initiele setup)",
            gui_rule in rules,
            "wordt uitgeschakeld bij de eerste Toepassen",
        )
    else:
        add("GUI niet bereikbaar via WAN", gui_rule not in rules)
    add(
        "GUI-bind is geen 0.0.0.0",
        cfg.gui.bind != "0.0.0.0",
        cfg.gui.bind,
    )

    # Eén bridge-naam in alle gegenereerde bestanden.
    add("Bridgenaam consequent in links", f'BR="{bridge}"' in links, bridge)
    add("Bridgenaam consequent in dnsmasq", f"interface={bridge}" in render_dnsmasq(sim_cfg), bridge)
    add("Bridgenaam consequent in DHCP-klant", bridge in render_lan_dhcp_script(sim_cfg), bridge)
    add("Bridgenaam consequent in MOTD", f"dev {bridge}" in render_motd_ip_script(sim_cfg), bridge)

    i = cfg.interfaces
    if nic_only:
        add(
            "Test zonder PPPoE: WAN-NIC is de uplink",
            True,
            "statisch" if mode == "static" else "DHCP",
        )
    if mode == "static":
        add(
            "Standaardroute via WAN-NIC",
            f'$IP route replace default via "{i.wan_static_gateway}" dev "$WAN_IFACE"' in links,
            f"{i.wan_static_ip}/{i.wan_static_prefix} via {i.wan_static_gateway}",
        )
    elif mode == "dhcp":
        add(
            "WAN-NIC haalt adres via DHCP",
            '/usr/sbin/dhclient -4 -1 "$WAN_IFACE"' in links,
        )
        add("Geen default route via de LAN-bridge", '$IP route del default dev "$BR"' in links)
    else:  # pppoe
        add(
            "PPPoE vervangt de default route door ppp0",
            "defaultroute" in render_client_options(cfg)
            and "replacedefaultroute" in render_client_options(cfg),
        )
        if i.wan_static_ip:
            add(
                "Modem-beheeradres op WAN-NIC",
                f'$IP addr add "{i.wan_static_ip}/{i.wan_static_prefix}" dev "$WAN_IFACE"' in links,
                f"route naar {i.wan_static_gateway}" if i.wan_static_gateway else "",
            )
        else:
            add(
                "WAN-NIC krijgt eerst DHCP-adres van modem",
                '/usr/sbin/dhclient -4 -1 "$WAN_IFACE"' in links,
            )
    return checks


def report(cfg: Config, *, nic_only: bool = False) -> str:
    checks = simulate(cfg, nic_only=nic_only)
    lines = ["Bassie-Router simulatie (configuratie, geen hardware):"]
    for check in checks:
        status = "OK  " if check.ok else "FAIL"
        detail = f" — {check.detail}" if check.detail else ""
        lines.append(f"  [{status}] {check.name}{detail}")
    failed = sum(1 for check in checks if not check.ok)
    lines.append(f"{len(checks) - failed}/{len(checks)} controles geslaagd.")
    return "\n".join(lines)
