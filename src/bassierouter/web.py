"""HTTP-GUI voor Bassie-Router."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.exceptions import abort

from . import __release_date__, __version__, connectivity, dhcp, hw

try:
    import pam  # type: ignore[import-not-found]
except ImportError:  # python3-pam nog niet geïnstalleerd (bijv. in tests)
    pam = None  # type: ignore[assignment]

from .services import MAX_WIFI_APS, detect_wireless_interfaces, wifi_rows

from .apply import (
    ApplyReport,
    apply_config,
    apply_cpu_profile,
    applied_config_path,
    config_consistency,
    discard_changes,
    pending_changes,
    rollback,
    snapshot_applied,
    status,
)
from .config import (
    CPU_GOVERNORS,
    Config,
    ConfigError,
    DhcpReservation,
    WifiAp,
    config_path,
    load_config,
    save_config,
    validate,
)
from .runner import CommandRunner, LocalRunner


def _bool(form: Any, key: str) -> bool:
    return key in form


def _short_name(value: str) -> str:
    """Apparaten melden vaak 'naam.local'; we bewaren alleen de korte naam."""
    return value.strip().split(".", 1)[0]


def _int(form: Any, key: str, default: int = 0) -> int:
    value = str(form.get(key, "")).strip()
    return int(value) if value else default


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def available_interfaces() -> list[str]:
    """Fysieke NIC's, voor de WAN-dropdowns."""
    try:
        return sorted(p.name for p in Path("/sys/class/net").iterdir() if p.is_dir())
    except OSError:
        return []


def _ipv4_from(command: list[str]) -> set[str]:
    """Haal globale IPv4-adressen op uit `ip -o addr show`."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    addresses: set[str] = set()
    fields = result.stdout.split()
    for index, field in enumerate(fields[:-1]):
        if field == "inet":
            addresses.add(fields[index + 1].split("/", 1)[0])
    return addresses


def _local_ipv4() -> set[str]:
    return _ipv4_from(["/usr/sbin/ip", "-4", "-o", "addr", "show", "scope", "global"])


def _bridge_ipv4(bridge: str) -> str | None:
    addresses = _ipv4_from(
        ["/usr/sbin/ip", "-4", "-o", "addr", "show", "dev", bridge, "scope", "global"]
    )
    return next(iter(addresses), None)


def _wan_dhcp_info(cfg: Config) -> dict[str, str]:
    """Actuele WAN-gegevens van de NIC, alleen ter informatie in de GUI."""

    def output(command: list[str]) -> str:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip()

    iface = cfg.active_wan_iface
    address = ""
    for field in output([
        "/usr/sbin/ip", "-4", "-o", "addr", "show", "dev", iface, "scope", "global"
    ]).split():
        if field[0].isdigit() and "/" in field:
            address = field
            break

    words = output(["/usr/sbin/ip", "-4", "route", "show", "default", "dev", iface]).split()
    gateway = words[words.index("via") + 1] if "via" in words else ""

    dns = ""
    try:
        for line in Path("/etc/resolv.conf").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "nameserver" and not parts[1].startswith("127."):
                dns = parts[1]
                break
    except OSError:
        pass

    return {"address": address, "gateway": gateway, "dns": dns}


def resolve_bind(cfg: Config) -> str:
    """Kies een lokaal IPv4-adres voor de GUI; nooit 0.0.0.0.

    DHCP kan na een reboot een ander adres geven dan `gui.bind`. Dan luisteren
    we op het adres dat de LAN-bridge nu echt heeft. Als er (nog) geen adres is,
    wachten we even op de DHCP-client en vallen we terug op 127.0.0.1.
    """
    configured = cfg.gui.bind
    bridge = cfg.interfaces.lan_bridge
    if configured == "127.0.0.1":
        # Tijdens de initiele setup is 127.0.0.1 alleen een tijdelijke plek:
        # zodra de bridge een DHCP-adres heeft, luisteren we daarop. Als de
        # gebruiker het later bewust instelt, blijft het lokaal.
        if cfg.bootstrapped:
            return configured
    elif configured in _local_ipv4():
        return configured
    for _ in range(15):
        current = _bridge_ipv4(bridge)
        if current:
            return current
        time.sleep(2)
    return "127.0.0.1"


def create_app(
    config_file: str | None = None,
    runner: CommandRunner | None = None,
    root: str = "/",
) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("BASSIE_SECRET_KEY") or secrets.token_hex(32)
    app.config["BASSIE_CONFIG_FILE"] = str(config_path(config_file))
    app.config["BASSIE_RUNNER"] = runner or LocalRunner()
    app.config["BASSIE_ROOT"] = root

    # Aanmelden met root + SSH-wachtwoord via PAM. De sessie is 30 minuten
    # geldig (absolute timeout, ook als de browser het cookie verlengt).
    try:
        minutes = max(1, int(os.environ.get("BASSIE_GUI_SESSION_MINUTES", "30")))
    except ValueError:
        minutes = 30
    app.config["BASSIE_SESSION_SECONDS"] = minutes * 60
    app.permanent_session_lifetime = timedelta(minutes=minutes)
    # PAM is de normale weg. Debian 13 levert geen bruikbaar python3-pam meer;
    # dan gebruiken we de pamtester-CLI (libpam_misc leest het wachtwoord van
    # stdin). Beide praten tegen /etc/pam.d/bassie-router.
    app.config["BASSIE_PAM"] = os.environ.get("BASSIE_GUI_PAM", "1") != "0"
    fallback_user = os.environ.get("BASSIE_GUI_USER", "root")
    fallback_password = os.environ.get("BASSIE_GUI_PASSWORD", "")
    app.config["BASSIE_FALLBACK_AUTH"] = (
        (fallback_user, fallback_password) if fallback_password else None
    )

    def _pam_ok(username: str, password: str) -> bool:
        if not app.config["BASSIE_PAM"]:
            return False
        if pam is not None:
            try:
                if pam.authenticate(username, password, service="bassie-router"):
                    return True
            except Exception:
                pass
        tester = shutil.which("pamtester")
        if tester:
            try:
                result = subprocess.run(
                    [tester, "bassie-router", username, "authenticate"],
                    input=f"{password}\n",
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
        return False

    def _logged_in() -> bool:
        user = session.get("user")
        login_at = session.get("login_at", 0)
        if not user or not login_at:
            return False
        try:
            age = time.time() - float(login_at)
        except (TypeError, ValueError):
            age = app.config["BASSIE_SESSION_SECONDS"] + 1
        if age > app.config["BASSIE_SESSION_SECONDS"]:
            session.clear()
            return False
        return True

    @app.before_request
    def protect() -> Response | None:
        if request.endpoint not in ("login", "static") and not _logged_in():
            return redirect(url_for("login", next=request.full_path))
        if request.method == "POST":
            token = session.get("csrf_token", "")
            if not token or not hmac.compare_digest(request.form.get("csrf_token", ""), token):
                abort(400, description="Ongeldige CSRF-token")
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login() -> str:
        next_url = request.values.get("next", "")
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = url_for("dashboard")
        if request.method == "POST":
            username = request.form.get("username", "root").strip()
            password = request.form.get("password", "")
            ok = _pam_ok(username, password)
            if not ok and not app.config["BASSIE_PAM"]:
                auth = app.config["BASSIE_FALLBACK_AUTH"]
                if auth:
                    ok = hmac.compare_digest(username, auth[0]) and hmac.compare_digest(
                        password, auth[1]
                    )
            if ok:
                session.permanent = True
                session["user"] = username
                session["login_at"] = time.time()
                return redirect(next_url)
            flash("Onjuiste gebruikersnaam of wachtwoord.", "error")
        return render_template(
            "login.html",
            next_url=next_url,
            session_minutes=app.config["BASSIE_SESSION_SECONDS"] // 60,
        )

    @app.post("/logout")
    def logout() -> str:
        session.clear()
        flash("Uitgelogd.", "ok")
        return redirect(url_for("login"))

    @app.get("/system")
    def system() -> str:
        return render_template(
            "system.html",
            cfg=load(),
            config_file=app.config["BASSIE_CONFIG_FILE"],
            cpu=hw.cpu_info(),
            temps=hw.temperatures(),
            governors=CPU_GOVERNORS,
        )

    @app.get("/system/config")
    def download_config() -> Response:
        path = Path(app.config["BASSIE_CONFIG_FILE"])
        try:
            payload = path.read_text(encoding="utf-8")
        except OSError:
            payload = "{}\n"
        name = f"bassie-router-config-{time.strftime('%Y%m%d-%H%M%S')}.json"
        return Response(
            payload,
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    def system_action(argv: list[str], message: str) -> str:
        if request.form.get("confirm") != "1":
            flash("Vink eerst 'Ik weet het zeker' aan.", "error")
            return redirect(url_for("system"))
        app.config["BASSIE_RUNNER"].run(argv)
        flash(message, "ok")
        return redirect(url_for("system"))

    @app.post("/system/restore")
    def restore_config() -> str:
        if request.form.get("confirm") != "1":
            flash("Vink eerst 'Ik weet het zeker' aan.", "error")
            return redirect(url_for("system"))
        upload = request.files.get("config_file")
        if not upload or not upload.filename:
            flash("Geen bestand gekozen.", "error")
            return redirect(url_for("system"))
        try:
            cfg = Config.from_dict(json.loads(upload.read().decode("utf-8")))
            save_config(cfg, app.config["BASSIE_CONFIG_FILE"])
        except (
            ConfigError, UnicodeDecodeError, json.JSONDecodeError,
            ValueError, TypeError, KeyError,
        ) as exc:
            flash(f"Kon configuratie niet herstellen: {exc}", "error")
            return redirect(url_for("system"))
        note = ""
        if errors := validate(cfg):
            note = " Let op: " + "; ".join(errors[:3])
        flash(
            "Configuratie hersteld. Klik op Wijzigingen Opslaan om ze actief te maken." + note,
            "ok",
        )
        return redirect(url_for("system"))

    @app.post("/system/cpu")
    def cpu_profile() -> str:
        cfg = load()

        def mhz(key: str) -> int | None:
            raw = request.form.get(key, "").strip()
            return int(raw) if raw else None

        try:
            minimum, maximum = mhz("cpu_min_mhz"), mhz("cpu_max_mhz")
        except ValueError:
            flash("Frequenties moeten hele getallen in MHz zijn.", "error")
            return redirect(url_for("system"))
        governor = request.form.get("cpu_governor", "").strip()
        if governor not in CPU_GOVERNORS:
            flash("Onbekende CPU-governor.", "error")
        elif minimum is not None and not 1 <= minimum <= 100000:
            flash("CPU-min moet tussen 1 en 100000 MHz liggen.", "error")
        elif maximum is not None and not 1 <= maximum <= 100000:
            flash("CPU-max moet tussen 1 en 100000 MHz liggen.", "error")
        elif (
            minimum is not None
            and maximum is not None
            and minimum > maximum
        ):
            flash("CPU-min mag niet hoger zijn dan CPU-max.", "error")
        else:
            cfg.system.cpu_governor = governor
            cfg.system.cpu_min_mhz = minimum
            cfg.system.cpu_max_mhz = maximum
            save_config(cfg, app.config["BASSIE_CONFIG_FILE"])
            report = apply_cpu_profile(
                cfg,
                app.config["BASSIE_RUNNER"],
                root=app.config["BASSIE_ROOT"],
            )
            if report.ok:
                snapshot_applied(
                    cfg,
                    app.config["BASSIE_CONFIG_FILE"],
                    root=app.config["BASSIE_ROOT"],
                )
                flash(
                    f"CPU-profiel toegepast: {governor}, "
                    f"{minimum or 'auto'}–{maximum or 'max'} MHz.",
                    "ok",
                )
            else:
                for error in report.errors:
                    flash(error, "error")
        return redirect(url_for("system"))

    @app.post("/system/cpu/suggest")
    def cpu_suggest() -> str:
        cfg = load()
        info = hw.cpu_info()
        if not info["nominal_mhz"]:
            flash("Geen nominale CPUsnelheid gevonden in /proc/cpuinfo.", "error")
            return redirect(url_for("system"))
        cfg.system.cpu_governor = "powersave"
        cfg.system.cpu_min_mhz = info["suggested_min_mhz"]
        cfg.system.cpu_max_mhz = info["suggested_max_mhz"]
        save_config(cfg, app.config["BASSIE_CONFIG_FILE"])
        flash(
            f"Voorstel ingesteld: powersave, {cfg.system.cpu_min_mhz}–"
            f"{cfg.system.cpu_max_mhz} MHz. Klik op Toepassen.",
            "ok",
        )
        return redirect(url_for("system"))

    @app.post("/system/reboot")
    def reboot_system() -> str:
        return system_action(["systemctl", "reboot"], "Systeem wordt opnieuw opgestart.")

    @app.post("/system/shutdown")
    def shutdown_system() -> str:
        return system_action(["systemctl", "poweroff"], "Systeem wordt afgesloten.")

    @app.context_processor
    def inject_token() -> dict[str, str]:
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return {"csrf_token": session["csrf_token"]}

    @app.context_processor
    def inject_interfaces() -> dict[str, list[str]]:
        return {"interfaces": available_interfaces()}

    @app.context_processor
    def inject_version() -> dict[str, object]:
        return {
            "version": __version__,
            "release_date": __release_date__,
            "session_minutes": app.config["BASSIE_SESSION_SECONDS"] // 60,
        }

    @app.context_processor
    def inject_pending() -> dict[str, bool]:
        cfg = load()
        return {
            "pending_changes": pending_changes(
                cfg,
                app.config["BASSIE_CONFIG_FILE"],
                root=app.config["BASSIE_ROOT"],
            ),
            "has_applied_baseline": applied_config_path(
                app.config["BASSIE_CONFIG_FILE"], root=app.config["BASSIE_ROOT"]
            ).exists(),
        }

    def load() -> Config:
        return load_config(app.config["BASSIE_CONFIG_FILE"])

    def store(cfg: Config) -> bool:
        errors = validate(cfg)
        if errors:
            # Duidelijk melden dat er níets is opgeslagen; anders lijkt het alsof
            # de GUI de gegevens wel bewaart.
            flash("Niet opgeslagen — corrigeer deze punten:", "error")
            for error in errors:
                flash(error, "error")
            return False
        try:
            save_config(cfg, app.config["BASSIE_CONFIG_FILE"])
        except OSError as exc:
            flash(f"Kon configuratie niet opslaan: {exc}", "error")
            return False
        flash("Configuratie opgeslagen.", "ok")
        return True

    def save_and_apply(cfg: Config, endpoint: str) -> Response | str:
        """Eén knop: opslaan én meteen actief maken."""
        if not store(cfg):
            return ""  # de pagina wordt opnieuw getoond met de foutmeldingen
        report = apply_config(
            cfg,
            runner=app.config["BASSIE_RUNNER"],
            root=app.config["BASSIE_ROOT"],
            config_file=app.config["BASSIE_CONFIG_FILE"],
        )
        if report.ok:
            checks = connectivity.run_checks(
                cfg, app.config["BASSIE_RUNNER"]
            )
            failed = [check.name for check in checks if not check.ok]
            if failed:
                flash(
                    "Wijzigingen opgeslagen en toegepast. Verbinding nog niet "
                    "bevestigd: " + ", ".join(failed) + ". Zie Status.",
                    "warning",
                )
            else:
                flash(
                    "Wijzigingen opgeslagen, toegepast en verbinding gecontroleerd.",
                    "ok",
                )
        else:
            for error in report.errors:
                flash(error, "error")
        return redirect(url_for(endpoint))

    @app.post("/apply/discard")
    def discard() -> str:
        """Herstelt de laatst toegepaste stand; gebruik door 'Negeren'."""
        report = discard_changes(
            app.config["BASSIE_CONFIG_FILE"], root=app.config["BASSIE_ROOT"]
        )
        if report.ok:
            flash("Wijzigingen genegeerd; velden terug naar de laatst toegepaste situatie. Niets gewijzigd.", "ok")
        else:
            for error in report.errors:
                flash(error, "error")
        allowed = {"dashboard", "network", "pppoe", "wifi", "firewall", "system", "apply"}
        target = request.form.get("next", "")
        return redirect(url_for(target if target in allowed else "dashboard"))

    @app.get("/")
    def dashboard() -> str:
        cfg = load()
        state = status(
            cfg,
            app.config["BASSIE_RUNNER"],
            config_file=app.config["BASSIE_CONFIG_FILE"],
            root=app.config["BASSIE_ROOT"],
        )
        # Met DHCP-server uit is cfg.interfaces.lan_ip slechts een ingestelde
        # waarde (en geen adres van deze router). Toon dan het echte adres van
        # de bridge, of het adres waar de GUI op luistert.
        if cfg.interfaces.dhcp_enabled:
            lan_ip_display = cfg.interfaces.lan_ip
            lan_ip_note = "instelling; wordt na Toepassen actief"
        else:
            lan_ip_display = ""
            for iface in state.get("interfaces", []):
                if iface.get("ifname") != cfg.interfaces.lan_iface:
                    continue
                for addr in iface.get("addr_info", []) or []:
                    address = str(addr.get("address", ""))
                    if "." in address and not address.startswith("127."):
                        lan_ip_display = address
                        break
                break
            if not lan_ip_display and cfg.gui.bind != "127.0.0.1":
                lan_ip_display = cfg.gui.bind
            lan_ip_display = lan_ip_display or "-"
            lan_ip_note = "van externe DHCP-server"
        return render_template(
            "dashboard.html",
            cfg=cfg,
            state=state,
            lan_ip_display=lan_ip_display,
            lan_ip_note=lan_ip_note,
            connectivity=state.get("connectivity", []),
            consistency=state.get("consistency", []),
            errors=validate(cfg),
            temps=hw.temperatures(),
            cpu=hw.cpu_info(),
        )

    @app.route("/network", methods=["GET", "POST"])
    def network() -> str:
        cfg = load()
        if request.method == "POST":
            try:
                i = cfg.interfaces
                cfg.hostname = request.form.get("hostname", cfg.hostname).strip() or cfg.hostname
                # WAN-poort, VLAN en WAN-adres worden bij Provider ingesteld.
                i.wan = request.form.get("wan", i.wan).strip()
                selected_ports = request.form.getlist("lan_ports") or _csv(
                    request.form.get("lan_ports", "")
                )
                i.lan_ports = [
                    port for port in selected_ports
                    if port != i.wan and port != i.lan_bridge
                ]
                i.lan_bridge = request.form.get("lan_bridge", i.lan_bridge).strip() or i.lan_bridge
                i.lan_ip = request.form["lan_ip"].strip()
                i.lan_prefix = _int(request.form, "lan_prefix", 24)
                i.dhcp_enabled = _bool(request.form, "dhcp_enabled")
                i.dhcp_start = request.form["dhcp_start"].strip()
                i.dhcp_end = request.form["dhcp_end"].strip()
                i.dhcp_lease = request.form["dhcp_lease"].strip() or "12h"
                i.dns_domain = request.form["dns_domain"].strip() or "lan"
                i.dns_upstream = _csv(request.form.get("dns_upstream", ""))
                cfg.gui.bind = request.form.get("gui_bind", "127.0.0.1").strip()
                cfg.gui.port = _int(request.form, "gui_port", 8080)
            except (KeyError, ValueError) as exc:
                flash(f"Ongeldige invoer: {exc}", "error")
            else:
                result = save_and_apply(cfg, "network")
                if result:
                    return result
        return render_template("network.html", cfg=cfg)

    @app.route("/dhcp", methods=["GET", "POST"])
    def dhcp_clients() -> str:
        cfg = load()
        runner = app.config["BASSIE_RUNNER"]
        reservations = cfg.interfaces.dhcp_reservations
        if request.method == "POST":
            action = request.form.get("action", "add")
            changed = False
            if action == "remove":
                try:
                    index = int(request.form.get("index", ""))
                except ValueError:
                    index = -1
                if 0 <= index < len(reservations):
                    removed = reservations.pop(index)
                    changed = True
                    flash(f"Vast adres voor {removed.mac} verwijderd.", "ok")
                else:
                    flash("Onbekend vast adres.", "error")
            elif action == "rename":
                try:
                    index = int(request.form.get("index", ""))
                except ValueError:
                    index = -1
                name = _short_name(request.form.get("name", ""))
                if 0 <= index < len(reservations):
                    reservations[index].name = name
                    changed = True
                    flash(f"DNS-naam ingesteld op {name or 'geen naam'}.", "ok")
                else:
                    flash("Onbekend vast adres.", "error")
            else:
                mac = request.form.get("mac", "").strip().lower()
                address = request.form.get("ip", "").strip()
                name = _short_name(request.form.get("name", ""))
                if not mac or not address:
                    flash("Vul een MAC-adres en een IP-adres in.", "error")
                elif any(
                    r.mac.lower() == mac or r.ip == address for r in reservations
                ):
                    flash("Deze MAC of dit IP is al vastgelegd.", "error")
                else:
                    reservations.append(DhcpReservation(
                        mac=mac, ip=address, name=name
                    ))
                    changed = True
                    flash(f"{mac} krijgt nu altijd {address}.", "ok")
            if changed:
                errors = validate(cfg)
                if errors:
                    for error in errors:
                        flash(error, "error")
                else:
                    result = save_and_apply(cfg, "dhcp_clients")
                    if result:
                        return result
            # Bij een fout de niet-opgeslagen wijziging niet laten meedoen.
            cfg = load()
            reservations = cfg.interfaces.dhcp_reservations
        leases = dhcp.read_leases(runner)
        wifi_macs, wifi_ok = dhcp.read_wifi_macs(
            runner, detect_wireless_interfaces()
        )
        for lease in leases:
            lease.via = (
                "Wifi" if lease.mac in wifi_macs
                else ("Kabel" if wifi_ok else "Onbekend")
            )
        return render_template(
            "dhcp.html",
            cfg=cfg,
            leases=leases,
        )

    @app.route("/pppoe", methods=["GET", "POST"])
    def pppoe() -> str:
        cfg = load()
        if request.method == "POST":
            try:
                i = cfg.interfaces
                i.wan = request.form.get("wan", i.wan).strip()
                i.lan_ports = [port for port in i.lan_ports if port != i.wan]
                # Stap 1: de NIC kiest DHCP of statisch. Stap 2: PPPoE erover ->
                # ppp0 wordt de uplink; zonder PPPoE routeert de NIC zelf.
                # Het VLAN-id blijft ingesteld (en zichtbaar in de GUI), maar
                # is alleen actief zolang PPPoE aan staat.
                enabled = _bool(request.form, "client_enabled")
                vlan = request.form.get("wan_vlan_id", "").strip()
                i.wan_vlan_id = (int(vlan) or None) if vlan else None
                nic_mode = request.form.get("wan_nic_mode", "dhcp").strip()
                if nic_mode not in ("dhcp", "static"):
                    nic_mode = "dhcp"
                i.wan_mode = "pppoe" if enabled else nic_mode
                i.wan_static_ip = request.form.get("wan_static_ip", i.wan_static_ip).strip()
                i.wan_static_prefix = _int(
                    request.form, "wan_static_prefix", i.wan_static_prefix
                )
                i.wan_static_gateway = request.form.get(
                    "wan_static_gateway", i.wan_static_gateway
                ).strip()
                if "wan_static_dns" in request.form:
                    i.wan_static_dns = _csv(request.form.get("wan_static_dns", ""))
                c = cfg.pppoe_client
                c.enabled = enabled
                c.interface = request.form.get("client_interface", "").strip()
                c.username = request.form.get("client_username", "").strip()
                c.password = request.form.get("client_password", "")
                c.service_name = request.form.get("client_service", "").strip()
                c.usepeerdns = _bool(request.form, "client_peerdns")
                c.mtu = _int(request.form, "client_mtu", 1492)
                c.demand = _bool(request.form, "client_demand")
                c.idle_seconds = _int(request.form, "client_idle_seconds", 300)

            except (KeyError, ValueError) as exc:
                flash(f"Ongeldige invoer: {exc}", "error")
            else:
                result = save_and_apply(cfg, "pppoe")
                if result:
                    return result
        return render_template(
            "pppoe.html", cfg=cfg, wan_info=_wan_dhcp_info(cfg)
        )

    @app.route("/wifi", methods=["GET", "POST"])
    def wifi() -> str:
        cfg = load()
        if request.method == "POST":
            try:
                w = cfg.wifi
                w.enabled = _bool(request.form, "wifi_enabled")
                w.ssid = request.form.get("wifi_ssid", "").strip()
                w.password = request.form.get("wifi_password", "")
                w.country = request.form.get("wifi_country", "BE").strip().upper()
                w.aps = []
                for index in range(1, MAX_WIFI_APS + 1):
                    iface = request.form.get(f"wifi_iface_{index}", "").strip()
                    band_key = f"wifi_band_{index}"
                    if not iface and band_key not in request.form:
                        continue
                    band = request.form.get(band_key, "2.4").strip()
                    if band not in ("2.4", "5"):
                        band = "2.4"
                    kind = "24" if band == "2.4" else "5"
                    channel = _int(
                        request.form, f"wifi_channel_{kind}_{index}", 0
                    )
                    width = request.form.get(
                        f"wifi_width_{kind}_{index}", ""
                    ).strip()
                    if band == "2.4" and width not in ("20", "40"):
                        width = "40"
                    if band == "5" and width not in ("20", "40", "80", "160"):
                        width = "80"
                    w.aps.append(WifiAp(
                        enabled=_bool(request.form, f"wifi_enabled_{index}"),
                        interface=iface,
                        band=band,
                        channel=channel,
                        width=width,
                    ))
            except (KeyError, ValueError) as exc:
                flash(f"Ongeldige invoer: {exc}", "error")
            else:
                result = save_and_apply(cfg, "wifi")
                if result:
                    return result
        return render_template("wifi.html", cfg=cfg, wifi_rows=wifi_rows(cfg))

    @app.route("/firewall", methods=["GET", "POST"])
    def firewall() -> str:
        cfg = load()
        if request.method == "POST":
            try:
                fw = cfg.firewall
                fw.wan_input_policy = request.form.get("wan_policy", "drop")
                fw.allow_ping_wan = _bool(request.form, "allow_ping")
                fw.allow_ssh_lan = _bool(request.form, "allow_ssh_lan")
                fw.allow_dns_dhcp_lan = _bool(request.form, "allow_dns_lan")
                fw.allow_gui_lan = _bool(request.form, "allow_gui_lan")
                fw.force_lan_dns_via_router = _bool(
                    request.form, "force_lan_dns_via_router"
                )
                fw.udp_nat_timeout = _int(request.form, "udp_nat_timeout", 30)
                fw.no_track_udp_ports = [
                    int(port) for port in _csv(request.form.get("no_track_udp_ports", ""))
                ]
                forwards = []
                index = 0
                while f"fwd_name_{index}" in request.form:
                    if f"fwd_delete_{index}" not in request.form:
                        from .config import PortForward
                        forwards.append(
                            PortForward.from_dict(
                                {
                                    "name": request.form[f"fwd_name_{index}"].strip(),
                                    "protocol": request.form.get(f"fwd_protocol_{index}", "tcp"),
                                    "external_port": _int(request.form, f"fwd_external_{index}"),
                                    "internal_ip": request.form.get(f"fwd_ip_{index}", "").strip(),
                                    "internal_port": _int(request.form, f"fwd_internal_{index}"),
                                    "source": request.form.get(f"fwd_source_{index}", "any").strip() or "any",
                                    "udp_timeout": _int(request.form, f"fwd_udp_timeout_{index}"),
                                }
                            )
                        )
                    index += 1
                if request.form.get("new_fwd_ip", "").strip():
                    from .config import PortForward
                    forwards.append(
                        PortForward(
                            name=request.form.get("new_fwd_name", "").strip() or "forward",
                            protocol=request.form.get("new_fwd_protocol", "tcp"),
                            external_port=_int(request.form, "new_fwd_external"),
                            internal_ip=request.form["new_fwd_ip"].strip(),
                            internal_port=_int(request.form, "new_fwd_internal"),
                            source=request.form.get("new_fwd_source", "any").strip() or "any",
                            udp_timeout=_int(request.form, "new_fwd_udp_timeout"),
                        )
                    )
                fw.forwards = forwards
            except (KeyError, ValueError) as exc:
                flash(f"Ongeldige invoer: {exc}", "error")
            else:
                result = save_and_apply(cfg, "firewall")
                if result:
                    return result
        return render_template(
            "firewall.html",
            cfg=cfg,
            consistency=config_consistency(
                cfg,
                app.config["BASSIE_RUNNER"],
                root=app.config["BASSIE_ROOT"],
            ),
        )

    @app.route("/apply", methods=["GET", "POST"])
    def apply() -> str:
        cfg = load()
        runner = app.config["BASSIE_RUNNER"]
        if request.method == "POST":
            action = request.form.get("action", "preview")
            if action == "apply":
                report: ApplyReport = apply_config(
                    cfg,
                    runner=runner,
                    root=app.config["BASSIE_ROOT"],
                    config_file=app.config["BASSIE_CONFIG_FILE"],
                )
                if report.ok:
                    flash("Configuratie toegepast.", "ok")
                else:
                    for error in report.errors:
                        flash(error, "error")
                return redirect(url_for("apply"))
            if action == "discard":
                report = discard_changes(
                    app.config["BASSIE_CONFIG_FILE"], root=app.config["BASSIE_ROOT"]
                )
                if report.ok:
                    flash("Niet-toegepaste wijzigingen genegeerd.", "ok")
                else:
                    for error in report.errors:
                        flash(error, "error")
                return redirect(url_for("apply"))
            if action == "rollback":
                report = rollback(
                    runner=runner,
                    root=app.config["BASSIE_ROOT"],
                    config_file=app.config["BASSIE_CONFIG_FILE"],
                )
                if report.ok:
                    flash("Laatste backup teruggezet.", "ok")
                else:
                    for error in report.errors:
                        flash(error, "error")
                return redirect(url_for("apply"))
        report = apply_config(
            cfg,
            runner=runner,
            root=app.config["BASSIE_ROOT"],
            dry_run=True,
            config_file=app.config["BASSIE_CONFIG_FILE"],
        )
        return render_template("apply.html", cfg=cfg, report=report)

    return app


def main() -> None:
    cfg = load_config()
    app = create_app()
    bind = resolve_bind(cfg)
    app.logger.warning("GUI luistert op %s:%s", bind, cfg.gui.port)
    app.run(host=bind, port=cfg.gui.port, threaded=True)


if __name__ == "__main__":
    main()
