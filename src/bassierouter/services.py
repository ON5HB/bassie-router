"""Renderers voor dnsmasq, sysctl, PPPoE, link-setup en systemd-units."""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from . import __release_date__, __version__
from .config import Config

BEGIN = "# BEGIN Bassie-Router (automatisch gegenereerd)"
END = "# END Bassie-Router"


def _q(value: str) -> str:
    """Escape voor gebruik tussen dubbele quotes in PPP-config."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_hostname(cfg: Config) -> str:
    return cfg.hostname + "\n"


def render_hosts(cfg: Config, existing: str, old_hostname: str = "") -> str:
    """Bewaar /etc/hosts en vervang alleen de oude hostname."""
    lines = existing.splitlines() if existing.strip() else ["127.0.0.1 localhost"]
    result = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            parts = [cfg.hostname if part == old_hostname else part for part in stripped.split()]
            line = " ".join(parts)
            if "127.0.1.1" in parts and cfg.hostname in parts:
                found = True
        result.append(line)
    if not found:
        result.append(f"127.0.1.1 {cfg.hostname}")
    return "\n".join(result) + "\n"


def render_motd(cfg: Config) -> str:
    """Statisch welkomstbericht. Het IP komt uit update-motd.d (actueel)."""
    return (
        "\n"
        "============================================================\n"
        f" Bassie-Router v{__version__} · {__release_date__}\n"
        " Debian 13 · IPv4-only · open source & gratis (MIT)\n"
        "\n"
        " Samen gebouwd door Bas Heijermans (ON5HB, radioamateur):\n"
        " hardware, testen en koers — en Big Pickle, de AI-assistent\n"
        " in OpenCode: https://opencode.ai\n"
        "\n"
        " Login: root + je SSH-wachtwoord (sessie 30 min)\n"
        " CLI: bassie-router status · bassie-router apply --dry-run\n"
        "============================================================\n"
    )


def render_motd_ip_script(cfg: Config) -> str:
    """Toont bij elke login het IP dat de LAN-bridge nú echt heeft."""
    bridge = shlex.quote(cfg.interfaces.lan_bridge)
    port = cfg.gui.port
    return f"""#!/bin/sh
# Gegenereerd door Bassie-Router - actueel GUI-adres bij elke login.
IP=$(/usr/sbin/ip -4 -o addr show dev {bridge} scope global 2>/dev/null | \\
    awk '{{for (i = 1; i <= NF; i++) if ($i == "inet") {{split($(i + 1), a, "/"); print a[1]; exit}}}}')
if [ -z "$IP" ]; then
    echo "GUI: http://127.0.0.1:{port} (alleen lokaal; via SSH -L {port}:127.0.0.1:{port})"
else
    echo "GUI: http://$IP:{port}"
fi
"""


def render_sysctl(cfg: Config) -> str:
    udp = cfg.firewall.udp_nat_timeout
    udp_lines = ""
    if udp:
        udp_lines = (
            f"net.netfilter.nf_conntrack_udp_timeout={udp}\n"
            f"net.netfilter.nf_conntrack_udp_timeout_stream={udp}\n"
        )
    return (
        "# Gegenereerd door Bassie-Router - IPv4-only router\n"
        "net.ipv4.ip_forward=1\n"
        "net.ipv4.conf.all.rp_filter=2\n"
        "net.ipv4.conf.default.rp_filter=2\n"
        "net.ipv4.conf.all.accept_redirects=0\n"
        "net.ipv6.conf.all.disable_ipv6=1\n"
        "net.ipv6.conf.default.disable_ipv6=1\n"
        "net.ipv6.conf.lo.disable_ipv6=1\n"
        + udp_lines
    )


def render_dnsmasq(cfg: Config) -> str:
    i = cfg.interfaces
    lines = [
        "# Gegenereerd door Bassie-Router - DNS (IPv4)",
        f"interface={i.lan_iface}",
        "bind-dynamic",
        "no-resolv",
        "domain-needed",
        "bogus-priv",
        "filter-AAAA",
        f"domain={i.dns_domain}",
        f"local=/{i.dns_domain}/",
        "expand-hosts",
    ]
    if i.dhcp_enabled:
        lines += [
            "# DHCP-server aan: de router bedient het LAN zelf.",
            f"listen-address={i.lan_ip}",
            "dhcp-authoritative",
            f"dhcp-range={i.dhcp_start},{i.dhcp_end},{i.dhcp_lease}",
            f"dhcp-option=3,{i.lan_ip}",
            f"dhcp-option=6,{i.lan_ip}",
        ]
        if i.dhcp_reservations:
            lines.append("# Vaste adressen op basis van MAC-adres.")
            for reservation in i.dhcp_reservations:
                entry = f"dhcp-host={reservation.mac.lower()},{reservation.ip}"
                if reservation.name:
                    entry += f",{reservation.name}"
                lines.append(entry)
    else:
        lines.append(
            "# DHCP-server uit: geen dhcp-range en geen vast listen-address; "
            "DNS luistert op de adressen van de bridge."
        )
    lines += [f"server={s}" for s in i.dns_upstream]
    return "\n".join(lines) + "\n"


def render_links_script(cfg: Config) -> str:
    i = cfg.interfaces
    # Ingesteld VLAN-id blijft bewaard, maar wordt alleen toegepast als
    # PPPoE aan staat. Anders moet een oude subinterface juist verdwijnen.
    active_vlan = cfg.active_vlan_id
    active_wan = cfg.active_wan_iface
    ports = " ".join(shlex.quote(p) for p in i.lan_ports)
    bootstrap = "0" if cfg.bootstrapped else "1"
    wifi_block = ""
    if cfg.wifi.enabled:
        # De AP's horen bij hetzelfde untagged switch-netwerk als de LAN-poorten.
        for iface, _band, _channel in wifi_plan(cfg):
            wifi_block += f"""
# WiFi-AP op de LAN-bridge (zelfde DHCP/DNS als bekabeld).
if exists "{iface}"; then
    $IP addr flush dev "{iface}" 2>/dev/null || true
    $IP -6 addr flush dev "{iface}" 2>/dev/null || true
    $IP link set dev "{iface}" master "$BR" 2>/dev/null || true
    $IP link set dev "{iface}" up 2>/dev/null || true
fi
"""
    else:
        for iface in detect_wireless_interfaces():
            wifi_block += f"""
# WiFi staat uit: NIC losmaken van de bridge en down laten.
if exists "{iface}"; then
    $IP link set dev "{iface}" nomaster 2>/dev/null || true
    $IP link set dev "{iface}" down 2>/dev/null || true
fi
"""
    vlan_block = ""
    vlan_cleanup = ""
    if i.wan:
        vlan_cleanup = """
# Oude WAN-VLAN-subinterfaces opruimen (bijv. enp1s0.10 als VLAN niet actief is).
if exists "$WAN"; then
    for entry in /sys/class/net/"$WAN".*; do
        [ -e "$entry" ] || continue
        vid=${entry##*.}
        if [ -n "$VLAN_ID" ] && [ "$vid" = "$VLAN_ID" ]; then
            continue
        fi
        $IP link del "$WAN.$vid" 2>/dev/null || true
    done
fi
"""
    if active_vlan:
        vlan_block = f"""
if ! exists "{active_wan}" && exists "$WAN"; then
    $IP link add link "$WAN" name "{active_wan}" type vlan id {active_vlan} 2>/dev/null || true
fi
if exists "{active_wan}"; then
    $IP link set dev "{active_wan}" up 2>/dev/null || true
fi
"""
    # Na de eerste setup: poorten die niet als LAN gekozen zijn, uit de bridge halen.
    detach_block = ""
    if cfg.bootstrapped:
        detach_block = """
# Alleen de gekozen LAN-poorten blijven in de bridge; de rest wordt WAN.
for port in $ALL_PORTS; do
    case " $PORTS " in
        *" $port "*) continue ;;
    esac
    if exists "$port"; then
        $IP link set dev "$port" nomaster 2>/dev/null || true
        $IP addr flush dev "$port" 2>/dev/null || true
        $IP -6 addr flush dev "$port" 2>/dev/null || true
        $IP link set dev "$port" up 2>/dev/null || true
    fi
done
"""
    if cfg.bootstrapped and i.dhcp_enabled:
        addr_block = f"""
# DHCP-server aan: definitief LAN-adres (IPv4, geen VLAN); ook IPv6 resten weghalen.
$IP addr flush dev "$BR" 2>/dev/null || true
$IP -6 addr flush dev "$BR" 2>/dev/null || true
$IP addr add "${{{'LAN_IP'}}}/${{{'PREFIX'}}}" dev "$BR"
"""
    else:
        addr_block = """
# DHCP-server uit (ook tijdens de initiele setup): bridge is DHCP-klant.
# bassie-lan-dhcp.service houdt de lease actief en probeert opnieuw als de
# DHCP-server bij boot nog niet bereikbaar is. Hier alleen oude adressen weg.
$IP addr flush dev "$BR" 2>/dev/null || true
$IP -6 addr flush dev "$BR" 2>/dev/null || true
"""
    if i.wan_mode == "dhcp":
        wan_addr_block = """
# WAN dhcp-client: adres en default route komen van de provider/modem.
$IP addr flush dev "$WAN_IFACE" 2>/dev/null || true
$IP -6 addr flush dev "$WAN_IFACE" 2>/dev/null || true
if [ -x /usr/sbin/dhclient ] && command -v timeout >/dev/null 2>&1; then
    timeout 12 /usr/sbin/dhclient -4 -1 "$WAN_IFACE" >/dev/null 2>&1 || true
fi
"""
    elif i.wan_mode == "static":
        wan_addr_block = f"""
# WAN statisch: adres, gateway en (optioneel) DNS van de provider.
$IP addr flush dev "$WAN_IFACE" 2>/dev/null || true
$IP -6 addr flush dev "$WAN_IFACE" 2>/dev/null || true
$IP addr add "{i.wan_static_ip}/{i.wan_static_prefix}" dev "$WAN_IFACE"
$IP route replace default via "{i.wan_static_gateway}" dev "$WAN_IFACE"
"""
        if i.wan_static_dns:
            dns_commands = "\n".join(
                f'    echo "nameserver {shlex.quote(server)}"' for server in i.wan_static_dns
            )
            wan_addr_block += f"""
# Alleen in statische modus beheert niemand anders resolv.conf.
{{
    echo "# Gegenereerd door Bassie-Router"
{dns_commands}
}} > /etc/resolv.conf
"""
    elif i.wan_static_ip:
        # PPPoE mét modem-beheeradres: de NIC krijgt een IP in het subnet van
        # de modem/ONT (bijv. 192.168.1.2/24 bij 192.168.1.1). Alleen een route
        # naar die gateway; géén default route, want ppp0 is de uplink.
        gateway_route = ""
        modem_state = ""
        if i.wan_static_gateway:
            gateway_route = (
                f'\n$IP route replace "{i.wan_static_gateway}/32" dev "$WAN_IFACE"'
            )
            modem_state = f"""
mkdir -p /run/bassie-router
printf "%s\\n" "{shlex.quote(i.wan_static_gateway)}" > /run/bassie-router/modem-ip 2>/dev/null || true"""
        wan_addr_block = f"""
# PPPoE: optioneel beheeradres om de modem/ONT op {i.wan_static_gateway or 'haar IP'} te bereiken.
$IP addr flush dev "$WAN_IFACE" 2>/dev/null || true
$IP -6 addr flush dev "$WAN_IFACE" 2>/dev/null || true
$IP addr add "{i.wan_static_ip}/{i.wan_static_prefix}" dev "$WAN_IFACE"{gateway_route}{modem_state}
"""
    else:
        # PPPoE met DHCP op de NIC: de modem/ONT geeft eerst een beheeradres
        # (bijv. 192.168.1.x), daarna bouwt pppd ppp0 op. Een default route via
        # de NIC mag niet winnen van ppp0.
        wan_addr_block = """
# PPPoE: WAN-NIC haalt eerst zelf een adres bij de modem/ONT.
$IP addr flush dev "$WAN_IFACE" 2>/dev/null || true
$IP -6 addr flush dev "$WAN_IFACE" 2>/dev/null || true
if [ -x /usr/sbin/dhclient ] && command -v timeout >/dev/null 2>&1; then
    timeout 12 /usr/sbin/dhclient -4 -1 "$WAN_IFACE" >/dev/null 2>&1 || true
fi
# Bewaar het modem-adres vóórdat we de default route van de NIC verwijderen;
# bassie-pppoe-check gebruikt het om de modem te pingen.
mkdir -p /run/bassie-router
$IP -4 route show default dev "$WAN_IFACE" 2>/dev/null | awk '$2 == "via" {print $3; exit}' > /run/bassie-router/modem-ip 2>/dev/null || true
$IP route del default dev "$WAN_IFACE" 2>/dev/null || true
"""
    if wan_addr_block:
        wan_block = (
            '\nif [ "$BOOTSTRAP" = "0" ] && exists "$WAN_IFACE"; then\n'
            + wan_addr_block
            + "fi\n"
        )
    else:
        wan_block = ""
    # De bridge kan (met DHCP-server uit) ook een default route van een andere
    # DHCP-server krijgen. Die moet niet winnen van de WAN-verbinding.
    if i.wan_mode == "static":
        wan_route_fix = f"""
# Default route via WAN herstellen na de DHCP-klant op de bridge.
$IP route del default dev "$BR" 2>/dev/null || true
$IP route replace default via "{i.wan_static_gateway}" dev "$WAN_IFACE"
"""
    elif i.wan_mode == "dhcp":
        wan_route_fix = """
# Geen default route via de LAN-bridge; WAN is de uplink.
$IP route del default dev "$BR" 2>/dev/null || true
"""
    else:
        wan_route_fix = ""
    return f"""#!/bin/sh
# Gegenereerd door Bassie-Router - maakt bridge, WAN-VLAN en LAN-IP aan.
# Geen 'set -e': een mislukte link-actie mag nooit de rest (LAN-IP) afbreken.
set -u
IP=/usr/sbin/ip
WAN="{i.wan}"
WAN_IFACE="{active_wan}"
WAN_MODE="{i.wan_mode}"
VLAN_ID="{active_vlan or ''}"
BR="{i.lan_bridge}"
LAN_IP="{i.lan_ip}"
PREFIX="{i.lan_prefix}"
PORTS="{ports}"
BOOTSTRAP="{bootstrap}"

exists() {{ $IP link show dev "$1" >/dev/null 2>&1 || return 1; }}

# Bridge eerst, en met retry: als de module of udev nog niet klaar is,
# mag de rest van het script niet zonder bridge verdergaan.
n=0
while ! exists "$BR"; do
    command -v modprobe >/dev/null 2>&1 && modprobe bridge >/dev/null 2>&1 || true
    $IP link add name "$BR" type bridge 2>/dev/null || true
    n=$((n + 1))
    [ "$n" -ge 10 ] && break
    sleep 1
done

# De LAN-bridge is altijd untagged: VLAN 10 hoort alleen bij de WAN-subinterface.
# Zet VLAN-filtering expliciet uit, ook als een eerdere configuratie die aan had.
exists "$BR" && $IP link set dev "$BR" type bridge vlan_filtering 0 2>/dev/null || true

# Daarna kort wachten op definitieve kaartnamen (enp* i.p.v. eth*).
command -v udevadm >/dev/null 2>&1 && timeout 10 udevadm settle >/dev/null 2>&1 || true

# Fysieke ethernetpoorten, exclusief loopback, bridge, WAN en virtuele apparaten.
ALL_PORTS=""
for entry in /sys/class/net/*; do
    name=${{entry##*/}}
    case "$name" in
        lo|"$BR"|"$WAN"|ppp*|veth*|docker*|virbr*|wl*|*.*) continue ;;
    esac
    ALL_PORTS="$ALL_PORTS $name"
done

for port in $PORTS; do
    if exists "$port"; then
        # Een oud DHCP-adres op de fysieke poort moet weg vóór het masteren,
        # anders staat hetzelfde IP zowel op de poort als op de bridge.
        $IP addr flush dev "$port" 2>/dev/null || true
        $IP -6 addr flush dev "$port" 2>/dev/null || true
        $IP link set dev "$port" master "$BR" 2>/dev/null || true
        $IP link set dev "$port" up 2>/dev/null || true
    fi
done
{detach_block}{wifi_block}{vlan_cleanup}{vlan_block}
if exists "$WAN"; then
    $IP link set dev "$WAN" up 2>/dev/null || true
    if [ "$BOOTSTRAP" = "0" ]; then
        $IP addr flush dev "$WAN" 2>/dev/null || true
    fi
fi
{wan_block}$IP link set dev "$BR" up 2>/dev/null || true
{addr_block}{wan_route_fix}"""


def render_client_options(cfg: Config) -> str:
    c = cfg.pppoe_client
    lines = [
        "# Gegenereerd door Bassie-Router - PPPoE-client",
        "noipdefault",
        "defaultroute",
        "replacedefaultroute",
        "hide-password",
        "noauth",
        "persist",
        "maxfail 0",
        # Elke 30 seconden een echo; één gemiste echo betekent disconnect.
        # Met 4 pogingen duurde het ~2 minuten voordat een dode sessie werd
        # vervangen, terwijl de provider de sessie al eerder kan hebben laten
        # vallen.
        "lcp-echo-interval 30",
        "lcp-echo-failure 1",
        f"mtu {c.mtu}",
        "plugin rp-pppoe.so",
        # pppd verwacht de ethernet-interface als 'nic-<naam>' (ook bij
        # VLAN-subinterfaces); een kale 'enp1s0.10' wordt als optie gezien.
        f"nic-{c.iface(cfg)}",
        f'user "{_q(c.username)}"',
    ]
    if c.service_name:
        lines.append(f'rp-pppoe-service "{_q(c.service_name)}"')
    if c.usepeerdns:
        lines.append("usepeerdns")
    if c.demand and c.idle_seconds > 0:
        lines.append("demand")
        lines.append(f"idle {c.idle_seconds}")
    lines.append("")
    return "\n".join(lines)


def render_secrets_block(cfg: Config) -> str:
    lines = [BEGIN]
    c = cfg.pppoe_client
    if c.enabled:
        lines.append(f'"{_q(c.username)}" * "{_q(c.password)}" *')
    lines += [END, ""]
    return "\n".join(lines)


def merge_managed_file(existing: str, block: str) -> str:
    """Vervangt het door Bassie-Router beheerde blok, laat de rest intact."""
    if BEGIN in existing and END in existing:
        before, rest = existing.split(BEGIN, 1)
        _, after = rest.split(END, 1)
        existing = before + after.lstrip("\n")
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return existing + block


def _cmd_output(argv: list[str]) -> str:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _grub_boot_info() -> dict[str, str]:
    """Kernelpaden en UUID's voor de factory-reset-menuentry."""
    root_dev = _cmd_output(["findmnt", "-no", "SOURCE", "/"])
    boot_dev = _cmd_output(["findmnt", "-no", "SOURCE", "/boot"]) or root_dev
    root_uuid = _cmd_output(["blkid", "-s", "UUID", "-o", "value", root_dev]) if root_dev else ""
    boot_uuid = _cmd_output(["blkid", "-s", "UUID", "-o", "value", boot_dev]) if boot_dev else ""
    separate = bool(boot_dev and root_dev and boot_dev != root_dev)
    if separate:
        # Bij een aparte /boot-partitie zijn de /vmlinuz-symlinks niet leesbaar
        # voor GRUB. Dan nemen we de nieuwste kernel; na een update moet de
        # entry eventueel opnieuw worden toegepast.
        kernels = sorted(
            Path("/boot").glob("vmlinuz-*"),
            key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p.name)],
        )
        kernel = kernels[-1].name if kernels else "vmlinuz"
        version = kernel[len("vmlinuz-"):] if kernel.startswith("vmlinuz-") else ""
        initrd = f"initrd.img-{version}" if version else "initrd.img"
        search_uuid, prefix = boot_uuid, ""
    else:
        # Standaard Debian: /vmlinuz en /initrd.img zijn symlinks die bij
        # elke kernelupdate automatisch meewisselen.
        search_uuid, prefix, kernel, initrd = root_uuid, "", "vmlinuz", "initrd.img"
    return {
        "search_uuid": search_uuid,
        "root_uuid": root_uuid,
        "separate": separate,
        "kernel": f"{prefix}/{kernel}" if kernel.startswith("/") else f"/{kernel}",
        "initrd": f"{prefix}/{initrd}" if initrd.startswith("/") else f"/{initrd}",
    }


def render_grub_factory(existing: str) -> str:
    """Voegt een GRUB-keuze toe die de router als verse installatie start."""
    info = _grub_boot_info()
    search = (
        f"search --no-floppy --fs-uuid --set=root {info['search_uuid']}\n    "
        if info["search_uuid"] else ""
    )
    root = f"root=UUID={info['root_uuid']} " if info["root_uuid"] else ""
    block = (
        f"{BEGIN}\n"
        "# Bassie-Router: start met standaardwaarden (configs worden verwijderd).\n"
        "menuentry 'Bassie-Router: verse installatie (factory reset)' "
        "--class gnu-linux --class os {\n"
        f"    {search}"
        f"linux {info['kernel']} {root}ro quiet bassie_factory=1\n"
        f"    initrd {info['initrd']}\n"
        "}\n"
        f"{END}\n"
    )
    return merge_managed_file(existing, block)


def render_factory_boot_script(cfg: Config) -> str:
    """Verwijdert onze configs en start met standaardwaarden bij GRUB-keuze."""
    return """#!/bin/sh
# Gegenereerd door Bassie-Router - factory reset via GRUB-menukeuze.
set -u
grep -qw 'bassie_factory=1' /proc/cmdline 2>/dev/null || exit 0
echo "Bassie-Router: factory reset bij boot (standaardwaarden)."

rm -f /etc/bassie-router/config.json \\
    /var/lib/bassie-router/applied-config.json \\
    /etc/dnsmasq.d/bassie-router.conf \\
    /etc/hostapd/bassie-router-1.conf \
    /etc/hostapd/bassie-router-2.conf \
    /etc/nftables.conf \\
    /etc/sysctl.d/99-bassie-router.conf \\
    /etc/ppp/peers/bassie-isp \\
    /etc/update-motd.d/40-bassie-router-ip

PYTHONPATH=/opt/bassie-router/src /usr/bin/python3 - <<'PY'
from pathlib import Path
from bassierouter.config import Config, save_config

cfg = Config()
cfg.bootstrapped = False
cfg.interfaces.wan = "kies-poort"
ports = []
for entry in sorted(Path("/sys/class/net").iterdir()):
    name = entry.name
    if name in ("lo", cfg.interfaces.lan_bridge):
        continue
    if name.startswith(("ppp", "veth", "docker", "virbr", "wl")) or "." in name:
        continue
    ports.append(name)
cfg.interfaces.lan_ports = ports or ["eth1"]
cfg.firewall.allow_gui_bootstrap = True
cfg.gui.bind = "127.0.0.1"
save_config(cfg, "/etc/bassie-router/config.json")
PY

for what in nft dnsmasq sysctl links; do
    PYTHONPATH=/opt/bassie-router/src /usr/bin/python3 -m bassierouter \\
        render "$what" > "/tmp/bassie-factory-$what" 2>/dev/null || continue
    case "$what" in
        nft) cp "/tmp/bassie-factory-$what" /etc/nftables.conf ;;
        dnsmasq) cp "/tmp/bassie-factory-$what" /etc/dnsmasq.d/bassie-router.conf ;;
        sysctl) cp "/tmp/bassie-factory-$what" /etc/sysctl.d/99-bassie-router.conf ;;
        links) cp "/tmp/bassie-factory-$what" /usr/local/sbin/bassie-setup-links; chmod 0755 /usr/local/sbin/bassie-setup-links ;;
    esac
    rm -f "/tmp/bassie-factory-$what"
done
systemctl enable --now bassie-links.service bassie-lan-dhcp.service bassie-router.service >/dev/null 2>&1 || true
"""


def render_lan_dhcp_script(cfg: Config) -> str:
    """DHCP-klant op de LAN-bridge die blijft proberen tot er een lease is."""
    return f"""#!/bin/sh
# Gegenereerd door Bassie-Router - DHCP-klant voor de LAN-bridge.
# 'bassie-links.service' maakt de bridge; deze service houdt het adres actief.
# Gebruik het standaard Debian-pad: dhclient mag daar wél zijn lease schrijven
# (ook met AppArmor), in tegenstelling tot /var/lib/bassie-router.
set -u
LEASE=/var/lib/dhcp/dhclient-bassie-lan.leases
mkdir -p /var/lib/dhcp
chmod 0755 /var/lib/dhcp
[ -f "$LEASE" ] || : > "$LEASE"
chmod 0644 "$LEASE"
exec /usr/sbin/dhclient -4 -d -lf "$LEASE" \\
    {shlex.quote(cfg.interfaces.lan_bridge)}
"""


def render_cpu_script(cfg: Config) -> str:
    """Genereert het CPU-profiel: governor en min/max frequentie via cpupower."""
    s = cfg.system
    governor = shlex.quote(s.cpu_governor)
    minimum = s.cpu_min_mhz or ""
    maximum = s.cpu_max_mhz or ""
    return f"""#!/bin/sh
# Gegenereerd door Bassie-Router - CPU-profiel (zuinig, minder warmte).
GOV={governor}
MIN="{minimum}"
MAX="{maximum}"
CPUPOWER=""
for candidate in /usr/bin/cpupower /usr/local/bin/cpupower; do
    [ -x "$candidate" ] && CPUPOWER="$candidate" && break
done
if [ -n "$CPUPOWER" ]; then
    [ -n "$GOV" ] && $CPUPOWER frequency-set --governor "$GOV" >/dev/null 2>&1 || true
    if [ -n "$MIN$MAX" ]; then
        args=""
        [ -n "$MIN" ] && args="$args --min ${{MIN}}MHz"
        [ -n "$MAX" ] && args="$args --max ${{MAX}}MHz"
        $CPUPOWER frequency-set $args >/dev/null 2>&1 || true
    fi
fi
CPUFREQSET=$(command -v cpufreq-set 2>/dev/null || true)
if [ -n "$CPUFREQSET" ]; then
    [ -n "$GOV" ] && $CPUFREQSET -g "$GOV" >/dev/null 2>&1 || true
    [ -n "$MIN" ] && $CPUFREQSET -d "${{MIN}}MHz" >/dev/null 2>&1 || true
    [ -n "$MAX" ] && $CPUFREQSET -u "${{MAX}}MHz" >/dev/null 2>&1 || true
fi
# Laatste redmiddel: rechtstreeks sysfs (geen package nodig).
for dir in /sys/devices/system/cpu/cpu*/cpufreq; do
    [ -d "$dir" ] || continue
    if [ -n "$GOV" ] && [ -w "$dir/scaling_governor" ]; then
        echo "$GOV" > "$dir/scaling_governor" 2>/dev/null || true
    fi
    if [ -n "$MIN" ] && [ -w "$dir/scaling_min_freq" ]; then
        echo $((MIN * 1000)) > "$dir/scaling_min_freq" 2>/dev/null || true
    fi
    if [ -n "$MAX" ] && [ -w "$dir/scaling_max_freq" ]; then
        echo $((MAX * 1000)) > "$dir/scaling_max_freq" 2>/dev/null || true
    fi
done
"""


def render_pppoe_check_script(cfg: Config) -> str:
    """Ping de modem/ONT vóór PPPoE, maar blokkeer nooit: in bridge-modus
    hoeft de modem niet op een ping te antwoorden."""
    modem = shlex.quote(cfg.interfaces.wan_static_gateway)
    wan_iface = shlex.quote(cfg.active_wan_iface)
    return f"""#!/bin/sh
# Gegenereerd door Bassie-Router: controleer de DSL-modem/ONT vóór PPPoE.
WAN_IFACE={wan_iface}
MODEM="{modem}"
STATE=/run/bassie-router/modem-ip
PING=$(command -v ping 2>/dev/null || true)
TRIES=10

if [ -z "$MODEM" ] && [ -r "$STATE" ]; then
    MODEM=$(head -n 1 "$STATE" 2>/dev/null | tr -d '\\r\\n')
fi
if [ -z "$MODEM" ]; then
    MODEM=$(/usr/sbin/ip -4 route show dev "$WAN_IFACE" 2>/dev/null | \\
        awk '$1 == "default" && $2 == "via" {{print $3; exit}}')
fi
if [ -z "$MODEM" ]; then
    echo "bassie-pppoe-check: geen modem-adres bekend; PPPoE start zonder ping-check"
    exit 0
fi
if [ -z "$PING" ]; then
    echo "bassie-pppoe-check: ping ontbreekt; PPPoE start zonder ping-check"
    exit 0
fi

n=0
while [ "$n" -lt "$TRIES" ]; do
    if "$PING" -4 -c 1 -W 1 -q -n "$MODEM" >/dev/null 2>&1; then
        echo "bassie-pppoe-check: modem $MODEM bereikbaar"
        exit 0
    fi
    n=$((n + 1))
    [ "$n" -ge "$TRIES" ] && break
    sleep 2
done
echo "bassie-pppoe-check: modem $MODEM niet bereikbaar na $TRIES pogingen; PPPoE start toch" >&2
exit 0
"""


def detect_wireless_interfaces() -> list[str]:
    """Alle draadloze NIC's (alleen aanwijzen, nooit automatisch inschakelen)."""
    try:
        return sorted(
            path.name for path in Path("/sys/class/net").iterdir()
            if path.is_dir() and (path / "wireless").is_dir()
        )
    except OSError:
        return []


MAX_WIFI_APS = 2


def wifi_rows(cfg: Config) -> list[dict[str, object]]:
    """Rijen voor de GUI: één per gevonden radio, met eigen band en kanaal."""
    radios = detect_wireless_interfaces()
    rows: list[dict[str, object]] = []
    for index, iface in enumerate(radios[:MAX_WIFI_APS]):
        ap = cfg.wifi.aps[index] if index < len(cfg.wifi.aps) else None
        rows.append({
            "index": index,
            "interface": iface,
            "band": ap.band if ap else ("2.4" if index == 0 else "5"),
            "channel": ap.channel if ap else (11 if index == 0 else 36),
            "bands": wifi_bands(iface),
        })
    return rows


def wifi_plan(cfg: Config) -> list[tuple[str, str, int]]:
    """Actieve AP's: opgeslagen keuzes, of automatisch bij een lege config."""
    if cfg.wifi.aps:
        return [
            (ap.interface, ap.band, ap.channel)
            for ap in cfg.wifi.aps[:MAX_WIFI_APS]
            if ap.interface
        ]
    radios = detect_wireless_interfaces()[:MAX_WIFI_APS]
    return [
        (iface, "2.4" if index == 0 else "5", 11 if index == 0 else 36)
        for index, iface in enumerate(radios)
    ]


def bands_from_iw_output(text: str) -> list[str]:
    """Banden uit `iw phy ... info` (ook met '2412.0 MHz')."""
    bands: set[str] = set()
    for match in re.finditer(r"\*\s+(\d{4})(?:\.\d+)?\s+MHz", text):
        mhz = int(match.group(1))
        if 2400 <= mhz < 2500:
            bands.add("2.4")
        elif 4900 <= mhz < 5900:
            bands.add("5")
        elif 5900 <= mhz < 7100:
            bands.add("6")
    return [band for band in ("2.4", "5", "6") if band in bands]


def wifi_bands(interface: str) -> list[str]:
    """Banden die de NIC volgens `iw` ondersteunt (informatief)."""
    if not interface:
        return []
    try:
        phy = (
            Path("/sys/class/net") / interface / "phy80211" / "name"
        ).read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if not phy:
        return []
    iw = next(
        (path for path in ("/usr/sbin/iw", "/usr/bin/iw") if Path(path).exists()), ""
    )
    if not iw:
        return []
    try:
        result = subprocess.run(
            [iw, "phy", phy, "info"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return bands_from_iw_output(result.stdout)


def render_hostapd_conf(
    cfg: Config, interface: str, band: str, channel: int
) -> str:
    """hostapd-configuratie: accesspoint op de LAN-bridge."""
    wifi = cfg.wifi
    lines = [
        "# Gegenereerd door Bassie-Router - WiFi accesspoint (IPv4-LAN)",
        f"interface={shlex.quote(interface)}",
        "driver=nl80211",
        f"bridge={cfg.interfaces.lan_bridge}",
        f"ssid={wifi.ssid.strip()}",
        f"country_code={wifi.country}",
        "hw_mode=g" if band == "2.4" else "hw_mode=a",
        "ieee80211n=1",
        "ieee80211ac=1" if band == "5" else "",
        "wmm_enabled=1",
        "auth_algs=1",
        "wpa=2",
        "wpa_key_mgmt=WPA-PSK",
        "rsn_pairwise=CCMP",
        f"wpa_passphrase={wifi.password}",
    ]
    if channel:
        lines.insert(7, f"channel={channel}")
    return "\n".join(line for line in lines if line) + "\n"


def render_systemd_units(cfg: Config) -> dict[str, str]:
    units = {
        "/etc/systemd/system/bassie-links.service": (
            "[Unit]\n"
            "Description=Bassie-Router netwerklinks (bridge, WAN-VLAN, LAN-IP)\n"
            "DefaultDependencies=no\n"
            "After=local-fs.target\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "RemainAfterExit=yes\n"
            "ExecStart=/usr/local/sbin/bassie-setup-links\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        ),
        "/etc/systemd/system/bassie-factory-boot.service": (
            "[Unit]\n"
            "Description=Bassie-Router factory reset bij GRUB-keuze\n"
            "DefaultDependencies=no\n"
            "After=local-fs.target\n"
            "Before=bassie-links.service bassie-lan-dhcp.service "
            "bassie-router.service\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "RemainAfterExit=yes\n"
            "ExecStart=/usr/local/sbin/bassie-factory-boot\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        ),
        "/etc/systemd/system/bassie-lan-dhcp.service": (
            "[Unit]\n"
            "Description=Bassie-Router LAN DHCP-client (blijft proberen)\n"
            "After=bassie-links.service\n"
            "Requires=bassie-links.service\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            "ExecStart=/usr/local/sbin/bassie-lan-dhcp\n"
            "Restart=always\n"
            "RestartSec=5\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        ),
        "/etc/systemd/system/bassie-cpu.service": (
            "[Unit]\n"
            "Description=Bassie-Router CPU-profiel (cpupower)\n"
            "After=bassie-links.service\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "RemainAfterExit=yes\n"
            "ExecStart=/usr/local/sbin/bassie-cpu-profile\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        ),
        "/etc/systemd/system/bassie-router.service": (
            "[Unit]\n"
            "Description=Bassie-Router HTTP GUI\n"
            "After=network-online.target bassie-links.service bassie-lan-dhcp.service\n"
            "Wants=network-online.target\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            "User=root\n"
            "Environment=PYTHONPATH=/opt/bassie-router/src\n"
            "Environment=BASSIE_CONFIG=/etc/bassie-router/config.json\n"
            "Environment=BASSIE_GUI_PAM=1\n"
            "ExecStart=/usr/bin/python3 -m bassierouter serve\n"
            "Restart=on-failure\n"
            "RestartSec=3\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        ),
        "/etc/systemd/system/bassie-pppoe-client.service": (
            "[Unit]\n"
            "Description=Bassie-Router PPPoE-client\n"
            "After=bassie-links.service\n"
            "Requires=bassie-links.service\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            "ExecStartPre=/usr/local/sbin/bassie-pppoe-check\n"
            "ExecStart=/usr/sbin/pppd call bassie-isp nodetach\n"
            "ExecStop=/usr/sbin/poff bassie-isp\n"
            "Restart=on-failure\n"
            "RestartSec=5\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        ),
    }
    for index in range(1, MAX_WIFI_APS + 1):
        units[f"/etc/systemd/system/bassie-wifi-{index}.service"] = (
            "[Unit]\n"
            f"Description=Bassie-Router WiFi accesspoint {index}\n"
            "After=bassie-links.service\n"
            "Requires=bassie-links.service\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart=/usr/sbin/hostapd /etc/hostapd/bassie-router-{index}.conf\n"
            "Restart=on-failure\n"
            "RestartSec=5\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
    return units
