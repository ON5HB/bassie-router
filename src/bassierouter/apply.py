"""Planning, toepassen en terugdraaien van Bassie-Router-configuratie."""
from __future__ import annotations

import json
import os
import stat
from copy import deepcopy
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import services
from .config import Config, validate
from .connectivity import run_checks as run_connectivity_checks
from .nft import check_ruleset, render_nft
from .runner import CommandError, CommandResult, CommandRunner, LocalRunner

BACKUP_DIR = "/var/backups/bassie-router"
APPLIED_CONFIG_REL = "/var/lib/bassie-router/applied-config.json"
UNITS = (
    "bassie-links.service",
    "bassie-factory-boot.service",
    "bassie-lan-dhcp.service",
    "bassie-cpu.service",
    "bassie-router.service",
    "bassie-pppoe-client.service",
    "bassie-wifi-1.service",
    "bassie-wifi-2.service",
)
EXECUTABLES = {
    "/usr/local/sbin/bassie-setup-links",
    "/usr/local/sbin/bassie-factory-boot",
    "/etc/grub.d/40_custom",
    "/usr/local/sbin/bassie-lan-dhcp",
    "/usr/local/sbin/bassie-pppoe-check",
    "/usr/local/sbin/bassie-cpu-profile",
    "/etc/update-motd.d/40-bassie-router-ip",
}


@dataclass
class ApplyReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    backup: str | None = None


def _local_path(root: str, relative: str) -> Path:
    return Path(root) / relative.lstrip("/")


def build_files(cfg: Config, *, root: str = "/") -> dict[str, str]:
    """Alle te schrijven bestanden (pad -> inhoud)."""

    def read(relative: str) -> str:
        try:
            return _local_path(root, relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    existing_secrets = read("/etc/ppp/chap-secrets")
    existing_hosts = read("/etc/hosts")
    existing_grub = read("/etc/grub.d/40_custom")
    old_hostname = read("/etc/hostname").strip()
    files = {
        "/etc/bassie-router/config.json": json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False) + "\n",
        "/etc/hostname": services.render_hostname(cfg),
        "/etc/hosts": services.render_hosts(cfg, existing_hosts, old_hostname),
        "/etc/motd": services.render_motd(cfg),
        "/etc/grub.d/40_custom": services.render_grub_factory(existing_grub),
        "/etc/update-motd.d/40-bassie-router-ip": services.render_motd_ip_script(cfg),
        "/etc/nftables.conf": render_nft(cfg),
        "/etc/dnsmasq.d/bassie-router.conf": services.render_dnsmasq(cfg),
    }
    wifi_plan = services.wifi_plan(cfg)
    for index in range(1, services.MAX_WIFI_APS + 1):
        iface, band, channel = (
            wifi_plan[index - 1]
            if index <= len(wifi_plan)
            else ("", "2.4", 0)
        )
        files[f"/etc/hostapd/bassie-router-{index}.conf"] = (
            services.render_hostapd_conf(cfg, iface, band, channel)
        )
    files.update({
        "/etc/systemd/system/dnsmasq.service.d/10-bassie.conf": (
            "[Unit]\n"
            "Wants=bassie-links.service\n"
            "After=bassie-links.service\n"
        ),
        "/etc/sysctl.d/99-bassie-router.conf": services.render_sysctl(cfg),
        "/usr/local/sbin/bassie-setup-links": services.render_links_script(cfg),
        "/usr/local/sbin/bassie-factory-boot": services.render_factory_boot_script(cfg),
        "/usr/local/sbin/bassie-lan-dhcp": services.render_lan_dhcp_script(cfg),
        "/usr/local/sbin/bassie-pppoe-check": services.render_pppoe_check_script(cfg),
        "/usr/local/sbin/bassie-cpu-profile": services.render_cpu_script(cfg),
        "/etc/ppp/peers/bassie-isp": services.render_client_options(cfg),
        "/etc/ppp/chap-secrets": services.merge_managed_file(
            existing_secrets, services.render_secrets_block(cfg)
        ),
    })
    files.update(services.render_systemd_units(cfg))
    return files


def build_commands(cfg: Config) -> list[list[str]]:
    commands: list[list[str]] = [
        ["sysctl", "-e", "-p", "/etc/sysctl.d/99-bassie-router.conf"],
        ["hostname", "-F", "/etc/hostname"],
        ["systemctl", "daemon-reload"],
        # Debian's eigen hostapd.service mag niet naast onze AP gaan draaien.
        [
            "sh",
            "-c",
            "systemctl disable --now hostapd.service >/dev/null 2>&1 || true",
        ],
        [
            "sh",
            "-c",
            "command -v grub-mkconfig >/dev/null 2>&1 && "
            "grub-mkconfig -o /boot/grub/grub.cfg || true",
        ],
        ["systemctl", "enable", "--now", "bassie-factory-boot.service"],
        ["systemctl", "enable", "--now", "bassie-links.service"],
        # 'enable --now' voert een al actieve dienst niet opnieuw uit. Zonder
        # deze restart blijft een nieuw WAN-VLAN-id ontbreken als subinterface.
        ["systemctl", "restart", "bassie-links.service"],
        [
            "systemctl",
            "disable" if cfg.interfaces.dhcp_enabled else "enable",
            "--now",
            "bassie-lan-dhcp.service",
        ],
        ["systemctl", "enable", "--now", "bassie-cpu.service"],
        ["systemctl", "enable", "--now", "nftables.service"],
        # 'enable --now' herlaadt geen al draaiende nftables. Laad de zojuist
        # geschreven regels expliciet, anders blijft de oude ruleset actief.
        ["nft", "-f", "/etc/nftables.conf"],
        ["systemctl", "enable", "--now", "dnsmasq.service"],
    ]
    pppoe_active = cfg.interfaces.wan_mode == "pppoe" and cfg.pppoe_client.enabled
    action = "enable" if pppoe_active else "disable"
    commands.append(["systemctl", action, "--now", "bassie-pppoe-client.service"])
    if pppoe_active:
        # Ook hier herlaadt 'enable --now' geen al draaiende pppd: nieuwe
        # VLAN-interface of credentials moeten wél in de volgende sessie.
        commands.append(["systemctl", "restart", "bassie-pppoe-client.service"])
    plan = services.wifi_plan(cfg) if cfg.wifi.enabled else []
    for index in range(1, services.MAX_WIFI_APS + 1):
        unit = f"bassie-wifi-{index}.service"
        needed = index <= len(plan)
        commands.append([
            "systemctl", "enable" if needed else "disable", "--now", unit
        ])
        if needed:
            # 'enable --now' herlaadt geen al draaiende hostapd.
            commands.append(["systemctl", "restart", unit])
    # Geen restart van de GUI hier: dat doodt het lopende verzoek vóór de
    # 'actief'-snapshot is geschreven. Alleen bij een gewijzigd bindadres/poort
    # wordt hieronder uitgesteld herstart.
    commands.append(["systemctl", "enable", "--now", "bassie-router.service"])
    return commands


def apply_cpu_profile(
    cfg: Config, runner: CommandRunner, *, root: str = "/"
) -> ApplyReport:
    """Schrijft alleen het CPU-profiel en activeert de service."""
    unit = services.render_systemd_units(cfg)["/etc/systemd/system/bassie-cpu.service"]
    files = {
        "/usr/local/sbin/bassie-cpu-profile": (
            services.render_cpu_script(cfg), 0o755
        ),
        "/etc/systemd/system/bassie-cpu.service": (unit, 0o644),
    }
    report = ApplyReport(ok=True, files={key: value[0] for key, value in files.items()})
    try:
        for relative, (content, mode) in files.items():
            _write_atomic(root, relative, content, mode)
    except OSError as exc:
        report.ok = False
        report.errors.append(f"CPU-profiel kon niet worden geschreven: {exc}")
        return report
    commands = [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", "bassie-cpu.service"],
        ["systemctl", "restart", "bassie-cpu.service"],
    ]
    for argv in commands:
        result = runner.run(argv)
        if not result.ok:
            report.ok = False
            report.errors.append(f"{' '.join(argv)} faalde: {result.stderr.strip()}")
    return report


def _write_path(target: Path, content: str, mode: int) -> None:
    """Schrijf atomisch naar een absolute path."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".bassie-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_atomic(root: str, relative: str, content: str, mode: int) -> None:
    _write_path(_local_path(root, relative), content, mode)


def applied_config_path(
    config_file: str | Path | None = None, *, root: str = "/"
) -> Path:
    """Pad naar de laatst toegepaste configuratie.

    Bij de standaardlocatie bewaren we de snapshot onder /var/lib; bij een
    afwijkend configpad (tests/demo) naast dat bestand.
    """
    path = Path(config_file) if config_file else Path("/etc/bassie-router/config.json")
    if path.parent == Path("/etc/bassie-router"):
        return _local_path(root, APPLIED_CONFIG_REL)
    return path.with_name(f"applied-{path.name}")


def _config_target(config_file: str | Path | None, root: str) -> Path:
    path = Path(config_file) if config_file else Path("/etc/bassie-router/config.json")
    if path.parent == Path("/etc/bassie-router"):
        return _local_path(root, str(path))
    return path


def snapshot_applied(
    cfg: Config, config_file: str | Path | None = None, *, root: str = "/"
) -> Path:
    """Bewaar de configuratie die nu actief is."""
    target = applied_config_path(config_file, root=root)
    content = json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False) + "\n"
    _write_path(target, content, 0o600)
    return target


def _applied_gui(
    config_file: str | Path | None, root: str
) -> tuple[str, int] | None:
    """Bindadres/poort van de laatst actieve GUI, als die bekend is."""
    path = applied_config_path(config_file, root=root)
    try:
        previous = Config.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    return previous.gui.bind, previous.gui.port


def pending_changes(
    cfg: Config, config_file: str | Path | None = None, *, root: str = "/"
) -> bool:
    """Waar? De opgeslagen config wijkt af van de laatst toegepaste."""
    path = applied_config_path(config_file, root=root)
    if not path.exists():
        return True
    try:
        applied = Config.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return True
    return applied.to_dict() != cfg.to_dict()


def discard_changes(
    config_file: str | Path | None = None, *, root: str = "/"
) -> ApplyReport:
    """Negeer opgeslagen wijzigingen door de laatst toegepaste config terug te zetten."""
    applied_path = applied_config_path(config_file, root=root)
    if not applied_path.exists():
        return ApplyReport(
            ok=False, errors=["geen toegepaste configuratie om naar terug te gaan"]
        )
    try:
        cfg = Config.from_dict(json.loads(applied_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return ApplyReport(ok=False, errors=[f"toegepaste configuratie onleesbaar: {exc}"])
    errors = validate(cfg)
    if errors:
        return ApplyReport(ok=False, errors=errors)
    try:
        _write_path(
            _config_target(config_file, root),
            json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False) + "\n",
            0o600,
        )
    except OSError as exc:
        return ApplyReport(ok=False, errors=[f"wijzigingen negeren faalde: {exc}"])
    return ApplyReport(ok=True)


def _capture(root: str, files: dict[str, str], runner: CommandRunner) -> dict:
    captured = []
    for relative in files:
        path = _local_path(root, relative)
        if path.exists():
            info = path.stat()
            captured.append(
                {
                    "path": relative,
                    "mode": stat.S_IMODE(info.st_mode),
                    "content": path.read_text(encoding="utf-8", errors="replace"),
                }
            )
        else:
            captured.append({"path": relative, "mode": None, "content": None})
    units: dict[str, str] = {}
    for unit in UNITS:
        res = runner.run(["systemctl", "is-enabled", unit])
        units[unit] = res.stdout.strip() or "unknown"
    return {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": captured, "units": units}


def _store_backup(root: str, manifest: dict) -> str:
    relative = f"{BACKUP_DIR}/{time.strftime('%Y%m%d-%H%M%S')}.json"
    path = _local_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".backup-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    return relative


def apply_config(
    cfg: Config,
    *,
    root: str = "/",
    runner: CommandRunner | None = None,
    dry_run: bool = False,
    config_file: str | Path | None = None,
) -> ApplyReport:
    runner = runner or LocalRunner()
    errors = validate(cfg)
    if errors:
        return ApplyReport(ok=False, errors=errors)

    # Eerste toepassen: de gekozen WAN-poort wordt actief, alle overige poorten
    # blijven LAN. Alleen met DHCP-server aan verhuist de GUI naar het ingestelde
    # LAN-IP; met DHCP uit blijft hij op het adres van de externe DHCP-server.
    finishing_bootstrap = not cfg.bootstrapped
    working = deepcopy(cfg) if finishing_bootstrap else cfg
    if finishing_bootstrap:
        working.bootstrapped = True
        if working.interfaces.dhcp_enabled:
            working.gui.bind = working.interfaces.lan_ip
        working.firewall.allow_gui_bootstrap = False
        errors = validate(working)
        if errors:
            return ApplyReport(ok=False, errors=errors)

    files = build_files(working, root=root)
    report = ApplyReport(ok=True, files=files)

    check = check_ruleset(runner, files["/etc/nftables.conf"])
    if not check.ok:
        report.ok = False
        report.errors.append(f"nftables-syntaxcheck faalde: {check.stderr.strip()}")
        return report
    report.checks.append("nftables-syntax OK")

    report.commands = [" ".join(c) for c in build_commands(working)]
    if dry_run:
        report.checks.append("dry-run: niets gewijzigd")
        return report

    previous_gui = _applied_gui(config_file, root)
    manifest = _capture(root, files, runner)
    try:
        for relative, content in files.items():
            if relative in EXECUTABLES:
                mode = 0o755
            elif (
                relative in ("/etc/ppp/chap-secrets", "/etc/bassie-router/config.json")
                or relative.startswith("/etc/hostapd/")
            ):
                # Bevat PPPoE- of WiFi-wachtwoord: nooit wereldleesbaar.
                mode = 0o600
            else:
                mode = 0o644
            _write_atomic(root, relative, content, mode)
        report.backup = _store_backup(root, manifest)
        for argv in build_commands(working):
            result = runner.run(argv)
            if not result.ok:
                raise CommandError(f"{' '.join(argv)} faalde: {result.stderr.strip()}")
        snapshot_applied(working, config_file, root=root)
        if previous_gui is not None and previous_gui != (
            working.gui.bind,
            working.gui.port,
        ):
            delayed = [
                "systemd-run", "--on-active=2", "--unit=bassie-gui-restart",
                "systemctl", "restart", "bassie-router.service",
            ]
            result = runner.run(delayed)
            report.commands.append(" ".join(delayed))
            if not result.ok:
                report.checks.append(
                    f"GUI-herstart uitgesteld niet gestart: {result.stderr.strip()}"
                )
    except (OSError, CommandError) as exc:
        report.ok = False
        report.errors.append(f"toepassen faalde: {exc}; configuratie wordt teruggezet")
        rollback(root=root, runner=runner, manifest=manifest, config_file=config_file)
    if report.ok and finishing_bootstrap:
        address = (
            working.interfaces.lan_ip if working.interfaces.dhcp_enabled else working.gui.bind
        )
        report.checks.append(f"initiele setup afgerond: GUI luistert nu op {address}")
    return report


def latest_backup(root: str = "/") -> Path | None:
    directory = _local_path(root, BACKUP_DIR)
    if not directory.exists():
        return None
    backups = sorted(directory.glob("*.json"))
    return backups[-1] if backups else None


def rollback(
    *,
    root: str = "/",
    runner: CommandRunner | None = None,
    backup_path: str | None = None,
    manifest: dict | None = None,
    config_file: str | Path | None = None,
) -> ApplyReport:
    runner = runner or LocalRunner()
    report = ApplyReport(ok=True)
    path = _local_path(root, backup_path) if backup_path else latest_backup(root)
    if manifest is None:
        if path is None or not path.exists():
            return ApplyReport(ok=False, errors=["geen backup gevonden om terug te zetten"])
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return ApplyReport(ok=False, errors=[f"backup onleesbaar: {exc}"])
    report.backup = backup_path or (str(path.relative_to(Path(root))) if path else None)

    try:
        for item in manifest["files"]:
            target = _local_path(root, item["path"])
            if item["content"] is None:
                target.unlink(missing_ok=True)
            else:
                _write_atomic(root, item["path"], item["content"], item["mode"] or 0o644)
    except OSError as exc:
        report.ok = False
        report.errors.append(f"terugzetten bestanden faalde: {exc}")
        return report

    runner.run(["systemctl", "daemon-reload"])
    runner.run([
        "sh", "-c",
        "command -v grub-mkconfig >/dev/null 2>&1 && "
        "grub-mkconfig -o /boot/grub/grub.cfg || true",
    ])
    runner.run(["hostname", "-F", "/etc/hostname"])
    for argv in (
        ["systemctl", "restart", "bassie-links.service"],
        ["systemctl", "restart", "bassie-cpu.service"],
        ["systemctl", "restart", "nftables.service"],
        ["systemctl", "restart", "dnsmasq.service"],
    ):
        runner.run(argv)
    for unit, state in manifest.get("units", {}).items():
        action = "enable" if state == "enabled" else "disable"
        runner.run(["systemctl", f"{action}", "--now", unit])
    restored = next(
        (
            item["content"]
            for item in manifest["files"]
            if item["path"] == "/etc/bassie-router/config.json" and item["content"]
        ),
        None,
    )
    if restored:
        try:
            snapshot_applied(Config.from_dict(json.loads(restored)), config_file, root=root)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            report.errors.append(f"toegepaste configuratie niet bijgewerkt: {exc}")
    report.commands = ["bestanden hersteld", "systemd-units herstart"]
    return report


def config_consistency(
    cfg: Config,
    runner: CommandRunner | None = None,
    *,
    root: str = "/",
) -> list[dict[str, str | bool]]:
    """Vergelijk opgeslagen instellingen met de actieve staat op de router."""
    runner = runner or LocalRunner()
    checks: list[dict[str, str | bool]] = []

    rules = runner.run(["nft", "list", "ruleset"]).stdout
    dns_wanted = cfg.firewall.force_lan_dns_via_router
    dns_active = "DNS alleen via router" in rules
    checks.append({
        "name": "DNS van LAN via router",
        "ok": dns_wanted == dns_active,
        "detail": (
            f"config: {'aan' if dns_wanted else 'uit'} · "
            f"actief: {'aan' if dns_active else 'uit'}"
        ),
    })

    dhcp_wanted = cfg.interfaces.dhcp_enabled
    dhcp_file = _local_path(root, "/etc/dnsmasq.d/bassie-router.conf")
    try:
        dhcp_active = "dhcp-range=" in dhcp_file.read_text(encoding="utf-8")
    except OSError:
        dhcp_active = False
    checks.append({
        "name": "DHCP-server op LAN",
        "ok": dhcp_wanted == dhcp_active,
        "detail": (
            f"config: {'aan' if dhcp_wanted else 'uit'} · "
            f"actief: {'aan' if dhcp_active else 'uit'}"
        ),
    })

    pppoe_wanted = cfg.interfaces.wan_mode == "pppoe" and cfg.pppoe_client.enabled
    pppoe_result = runner.run(
        ["systemctl", "is-active", "bassie-pppoe-client.service"]
    )
    pppoe_active = pppoe_result.stdout.strip() == "active"
    checks.append({
        "name": "PPPoE-client",
        "ok": pppoe_wanted == pppoe_active,
        "detail": (
            f"config: {'aan' if pppoe_wanted else 'uit'} · "
            f"actief: {'aan' if pppoe_active else 'uit'}"
        ),
    })
    return checks


def service_status(
    cfg: Config,
    runner: CommandRunner | None = None,
    *,
    root: str = "/",
) -> list[dict[str, str | bool]]:
    """Status van de services, met echte controles voor DNS en DHCP."""
    runner = runner or LocalRunner()

    def active(unit: str) -> bool:
        return runner.run(["systemctl", "is-active", unit]).stdout.strip() == "active"

    dnsmasq = active("dnsmasq.service")
    dns_ok = dnsmasq and bool(
        runner.run(["getent", "ahostsv4", "debian.org"]).stdout.strip()
    )
    dhcp_ok = False
    if dnsmasq and cfg.interfaces.dhcp_enabled:
        try:
            conf = _local_path(
                root, "/etc/dnsmasq.d/bassie-router.conf"
            ).read_text(encoding="utf-8")
        except OSError:
            conf = ""
        # Een tweede DHCP-server in het LAN maakt de router zelf DHCP-klant.
        dhcp_ok = "dhcp-range=" in conf and not active("bassie-lan-dhcp.service")

    pppoe_needed = cfg.interfaces.wan_mode == "pppoe" and cfg.pppoe_client.enabled
    wifi_plan = services.wifi_plan(cfg) if cfg.wifi.enabled else []
    wifi_ok = (
        all(active(f"bassie-wifi-{i}.service") for i in range(1, len(wifi_plan) + 1))
        if wifi_plan
        else not any(
            active(f"bassie-wifi-{i}.service")
            for i in range(1, services.MAX_WIFI_APS + 1)
        )
    )
    entries = (
        ("Firewall", active("nftables.service"), True, "niet nodig"),
        ("DHCP server", dhcp_ok, cfg.interfaces.dhcp_enabled, "niet nodig"),
        ("DNS server", dns_ok, True, "niet nodig"),
        ("DHCP client", active("bassie-lan-dhcp.service"), not cfg.interfaces.dhcp_enabled, "niet nodig"),
        ("CPU snelheid", active("bassie-cpu.service"), True, "niet nodig"),
        ("Routering", active("bassie-router.service"), True, "niet nodig"),
        ("PPPoE klant", active("bassie-pppoe-client.service"), pppoe_needed, "uitgeschakeld"),
        ("Wifi accesspoint", wifi_ok, bool(wifi_plan), "uitgeschakeld"),
    )
    return [
        {
            "name": name,
            "active": "active" if ok else "inactive",
            "needed": needed,
            "off_text": off_text,
        }
        for name, ok, needed, off_text in entries
    ]


def status(
    cfg: Config,
    runner: CommandRunner | None = None,
    *,
    config_file: str | Path | None = None,
    root: str = "/",
) -> dict:
    runner = runner or LocalRunner()
    result = runner.run(["ip", "-j", "-brief", "address", "show"])
    interfaces: list[dict] = []
    if result.stdout.strip():
        try:
            interfaces = json.loads(result.stdout)
        except json.JSONDecodeError:
            interfaces = []
    services_state = service_status(cfg, runner, root=root)
    return {
        "wan": cfg.active_wan_iface,
        "lan": cfg.interfaces.lan_iface,
        "lan_ip": cfg.interfaces.lan_ip,
        "interfaces": interfaces,
        "ppp_interfaces": [
            i.get("ifname", "") for i in interfaces if str(i.get("ifname", "")).startswith("ppp")
        ],
        "services": services_state,
        "connectivity": [
            {"name": c.name, "ok": c.ok, "detail": c.detail}
            for c in run_connectivity_checks(cfg, runner)
        ],
        "pending_changes": pending_changes(cfg, config_file, root=root),
        "consistency": config_consistency(cfg, runner, root=root),
    }
