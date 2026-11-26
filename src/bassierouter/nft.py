"""Genereert de IPv4-firewall/NAT-regels (nftables, tabel 'ip') voor Bassie-Router."""
from __future__ import annotations

from .config import Config
from .runner import CommandResult, CommandRunner


def _comment(text: str) -> str:
    clean = text.replace("\\", "\\\\").replace('"', '\\"')
    return clean[:100]


def render_nft(cfg: Config) -> str:
    i = cfg.interfaces
    fw = cfg.firewall
    # nftables gebruikt '*' als wildcard voor interfacenamen; '+' is de
    # iptables-syntax en matcht nooit ppp0. Elke WAN-regel wordt daarom
    # tweemaal uitgeschreven: één voor de fysieke WAN-interface en één los
    # met oifname/iifname "ppp*" voor de PPPoE-interface.
    wan = f'"{cfg.active_wan_iface}"'
    lan = f'"{i.lan_iface}"'

    def wan_rules(rule: str) -> list[str]:
        return [rule, rule.replace("$WAN", '"ppp*"')]

    no_track = ""
    if fw.no_track_udp_ports:
        ports = ", ".join(str(p) for p in fw.no_track_udp_ports)
        no_track = f"define NO_TRACK = {{ {ports} }}"
    lines: list[str] = [
        "#!/usr/sbin/nft -f",
        "# Gegenereerd door Bassie-Router - niet handmatig bewerken.",
        "# Alleen IPv4 (tabel 'ip'); IPv6 staat uit via sysctl.",
        f'define WAN = {wan}',
        f"define LAN = {lan}",
        f"define LAN_NET = {i.network}",
    ]
    if no_track:
        lines.append(no_track)
    timeouts = [
        (index, rule)
        for index, rule in enumerate(fw.forwards)
        if rule.protocol == "udp" and rule.udp_timeout
    ]
    lines += [
        "",
        "flush ruleset",
        "",
        "table ip bassie {",
    ]
    for index, rule in timeouts:
        lines += [
            f"    ct timeout fwd_{index} {{",
            "        protocol udp",
            "        l3proto ip",
            f"        policy = {{ established: {rule.udp_timeout} }}",
            "    }",
            "",
        ]
    lines += [
        "    chain prerouting {",
        "        type nat hook prerouting priority dstnat; policy accept;",
    ]
    for index, rule in timeouts:
        src = "" if rule.source.strip().lower() == "any" else f"ip saddr {rule.source} "
        lines += wan_rules(
            f"        iifname $WAN {src}udp dport {rule.external_port} "
            f'ct state new ct timeout set "fwd_{index}" '
            f'comment "{_comment(rule.name)} udp-timeout"'
        )

    for rule in fw.forwards:
        src = "" if rule.source.strip().lower() == "any" else f"ip saddr {rule.source} "
        lines += wan_rules(
            f'        iifname $WAN {src}{rule.protocol} dport {rule.external_port} '
            f'dnat to {rule.internal_ip}:{rule.internal_port} '
            f'comment "{_comment(rule.name)}"'
        )
    if no_track:
        lines.append('        iifname $LAN udp dport $NO_TRACK notrack comment "no-track"')
    lines += [
        "    }",
        "",
        "    chain input {",
        f"        type filter hook input priority filter; policy {fw.wan_input_policy};",
        "        ct state established,related accept",
        "        ct state invalid drop",
        "        iif lo accept",
    ]
    if fw.allow_ping_wan:
        lines += wan_rules(
            "        iifname $WAN ip protocol icmp icmp type echo-request accept"
        )
    if fw.allow_ssh_lan:
        lines.append("        iifname $LAN tcp dport 22 accept")
    if fw.allow_gui_lan:
        lines.append(
            f'        iifname $LAN tcp dport {cfg.gui.port} accept comment "Bassie-Router GUI"'
        )
    if fw.allow_gui_bootstrap:
        lines += wan_rules(
            f'        iifname $WAN tcp dport {cfg.gui.port} accept '
            'comment "Bassie-Router GUI (initiele setup)"'
        )
    if fw.allow_dns_dhcp_lan:
        lines += [
            "        iifname $LAN udp dport 53 accept",
            "        iifname $LAN tcp dport 53 accept",
            "        iifname $LAN udp dport 67 accept",
        ]
    if no_track:
        lines += [
            '        iifname $LAN udp dport $NO_TRACK accept',
        ]
        lines += wan_rules('        iifname $WAN udp sport $NO_TRACK accept')
    lines += [
        "    }",
        "",
        "    chain forward {",
        "        type filter hook forward priority filter; policy drop;",
        "        ct state established,related accept",
        "        ct state invalid drop",
    ]
    if fw.force_lan_dns_via_router:
        lines += wan_rules(
            '        iifname $LAN oifname $WAN udp dport 53 drop comment "DNS alleen via router"'
        )
        lines += wan_rules(
            '        iifname $LAN oifname $WAN tcp dport 53 drop comment "DNS alleen via router"'
        )
    # PPPoE heeft een lagere MTU (standaard 1492) dan het LAN (1500).
    # Zonder MSS-clamping laat de router kleine verzoeken (ping, DNS) door,
    # maar blijven grote TCP-segmenten met DF-bit hangen: websites laden
    # niet, ook al heeft de router zelf internet.
    lines += wan_rules(
        '        oifname $WAN tcp flags syn tcp option maxseg size set rt mtu '
        'comment "TCP-MSS naar WAN/PPPoE-MTU"'
    )
    lines += wan_rules("        iifname $LAN oifname $WAN accept")
    for rule in fw.forwards:
        src = "" if rule.source.strip().lower() == "any" else f"ip saddr {rule.source} "
        lines += wan_rules(
            f"        iifname $WAN {src}oifname $LAN ip daddr {rule.internal_ip} "
            f"{rule.protocol} dport {rule.internal_port} "
            f'comment "{_comment(rule.name)}"'
        )
    lines += [
        "    }",
        "",
    ]
    if no_track:
        lines += [
            "    chain output {",
            "        type filter hook output priority filter; policy accept;",
            "        udp dport $NO_TRACK notrack",
            "        udp sport $NO_TRACK notrack",
            "    }",
            "",
        ]
    lines += [
        "    chain postrouting {",
        "        type nat hook postrouting priority srcnat; policy accept;",
    ]
    lines += wan_rules('        oifname $WAN masquerade comment "NAT naar WAN/PPPoE"')
    lines += [
        "    }",
        "}",
        "",
    ]
    return "\n".join(lines)


def check_ruleset(runner: CommandRunner, ruleset: str) -> CommandResult:
    """Syntaxcontrole zonder de regels te activeren."""
    return runner.run(["nft", "-c", "-f", "-"], input_text=ruleset)
