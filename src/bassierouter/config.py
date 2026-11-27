"""Configuratiemodel voor Bassie-Router (JSON, IPv4-LAN, optionele WAN-VLAN)."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
import time
import types
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

DEFAULT_CONFIG_PATH = "/etc/bassie-router/config.json"

IFACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,14}$")
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,32}$")
HOSTNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,62}$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
CPU_GOVERNORS = (
    "powersave", "ondemand", "conservative", "performance", "schedutil",
)


class ConfigError(Exception):
    """Ongeldige configuratie."""


def _coerce(value: Any, typ: Any) -> Any:
    origin = get_origin(typ)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(typ) if a is not type(None)]
        if value is None:
            return None
        return _coerce(value, args[0]) if args else value
    if origin is list:
        args = get_args(typ)
        return [_coerce(v, args[0]) for v in value] if args else list(value)
    if is_dataclass(typ):
        return _from_dict(typ, value)
    return value


def _from_dict(cls: type, data: dict[str, Any] | None) -> Any:
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ConfigError(f"verwacht object voor {cls.__name__}")
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name in data:
            kwargs[f.name] = _coerce(data[f.name], hints[f.name])
    return cls(**kwargs)


@dataclass
class DhcpReservation:
    mac: str = ""
    ip: str = ""
    name: str = ""
    """Vast IP: dnsmasq geeft deze host een lease zonder verloop."""


@dataclass
class Interfaces:
    wan: str = "eth0"
    """Fysieke WAN-interface (bijv. eth0)."""
    wan_vlan_id: int | None = None
    """802.1Q VLAN-tag op WAN; null betekent untagged. Alleen voor de WAN-kant."""
    wan_mode: str = "dhcp"
    """Effectieve WAN-modus: dhcp of static; 'pppoe' als PPPoE er overheen staat."""
    wan_static_ip: str = ""
    """Alleen bij wan_mode=static: IPv4-adres van de WAN-poort."""
    wan_static_prefix: int = 24
    """Alleen bij wan_mode=static: prefixlengte van het WAN-adres."""
    wan_static_gateway: str = ""
    """Alleen bij wan_mode=static: default gateway van de provider."""
    wan_static_dns: list[str] = field(default_factory=list)
    """Alleen bij wan_mode=static: DNS-servers voor de router zelf."""
    lan_ports: list[str] = field(default_factory=lambda: ["eth1"])
    """Fysieke LAN-poorten die in de bridge komen."""
    lan_bridge: str = "switch"
    """Naam van de LAN-bridge (IPv4, geen VLAN). 'switch' is duidelijker dan br0."""
    lan_ip: str = "192.168.88.1"
    lan_prefix: int = 24
    dhcp_enabled: bool = False
    """DHCP-server (dnsmasq) standaard uit; aan als de router het LAN bedient."""
    dhcp_start: str = "192.168.88.100"
    dhcp_end: str = "192.168.88.250"
    dhcp_lease: str = "12h"
    dns_domain: str = "lan"
    dns_upstream: list[str] = field(default_factory=lambda: ["1.1.1.1", "8.8.8.8"])
    dhcp_reservations: list[DhcpReservation] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 0 Betekent in de GUI 'geen VLAN'. Dat ook in oude/backup-configs
        # normaliseren, anders blijft een oude subinterface zoals enp1s0.10
        # achter while de rest al untagged wil.
        if self.wan_vlan_id == 0:
            self.wan_vlan_id = None

    @property
    def wan_iface(self) -> str:
        """Interface waarop PPPoE-client frames verstuurd worden."""
        return self.wan if not self.wan_vlan_id else f"{self.wan}.{self.wan_vlan_id}"

    @property
    def vlan_name(self) -> str | None:
        return None if not self.wan_vlan_id else self.wan_iface

    @property
    def lan_iface(self) -> str:
        return self.lan_bridge

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(f"{self.lan_ip}/{self.lan_prefix}", strict=False)


@dataclass
class PPPoEClient:
    enabled: bool = False
    interface: str = ""
    """Leeg = automatisch de WAN-interface (met VLAN indien ingesteld)."""
    username: str = ""
    password: str = ""
    service_name: str = ""
    usepeerdns: bool = True
    mtu: int = 1492
    demand: bool = False
    """Alleen verbinden als er verkeer is."""
    idle_seconds: int = 300
    """Verbreken na zoveel seconden zonder verkeer."""

    def iface(self, cfg: "Config") -> str:
        return self.interface or cfg.active_wan_iface


@dataclass
class WifiAp:
    interface: str = ""
    """Draadloze NIC voor dit accesspoint."""
    band: str = "2.4"
    """'2.4' of '5' (GHz)."""
    channel: int = 0
    """0 = automatisch kanaal."""


@dataclass
class Wifi:
    enabled: bool = False
    """Standaard uit: niet iedere router heeft een draadloze NIC."""
    ssid: str = ""
    password: str = ""
    country: str = "BE"
    aps: list[WifiAp] = field(default_factory=list)
    """Eén accesspoint per radio; SSID en wachtwoord zijn gedeeld."""


@dataclass
class PortForward:
    name: str = "forward"
    protocol: str = "tcp"
    external_port: int = 8080
    internal_ip: str = ""
    internal_port: int = 80
    source: str = "any"
    """'any' of een IPv4-netwerk (CIDR)."""
    udp_timeout: int = 0
    """UDP-timeout voor deze forward; 0 = globale waarde."""


@dataclass
class Firewall:
    wan_input_policy: str = "drop"
    allow_ping_wan: bool = True
    allow_ssh_lan: bool = True
    allow_dns_dhcp_lan: bool = True
    allow_gui_lan: bool = True
    allow_gui_bootstrap: bool = True
    """Alleen tijdens de eerste setup: GUI tijdelijk bereikbaar via WAN."""
    force_lan_dns_via_router: bool = True
    """DNS van LAN-klanten altijd via de router (voor latere DNS-filtering)."""
    udp_nat_timeout: int = 30
    """Seconden voor UDP NAT-verlooptijd; 0 = geen eigen timeout (kernel-default)."""
    no_track_udp_ports: list[int] = field(default_factory=list)
    """UDP-poorten zonder conntrack (bijv. NTP). Niet voor poortforwards."""
    forwards: list[PortForward] = field(default_factory=list)


@dataclass
class GUI:
    bind: str = "192.168.88.1"
    """Alleen 127.0.0.1 (lokaal) of het LAN-IP; nooit 0.0.0.0."""
    port: int = 8080


@dataclass
class System:
    cpu_governor: str = "powersave"
    """CPUgovernor via cpupower; standaard zuinig (minder warmte)."""
    cpu_min_mhz: int | None = 800
    """Ondergrens CPU-frequentie in MHz; None = kernel-default (hier 480)."""
    cpu_max_mhz: int | None = 1600
    """Bovengrens CPU-frequentie in MHz; None = maximum (hier 2500). Genoeg voor 50%."""


@dataclass
class Config:
    version: int = 1
    bootstrapped: bool = False
    """False = eerste setup; na de eerste Toepassen automatisch True."""
    hostname: str = "bassie-router"
    """Hostname van de router; wordt bij Toepassen in /etc/hostname gezet."""
    interfaces: Interfaces = field(default_factory=Interfaces)
    pppoe_client: PPPoEClient = field(default_factory=PPPoEClient)
    wifi: Wifi = field(default_factory=Wifi)
    firewall: Firewall = field(default_factory=Firewall)
    gui: GUI = field(default_factory=GUI)
    system: System = field(default_factory=System)

    def __post_init__(self) -> None:
        # Oude configs met wan_mode='pppoe' maar zonder account zijn in feite
        # gewoon een DHCP- of statische WAN-NIC. PPPoE is stap 2 (ppp0).
        if self.interfaces.wan_mode == "pppoe" and not self.pppoe_client.enabled:
            self.interfaces.wan_mode = (
                "static" if self.interfaces.wan_static_ip else "dhcp"
            )

    @property
    def active_vlan_id(self) -> int | None:
        """Het VLAN-id wordt alleen toegepast zolang PPPoE aan staat.

        De ingestelde waarde blijft in de config staan, zodat hij in de GUI
        zichtbaar blijft en terugkomt als PPPoE weer wordt ingeschakeld.
        """
        if not self.pppoe_client.enabled:
            return None
        return self.interfaces.wan_vlan_id

    @property
    def active_wan_iface(self) -> str:
        """WAN-interface die werkelijk gebruikt wordt (fysiek of VLAN-subinterface)."""
        vid = self.active_vlan_id
        return self.interfaces.wan if not vid else f"{self.interfaces.wan}.{vid}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        return _from_dict(cls, data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_path(path: str | Path | None = None) -> Path:
    return Path(
        path
        or os.environ.get("BASSIE_CONFIG")
        or os.environ.get("ROUTERCTL_CONFIG")
        or DEFAULT_CONFIG_PATH
    )


def load_config(path: str | Path | None = None) -> Config:
    cfg_path = config_path(path)
    if not cfg_path.exists():
        return Config()
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"kan {cfg_path} niet lezen: {exc}") from exc
    return Config.from_dict(data)


@dataclass
class MigrationReport:
    path: Path
    backup: Path | None = None
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Maak van geneste dicts platte 'a.b.c'-sleutels voor de migratierapportage."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten(value, name))
        else:
            result[name] = value
    return result


def migrate_config(
    path: str | Path | None = None,
    *,
    backup_dir: str | Path = "/var/backups/bassie-router",
) -> MigrationReport:
    """Vul ontbrekende parameters met defaults aan, met backup van het origineel.

    Bestaande waarden blijven ongewijzigd. Verouderde/onbekende sleutels worden
    gemeld en niet overgenomen.
    """
    cfg_path = config_path(path)
    report = MigrationReport(path=cfg_path)
    if not cfg_path.exists():
        report.errors.append(f"geen configuratie gevonden: {cfg_path}")
        return report
    try:
        original = cfg_path.read_bytes()
        raw = json.loads(original.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.errors.append(f"kan configuratie niet lezen: {exc}")
        return report
    if not isinstance(raw, dict):
        report.errors.append("configuratie is geen JSON-object")
        return report

    cfg = Config.from_dict(raw)
    if "bootstrapped" not in raw:
        # Oudere versies kenden deze schakelaar niet. Na installatie geldt
        # opnieuw: alle poorten zijn LAN totdat in de GUI een WAN-poort
        # gekozen wordt. De GUI-bind wordt door install.sh naar het DHCP-adres
        # van de bridge gezet; andere oude waarden blijven behouden.
        cfg.bootstrapped = False
        cfg.firewall.allow_gui_bootstrap = True
        report.notes.append(
            "oud configuratieformaat: bootstrapped=False; "
            "GUI-bind wordt aangepast naar het DHCP-adres van de bridge"
        )

    migrated = cfg.to_dict()
    old_keys = set(_flatten(raw))
    new_keys = set(_flatten(migrated))
    report.added = sorted(new_keys - old_keys)
    report.removed = sorted(old_keys - new_keys)

    try:
        directory = Path(backup_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        backup = directory / f"config-{stamp}.json"
        backup.write_bytes(original)
        os.chmod(backup, 0o600)
        report.backup = backup
        save_config(cfg, cfg_path)
    except OSError as exc:
        report.errors.append(f"migratie faalde: {exc}")
    return report


def save_config(cfg: Config, path: str | Path | None = None) -> Path:
    cfg_path = config_path(path)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=cfg_path.parent, prefix=".bassie-config-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, cfg_path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    return cfg_path


def _ip(value: str) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(value.strip())


def _network(value: str) -> ipaddress.IPv4Network:
    return ipaddress.ip_network(value.strip(), strict=False)


def validate(cfg: Config) -> list[str]:
    """Geeft een lijst met foutmeldingen terug (leeg = geldig)."""
    errors: list[str] = []
    i = cfg.interfaces

    if not HOSTNAME_RE.match(cfg.hostname):
        errors.append("hostname mag alleen letters, cijfers en streepjes bevatten")

    for label, name in (
        ("WAN-interface", i.wan),
        ("LAN-bridge", i.lan_bridge),
    ):
        if not IFACE_RE.match(name):
            errors.append(f"{label} '{name}' is geen geldige interfacenaam")
    if len(i.lan_ports) < 1:
        errors.append("er moet minimaal één LAN-poort zijn")
    for port in i.lan_ports:
        if not IFACE_RE.match(port):
            errors.append(f"LAN-poort '{port}' is geen geldige interfacenaam")
    if i.lan_bridge in i.lan_ports:
        errors.append("de LAN-bridge mag niet ook als LAN-poort opgegeven zijn")
    if i.wan in i.lan_ports:
        errors.append("de WAN-poort mag niet ook als LAN-poort opgegeven zijn")
    if i.wan_vlan_id is not None and not 2 <= i.wan_vlan_id <= 4094:
        errors.append("WAN VLAN-id moet tussen 2 en 4094 liggen")
    if not IFACE_RE.match(i.wan_iface):
        errors.append(f"afgeleide WAN-interface '{i.wan_iface}' is ongeldig")

    if i.wan_mode not in ("pppoe", "dhcp", "static"):
        errors.append("WAN-modus moet pppoe, dhcp of static zijn")
    if i.wan_mode == "static" or i.wan_static_ip or i.wan_static_gateway:
        # Bij PPPoE mogen dezelfde velden een modem-beheeradres geven.
        label = "statisch WAN-adres" if i.wan_mode == "static" else "modem-beheeradres"
        if not i.wan_static_ip:
            errors.append(f"WAN-IP is verplicht bij {label}")
        else:
            try:
                wan_ip = _ip(i.wan_static_ip)
                wan_net = ipaddress.ip_network(
                    f"{wan_ip}/{i.wan_static_prefix}", strict=False
                )
                if not 0 <= i.wan_static_prefix <= 32:
                    errors.append("WAN-prefix moet tussen 0 en 32 liggen")
                if wan_ip in (wan_net.network_address, wan_net.broadcast_address):
                    errors.append("WAN-IP mag niet het netwerk- of broadcastadres zijn")
                if i.wan_static_gateway:
                    gateway = _ip(i.wan_static_gateway)
                    if gateway not in wan_net:
                        errors.append(f"WAN-gateway {gateway} ligt niet in {wan_net}")
            except (ValueError, ipaddress.AddressValueError) as exc:
                errors.append(f"ongeldig {label}: {exc}")
        for server in i.wan_static_dns:
            try:
                _ip(server)
            except ValueError:
                errors.append(f"ongeldige WAN-DNS '{server}' (alleen IPv4)")

    try:
        lan_ip = _ip(i.lan_ip)
        net = i.network
        if net.version != 4:
            errors.append("LAN-netwerk moet IPv4 zijn")
        if not 0 <= i.lan_prefix <= 32:
            errors.append("LAN-prefix moet tussen 0 en 32 liggen")
        if lan_ip in (net.network_address, net.broadcast_address):
            errors.append("LAN-IP mag niet het netwerk- of broadcastadres zijn")
        if i.dhcp_enabled:
            # Alleen controleren als de router zelf DHCP geeft. Met DHCP uit is
            # de router klant bij een andere DHCP-server en is de range niet
            # van toepassing (stadiaire defaults mogen dan gewoon blijven staan).
            for label, value in (("DHCP-start", i.dhcp_start), ("DHCP-eind", i.dhcp_end)):
                addr = _ip(value)
                if addr not in net:
                    errors.append(f"{label} {value} ligt niet in {net}")
            if _ip(i.dhcp_start) > _ip(i.dhcp_end):
                errors.append("DHCP-start moet vóór DHCP-eind liggen")
            if _ip(i.dhcp_start) == lan_ip or _ip(i.dhcp_end) == lan_ip:
                errors.append("DHCP-range mag het LAN-IP niet bevatten")
    except (ValueError, ipaddress.AddressValueError) as exc:
        errors.append(f"ongeldig LAN-adres: {exc}")

    seen_macs: set[str] = set()
    seen_ips: set[str] = set()
    try:
        reservation_net = i.network
        reservation_lan_ip = _ip(i.lan_ip)
    except ValueError:
        reservation_net = None
        reservation_lan_ip = None
    for number, reservation in enumerate(i.dhcp_reservations, 1):
        label = f"DHCP-vaste toewijzing {number}"
        mac = reservation.mac.strip().lower()
        if not MAC_RE.match(mac):
            errors.append(f"{label}: ongeldig MAC-adres '{reservation.mac}'")
        elif mac in seen_macs:
            errors.append(f"{label}: MAC {mac} komt tweemaal voor")
        else:
            seen_macs.add(mac)
        try:
            address = _ip(reservation.ip)
        except ValueError:
            errors.append(f"{label}: ongeldig IP-adres '{reservation.ip}'")
            continue
        if reservation_net is not None:
            if address not in reservation_net:
                errors.append(f"{label}: IP {address} ligt niet in {reservation_net}")
            if address == reservation_lan_ip or address in (
                reservation_net.network_address,
                reservation_net.broadcast_address,
            ):
                errors.append(f"{label}: IP {address} is niet bruikbaar")
        if address in seen_ips:
            errors.append(f"{label}: IP {address} komt tweemaal voor")
        else:
            seen_ips.add(address)
        if reservation.name and not HOSTNAME_RE.match(reservation.name):
            errors.append(f"{label}: ongeldige naam '{reservation.name}'")

    if not NAME_RE.match(i.dns_domain.replace(".", "_")):
        errors.append("DNS-domein bevat ongeldige tekens")
    for server in i.dns_upstream:
        try:
            _ip(server)
        except ValueError:
            errors.append(f"ongeldige DNS-upstream '{server}' (alleen IPv4)")

    c = cfg.pppoe_client
    if i.wan_mode == "pppoe" and c.enabled:
        if not c.username.strip():
            errors.append("PPPoE-client: username is verplicht")
        if not c.password:
            errors.append("PPPoE-client: password is verplicht")
        if c.interface and not IFACE_RE.match(c.interface):
            errors.append("PPPoE-client: ongeldige interface")
        if not 576 <= c.mtu <= 1500:
            errors.append("PPPoE-client: MTU moet tussen 576 en 1500 liggen")
        if not 0 <= c.idle_seconds <= 86400:
            errors.append("PPPoE-client: idle-tijd moet tussen 0 en 86400 seconden liggen")

    w = cfg.wifi
    if w.enabled:
        if not 1 <= len(w.ssid.strip()) <= 32:
            errors.append("wifi: SSID moet tussen 1 en 32 tekens zijn")
        if not 8 <= len(w.password) <= 63 or "\n" in w.password:
            errors.append("wifi: wachtwoord moet tussen 8 en 63 tekens zijn")
        if not re.fullmatch(r"[A-Z]{2}", w.country):
            errors.append("wifi: landcode moet twee hoofdletters zijn")
        for ap in w.aps:
            if ap.interface and not IFACE_RE.match(ap.interface):
                errors.append("wifi: ongeldige interface")
            if ap.band not in ("2.4", "5"):
                errors.append("wifi: band moet 2.4 of 5 GHz zijn")
            if ap.band == "2.4" and ap.channel not in range(0, 14):
                errors.append("wifi: kanaal voor 2,4 GHz moet 0 of 1 t/m 13 zijn")
            if ap.band == "5" and ap.channel and ap.channel not in range(36, 197):
                errors.append("wifi: kanaal voor 5 GHz moet 0 of 36 t/m 196 zijn")

    fw = cfg.firewall
    if fw.wan_input_policy not in ("drop", "accept"):
        errors.append("firewall: wan_input_policy moet 'drop' of 'accept' zijn")
    if not 0 <= fw.udp_nat_timeout <= 86400:
        errors.append("firewall: UDP NAT-timeout moet tussen 0 en 86400 seconden liggen")
    for port in fw.no_track_udp_ports:
        if not 1 <= port <= 65535:
            errors.append(f"firewall: no-track poort {port} is ongeldig")
    if len(set(fw.no_track_udp_ports)) != len(fw.no_track_udp_ports):
        errors.append("firewall: dubbele no-track poort")
    forwarded_udp = {
        r.external_port for r in fw.forwards if r.protocol == "udp"
    }
    for port in forwarded_udp & set(fw.no_track_udp_ports):
        errors.append(
            f"firewall: UDP-poort {port} is zowel no-track als poortforward; dat kan niet samen"
        )
    for n, rule in enumerate(fw.forwards, 1):
        if not NAME_RE.match(rule.name):
            errors.append(f"forward {n}: naam bevat ongeldige tekens")
        if rule.protocol not in ("tcp", "udp"):
            errors.append(f"forward {n}: protocol moet tcp of udp zijn")
        if not 1 <= rule.external_port <= 65535 or not 1 <= rule.internal_port <= 65535:
            errors.append(f"forward {n}: poorten moeten tussen 1 en 65535 liggen")
        if not 0 <= rule.udp_timeout <= 86400:
            errors.append(f"forward {n}: UDP-timeout moet tussen 0 en 86400 seconden liggen")
        if rule.udp_timeout and rule.protocol != "udp":
            errors.append(f"forward {n}: UDP-timeout is alleen voor udp-regels")
        try:
            target = _ip(rule.internal_ip)
            if target not in net:
                errors.append(f"forward {n}: {rule.internal_ip} ligt niet in het LAN")
        except (ValueError, UnboundLocalError):
            errors.append(f"forward {n}: ongeldig intern IP")
        if rule.source.strip().lower() != "any":
            try:
                src = _network(rule.source)
                if src.version != 4:
                    errors.append(f"forward {n}: source moet IPv4 zijn")
            except ValueError:
                errors.append(f"forward {n}: ongeldige source '{rule.source}'")

    if cfg.bootstrapped and fw.allow_gui_bootstrap:
        errors.append("initiele setup is afgerond: 'GUI via WAN' moet uitstaan")

    if not 1 <= cfg.gui.port <= 65535:
        errors.append("GUI-poort moet tussen 1 en 65535 liggen")
    try:
        bind_ip = _ip(cfg.gui.bind)
        if bind_ip.is_unspecified or bind_ip.is_multicast:
            errors.append("GUI-bind moet een gewoon IPv4-adres zijn (geen 0.0.0.0)")
        if cfg.bootstrapped:
            if cfg.interfaces.dhcp_enabled:
                # Router is zelf DHCP-server: dan geldt het ingestelde LAN-netwerk.
                lan_net = ipaddress.IPv4Network(
                    f"{cfg.interfaces.lan_ip}/{cfg.interfaces.lan_prefix}", strict=False
                )
                if not (bind_ip.is_loopback or bind_ip in lan_net):
                    errors.append("GUI-bind moet 127.0.0.1 of een adres in het LAN-netwerk zijn")
                if bind_ip in (lan_net.network_address, lan_net.broadcast_address):
                    errors.append("GUI-bind mag niet het netwerk- of broadcastadres zijn")
            # Met DHCP-server uit komt het adres van een andere DHCP-server;
            # dat adres staat in gui.bind en is hier niet vooraf bekend.
            # 0.0.0.0 en multicast zijn hierboven al afgewezen.
    except ValueError:
        errors.append("GUI-bind moet een IPv4-adres zijn")

    s = cfg.system
    if s.cpu_governor not in CPU_GOVERNORS:
        errors.append("CPU-governor is ongeldig")
    for label, value in (("CPU-min", s.cpu_min_mhz), ("CPU-max", s.cpu_max_mhz)):
        if value is not None and not 1 <= value <= 100000:
            errors.append(f"{label} moet tussen 1 en 100000 MHz liggen")
    if (
        s.cpu_min_mhz is not None
        and s.cpu_max_mhz is not None
        and s.cpu_min_mhz > s.cpu_max_mhz
    ):
        errors.append("CPU-min mag niet hoger zijn dan CPU-max")

    return errors
