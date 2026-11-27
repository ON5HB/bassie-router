import io
import json
import subprocess
import time

from bassierouter import __release_date__, __version__
from bassierouter.apply import snapshot_applied
from bassierouter.config import Config, config_path, load_config, save_config
from bassierouter.nft import render_nft
from bassierouter.runner import FakeRunner
from bassierouter.services import render_links_script
from bassierouter.web import _ipv4_from, create_app, resolve_bind


def test_ipv4_from_parses_ip_output(monkeypatch):
    class Result:
        stdout = "2: br0    inet 192.168.1.24/24 brd 192.168.1.255 scope global dynamic br0\n"

    monkeypatch.setattr("bassierouter.web.subprocess.run", lambda *a, **k: Result())
    assert _ipv4_from(["ip", "addr"]) == {"192.168.1.24"}


def test_resolve_bind_never_uses_wildcard(tmp_path, monkeypatch):
    cfg = Config()
    cfg.gui.bind = "192.168.1.24"
    cfg.interfaces.lan_bridge = "br0"
    monkeypatch.setattr("bassierouter.web._local_ipv4", lambda: {"192.168.1.24"})
    assert resolve_bind(cfg) == "192.168.1.24"

    # DHCP gaf een ander adres: GUI volgt de bridge, nooit 0.0.0.0.
    monkeypatch.setattr("bassierouter.web._local_ipv4", lambda: set())
    monkeypatch.setattr("bassierouter.web._bridge_ipv4", lambda b: "192.168.1.27")
    assert resolve_bind(cfg) == "192.168.1.27"

    # Nog geen adres: wachten, daarna alleen lokaal bereikbaar.
    monkeypatch.setattr("bassierouter.web._bridge_ipv4", lambda b: None)
    monkeypatch.setattr("bassierouter.web.time.sleep", lambda _: None)
    assert resolve_bind(cfg) == "127.0.0.1"

    # Initiele setup: 127.0.0.1 is tijdelijk; de GUI volgt de bridge.
    cfg.gui.bind = "127.0.0.1"
    monkeypatch.setattr("bassierouter.web._bridge_ipv4", lambda b: "192.168.1.28")
    assert resolve_bind(cfg) == "192.168.1.28"
    # Bewust ingesteld ná de setup: dan blijft het lokaal.
    cfg.bootstrapped = True
    assert resolve_bind(cfg) == "127.0.0.1"


def test_create_app_resolves_default_config_path():
    assert create_app().config["BASSIE_CONFIG_FILE"] == str(config_path(None))


def make_client(tmp_path):
    cfg_file = tmp_path / "config.json"
    app = create_app(config_file=str(cfg_file), runner=FakeRunner(), root=str(tmp_path))
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = "testtoken"
        sess["user"] = "root"
        sess["login_at"] = time.time()
    return client, cfg_file


def test_dashboard_and_pages_load(tmp_path):
    client, _ = make_client(tmp_path)
    for path in ("/", "/network", "/pppoe", "/wifi", "/firewall", "/system", "/apply"):
        assert client.get(path).status_code == 200, path
    body = client.get("/").data
    assert b">Provider</a>" in body
    assert b">PPPoE</a>" not in body
    assert b"Account" in body
    assert f"Versie: {__version__} · {__release_date__}".encode() in body
    assert b"Samen gebouwd door Bas Heijermans (ON5HB, radioamateur:" in body
    assert b"Big Pickle" in body
    assert b"opencode.ai" in body
    for path in ("/network", "/pppoe"):
        page = client.get(path).data
        assert b"Zyxel" not in page, path
    assert b"ONT" in client.get("/pppoe").data
    network_page = client.get("/network").data
    assert b'name="wan"' not in network_page
    assert b'name="wan_nic_mode"' not in network_page
    provider_page = client.get("/pppoe").data
    assert b'name="wan"' in provider_page
    assert b'name="wan_nic_mode"' in provider_page
    nic_select = provider_page.split(b'<select name="wan_nic_mode"', 1)[1].split(b'</select>', 1)[0]
    assert b'value="dhcp"' in nic_select
    assert b'value="static"' in nic_select
    assert b'value="pppoe"' not in nic_select
    assert b'name="wan_vlan_id" type="number" min="0"' in provider_page


def test_apply_page_says_clearly_whether_changes_are_active(tmp_path):
    client, cfg_file = make_client(tmp_path)
    cfg = Config()
    cfg.bootstrapped = True
    save_config(cfg, cfg_file)
    snapshot_applied(cfg, cfg_file, root=str(tmp_path))

    body = client.get("/apply").data
    assert "Wél actief".encode() in body
    assert "Nog NIET actief".encode() not in body

    cfg.interfaces.wan_vlan_id = 6
    save_config(cfg, cfg_file)

    body = client.get("/apply").data
    assert "Nog NIET actief".encode() in body
    assert "Wél actief".encode() not in body
    assert b"Wijzigingen nog NIET actief" in body


def test_dashboard_shows_real_address_when_dhcp_server_off(tmp_path):
    client, cfg_file = make_client(tmp_path)
    cfg = Config()
    cfg.gui.bind = "192.168.1.24"
    save_config(cfg, cfg_file)
    body = client.get("/").data
    assert b"192.168.1.24" in body
    assert b"192.168.88.1" not in body
    assert b"van externe DHCP-server" in body
    assert b"Verbindingschecks" in body
    assert b"Opnieuw controleren" in body


def test_dashboard_shows_configured_lan_ip_when_dhcp_server_on(tmp_path):
    client, cfg_file = make_client(tmp_path)
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    cfg.gui.bind = cfg.interfaces.lan_ip
    save_config(cfg, cfg_file)
    body = client.get("/").data
    assert b"<code>192.168.88.1</code>/24" in body


def make_system_client(tmp_path):
    runner = FakeRunner()
    app = create_app(
        config_file=str(tmp_path / "config.json"), runner=runner, root=str(tmp_path)
    )
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = "testtoken"
        sess["user"] = "root"
        sess["login_at"] = time.time()
    return client, runner


def test_system_tab_and_config_backup(tmp_path):
    client, _ = make_system_client(tmp_path)
    save_config(Config(), tmp_path / "config.json")
    page = client.get("/system")
    assert page.status_code == 200
    assert str(tmp_path / "config.json").encode() in page.data
    assert b"(None)" not in page.data
    assert b"Configuratie downloaden" in page.data
    assert b"Configuratie uploaden" in page.data
    assert b"Bladeren" in page.data
    assert b"Reboot" in page.data
    assert b"Shutdown" in page.data
    download = client.get("/system/config")
    assert download.status_code == 200
    assert download.headers["Content-Disposition"].startswith("attachment;")
    assert json.loads(download.data)["interfaces"]["wan_mode"] == "dhcp"


def test_restore_config_upload(tmp_path):
    client, _ = make_system_client(tmp_path)
    cfg_file = tmp_path / "config.json"
    cfg = Config()
    cfg.hostname = "hersteld"
    payload = json.dumps(cfg.to_dict()).encode()
    response = client.post(
        "/system/restore",
        data={
            "csrf_token": "testtoken",
            "confirm": "1",
            "config_file": (io.BytesIO(payload), "backup.json"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert load_config(cfg_file).hostname == "hersteld"

    cfg.hostname = "mag-niet-opgeslagen-worden"
    client.post(
        "/system/restore",
        data={
            "csrf_token": "testtoken",
            "config_file": (io.BytesIO(json.dumps(cfg.to_dict()).encode()), "backup.json"),
        },
        content_type="multipart/form-data",
    )
    assert load_config(cfg_file).hostname == "hersteld"


def test_cpu_profile_form_saves_and_rejects_bad_range(tmp_path):
    client, runner = make_system_client(tmp_path)
    cfg_file = tmp_path / "config.json"
    response = client.post("/system/cpu", data={
        "csrf_token": "testtoken",
        "cpu_governor": "powersave",
        "cpu_min_mhz": "800",
        "cpu_max_mhz": "1600",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.system.cpu_governor == "powersave"
    assert cfg.system.cpu_min_mhz == 800
    assert cfg.system.cpu_max_mhz == 1600
    calls = [call[0] for call in runner.calls]
    assert ["systemctl", "restart", "bassie-cpu.service"] in calls
    assert (tmp_path / "applied-config.json").exists()

    bad = client.post("/system/cpu", data={
        "csrf_token": "testtoken",
        "cpu_governor": "powersave",
        "cpu_min_mhz": "1600",
        "cpu_max_mhz": "800",
    })
    assert bad.status_code == 302
    assert load_config(cfg_file).system.cpu_min_mhz == 800


def test_cpu_suggest_uses_nominal_speed(tmp_path, monkeypatch):
    client, _ = make_system_client(tmp_path)
    monkeypatch.setattr(
        "bassierouter.web.hw.cpu_info",
        lambda: {
            "nominal_mhz": 1600,
            "suggested_min_mhz": 800,
            "suggested_max_mhz": 1600,
            "available_mhz": [480, 800, 1600, 2500],
            "min_mhz": 480,
            "max_mhz": 2500,
            "governor": "schedutil",
            "available_governors": ["powersave", "schedutil"],
            "available": True,
            "cpupower": True,
        },
    )
    page = client.get("/system")
    assert b"Nominale CPUsnelheid" in page.data
    assert b"1600 MHz" in page.data
    assert b"N3710" not in page.data
    response = client.post("/system/cpu/suggest", data={"csrf_token": "testtoken"})
    assert response.status_code == 302
    cfg = load_config(tmp_path / "config.json")
    assert cfg.system.cpu_governor == "powersave"
    assert cfg.system.cpu_min_mhz == 800
    assert cfg.system.cpu_max_mhz == 1600


def test_dashboard_shows_temperatures_and_cpu_profile(tmp_path, monkeypatch):
    client, _ = make_system_client(tmp_path)
    save_config(Config(), tmp_path / "config.json")
    monkeypatch.setattr(
        "bassierouter.web.hw.temperatures",
        lambda: [{"label": "Core 0", "celsius": 59.0}],
    )
    monkeypatch.setattr(
        "bassierouter.web.hw.cpu_info",
        lambda: {
            "available": True,
            "governor": "powersave",
            "min_mhz": 800,
            "max_mhz": 1600,
            "available_mhz": [480, 800, 1600, 2500],
            "available_governors": ["powersave", "performance"],
            "cpupower": True,
        },
    )
    page = client.get("/")
    assert page.status_code == 200
    assert b"Core 0" in page.data
    assert b"59.0" in page.data
    assert b"800" in page.data
    assert b"1600" in page.data


def test_reboot_and_shutdown_use_ja_nee_popup(tmp_path):
    client, _ = make_system_client(tmp_path)
    page = client.get("/system").data
    assert b"Weet je zeker dat de router moet rebooten?" in page
    assert b"Weet je zeker dat de router moet afsluiten?" in page
    assert page.count(b">Nee</button>") == 2
    assert page.count(b">Ja</button>") == 2
    assert page.count(b'name="confirm" value="1" required') == 3  # restore + reboot + shutdown
    assert page.count(b"Ik weet het zeker") >= 2
    assert b"reportValidity()" in page


def test_reboot_and_shutdown_need_confirmation(tmp_path):
    client, runner = make_system_client(tmp_path)
    response = client.post("/system/reboot", data={"csrf_token": "testtoken"})
    assert response.status_code == 302
    assert runner.calls == []
    client.post("/system/reboot", data={"csrf_token": "testtoken", "confirm": "1"})
    client.post("/system/shutdown", data={"csrf_token": "testtoken", "confirm": "1"})
    calls = [call[0] for call in runner.calls]
    assert ["systemctl", "reboot"] in calls
    assert ["systemctl", "poweroff"] in calls


def test_login_required_and_session_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("BASSIE_GUI_PAM", "0")
    monkeypatch.setenv("BASSIE_GUI_USER", "root")
    monkeypatch.setenv("BASSIE_GUI_PASSWORD", "geheim")
    app = create_app(
        config_file=str(tmp_path / "config.json"),
        runner=FakeRunner(),
        root=str(tmp_path),
    )
    app.config["TESTING"] = True
    client = app.test_client()

    assert client.get("/").status_code == 302
    assert client.get("/login").status_code == 200
    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    wrong = client.post("/login", data={
        "csrf_token": token, "username": "root", "password": "fout",
    })
    assert wrong.status_code == 200
    assert b"Onjuiste" in wrong.data

    ok = client.post("/login", data={
        "csrf_token": token, "username": "root", "password": "geheim",
    })
    assert ok.status_code == 302
    assert client.get("/").status_code == 200

    with client.session_transaction() as sess:
        sess["login_at"] = time.time() - 3600
    assert client.get("/").status_code == 302


def test_login_uses_pamtester_when_python_pam_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("BASSIE_GUI_PAM", "1")
    monkeypatch.delenv("BASSIE_GUI_USER", raising=False)
    monkeypatch.delenv("BASSIE_GUI_PASSWORD", raising=False)
    monkeypatch.setattr("bassierouter.web.pam", None)
    monkeypatch.setattr("bassierouter.web.shutil.which", lambda name: "/usr/bin/pamtester")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("bassierouter.web.subprocess.run", fake_run)
    app = create_app(
        config_file=str(tmp_path / "config.json"),
        runner=FakeRunner(),
        root=str(tmp_path),
    )
    app.config["TESTING"] = True
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    response = client.post("/login", data={
        "csrf_token": token, "username": "root", "password": "geheim",
    })
    assert response.status_code == 302
    assert calls == [(
        ["/usr/bin/pamtester", "bassie-router", "root", "authenticate"], "geheim\n"
    )]


def test_pending_banner_and_discard(tmp_path):
    client, cfg_file = make_client(tmp_path)
    body = client.get("/").data
    assert b"Initiele setup nog niet toegepast" in body
    assert b">Negeren</button>" not in body

    snapshot_applied(Config(), str(cfg_file))
    cfg = load_config(cfg_file)
    cfg.firewall.udp_nat_timeout = 15
    save_config(cfg, cfg_file)

    body = client.get("/").data
    assert b"Wijzigingen nog NIET actief" in body
    assert b"Wijzigingen bekijken" in body

    response = client.post("/apply/discard", data={"csrf_token": "testtoken", "next": "dashboard"})
    assert response.status_code == 302
    assert load_config(cfg_file).firewall.udp_nat_timeout == 30


def test_apply_banner_uses_current_gui_address_with_dhcp_off(tmp_path):
    client, cfg_file = make_client(tmp_path)
    cfg = load_config(cfg_file)
    cfg.gui.bind = "192.168.1.24"
    cfg.interfaces.lan_ip = "10.9.8.1"
    save_config(cfg, cfg_file)

    body = client.get("/apply").data
    assert b"192.168.1.24" in body
    assert b"10.9.8.1" not in body
    assert b"DHCP-server is uit" in body


def test_apply_banner_uses_lan_ip_with_dhcp_on(tmp_path):
    client, cfg_file = make_client(tmp_path)
    cfg = load_config(cfg_file)
    cfg.gui.bind = "192.168.1.24"
    cfg.interfaces.lan_ip = "10.9.8.1"
    cfg.interfaces.dhcp_enabled = True
    save_config(cfg, cfg_file)

    body = client.get("/apply").data
    assert b"10.9.8.1" in body
    assert b"DHCP-server is uit" not in body


def test_dashboard_uses_friendly_service_names(tmp_path):
    client, _ = make_client(tmp_path)
    body = client.get("/").data
    assert b">Firewall</td>" in body
    assert b">DHCP server</td>" in body
    assert b">DNS server</td>" in body
    assert body.index(b">DHCP server</td>") < body.index(b">DNS server</td>")
    assert b"nftables.service" not in body


def test_dashboard_hides_consistency_table(tmp_path):
    client, _ = make_client(tmp_path)
    body = client.get("/").data
    assert b"Configuratie-controle" not in body
    assert b"WIJKT AF" not in body


def test_dashboard_marks_unused_services(tmp_path):
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    cfg_file = tmp_path / "config.json"
    save_config(cfg, cfg_file)
    client, _ = make_client(tmp_path)
    body = client.get("/").data
    assert b">DHCP client</td>" in body
    assert b"niet nodig" in body
    assert b">uitgeschakeld</td>" in body


def test_firewall_warns_when_dns_state_differs(tmp_path):
    client, _ = make_client(tmp_path)
    body = client.get("/firewall").data
    assert b"Let op:" in body
    assert b"actief: uit" in body


def test_post_requires_csrf(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post("/firewall", data={"wan_policy": "drop"})
    assert response.status_code == 400


def test_gui_adds_wan_to_lan_portforward(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/firewall", data={
        "csrf_token": "testtoken",
        "wan_policy": "drop",
        "udp_nat_timeout": "30",
        "new_fwd_name": "web",
        "new_fwd_protocol": "udp",
        "new_fwd_external": "8080",
        "new_fwd_ip": "192.168.88.10",
        "new_fwd_internal": "80",
        "new_fwd_source": "any",
        "new_fwd_udp_timeout": "1",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert len(cfg.firewall.forwards) == 1
    rule = cfg.firewall.forwards[0]
    assert (rule.protocol, rule.external_port, rule.internal_ip, rule.internal_port) == (
        "udp", 8080, "192.168.88.10", 80)
    assert rule.source == "any"
    assert rule.udp_timeout == 1


def test_firewall_form_can_disable_dns_enforcement(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/firewall", data={
        "csrf_token": "testtoken",
        "wan_policy": "drop",
        "udp_nat_timeout": "30",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.firewall.force_lan_dns_via_router is False
    assert "DNS alleen via router" not in render_nft(cfg)


def test_provider_page_shows_current_dhcp_info(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bassierouter.web._wan_dhcp_info",
        lambda cfg: {
            "address": "192.168.1.2/24",
            "gateway": "192.168.1.1",
            "dns": "1.1.1.1",
        },
    )
    client, _ = make_client(tmp_path)
    page = client.get("/pppoe").data
    assert b"DHCP gaf:" in page
    assert b"192.168.1.2/24" in page
    assert b"192.168.1.1" in page
    assert b"1.1.1.1" in page


def test_gui_pppoe_form_sets_provider_vlan(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/pppoe", data={
        "csrf_token": "testtoken",
        "wan_vlan_id": "6",
        "client_enabled": "on",
        "client_username": "provider-user",
        "client_password": "secret",
        "client_mtu": "1492",
        "client_peerdns": "on",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.interfaces.wan_vlan_id == 6
    assert cfg.interfaces.wan_iface == "eth0.6"
    assert cfg.pppoe_client.username == "provider-user"


def test_gui_pppoe_form_sets_idle_timeout(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/pppoe", data={
        "csrf_token": "testtoken",
        "client_enabled": "on",
        "client_username": "provider-user",
        "client_password": "secret",
        "client_mtu": "1492",
        "client_peerdns": "on",
        "client_demand": "on",
        "client_idle_seconds": "120",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.pppoe_client.demand is True
    assert cfg.pppoe_client.idle_seconds == 120


def test_gui_wifi_form_saves_two_accesspoints(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/wifi", data={
        "csrf_token": "testtoken",
        "wifi_enabled": "on",
        "wifi_ssid": "Bassie",
        "wifi_password": "wachtwoord123",
        "wifi_country": "be",
        "wifi_iface_1": "wlx98254a16b8da",
        "wifi_enabled_1": "on",
        "wifi_band_1": "2.4",
        "wifi_channel_24_1": "11",
        "wifi_width_24_1": "40",
        "wifi_iface_2": "wlx98254a16c878",
        "wifi_enabled_2": "on",
        "wifi_band_2": "5",
        "wifi_channel_5_2": "36",
        "wifi_width_5_2": "80",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.wifi.enabled is True
    assert cfg.wifi.ssid == "Bassie"
    assert cfg.wifi.country == "BE"
    assert len(cfg.wifi.aps) == 2
    assert cfg.wifi.aps[0].interface == "wlx98254a16b8da"
    assert cfg.wifi.aps[0].enabled is True
    assert cfg.wifi.aps[0].width == "40"
    assert cfg.wifi.aps[1].band == "5"
    assert cfg.wifi.aps[1].channel == 36
    assert cfg.wifi.aps[1].width == "80"


def test_gui_wifi_page_uses_one_shared_network_name(tmp_path, monkeypatch):
    from bassierouter import services

    monkeypatch.setattr(services, "detect_wireless_interfaces", lambda: [])
    client, _ = make_client(tmp_path)
    page = client.get("/wifi").data
    assert b">Wifi</a>" in page
    assert b'name="wifi_enabled"' in page
    assert b'name="wifi_ssid"' in page
    assert b'name="wifi_band_1"' not in page  # geen radio's in de testomgeving
    assert b'Geen draadloze NIC gevonden.' in page


def test_gui_wifi_page_has_channel_and_width_dropdowns(tmp_path, monkeypatch):
    from bassierouter import services

    monkeypatch.setattr(
        services, "detect_wireless_interfaces",
        lambda: ["wlx98254a16b8da", "wlx98254a16c878"],
    )
    monkeypatch.setattr(services, "wifi_bands", lambda iface: [])
    client, _ = make_client(tmp_path)
    page = client.get("/wifi").data
    assert b'name="wifi_enabled_1"' in page
    assert b'name="wifi_enabled_2"' in page
    assert b'name="wifi_channel_24_1"' in page
    assert b'name="wifi_channel_5_2"' in page
    assert b'name="wifi_width_24_1"' in page
    assert b'name="wifi_width_5_2"' in page
    assert b'0 = autokanaal' in page
    assert b'De radio komt op de bridge' in page


def test_gui_pppoe_form_allows_zero_idle_seconds(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/pppoe", data={
        "csrf_token": "testtoken",
        "client_enabled": "on",
        "client_username": "provider-user",
        "client_password": "secret",
        "client_mtu": "1492",
        "client_demand": "on",
        "client_idle_seconds": "0",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.pppoe_client.demand is True
    assert cfg.pppoe_client.idle_seconds == 0


def test_gui_pppoe_off_keeps_vlan_value_but_not_active(tmp_path):
    client, cfg_file = make_client(tmp_path)
    cfg = load_config(cfg_file)
    cfg.interfaces.wan = "enp1s0"
    cfg.interfaces.wan_vlan_id = 10
    save_config(cfg, cfg_file)

    response = client.post("/pppoe", data={
        "csrf_token": "testtoken",
        "wan": "enp1s0",
        "wan_vlan_id": "10",
        "wan_nic_mode": "dhcp",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.pppoe_client.enabled is False
    assert cfg.interfaces.wan_vlan_id == 10
    assert cfg.active_vlan_id is None
    assert cfg.active_wan_iface == "enp1s0"
    assert "type vlan" not in render_links_script(cfg)
    assert 'VLAN_ID=""' in render_links_script(cfg)


def test_gui_vlan_zero_clears_old_vlan(tmp_path):
    client, cfg_file = make_client(tmp_path)
    cfg = load_config(cfg_file)
    cfg.interfaces.wan = "enp1s0"
    cfg.interfaces.wan_vlan_id = 10
    save_config(cfg, cfg_file)

    response = client.post("/pppoe", data={
        "csrf_token": "testtoken",
        "wan": "enp1s0",
        "wan_vlan_id": "0",
        "wan_nic_mode": "dhcp",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.interfaces.wan_vlan_id is None
    assert cfg.interfaces.wan_iface == "enp1s0"


def test_gui_network_form_sets_lan_bind(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/network", data={
        "csrf_token": "testtoken",
        "wan": "eth0",
        "lan_ports": "eth1",
        "lan_bridge": "br0",
        "lan_ip": "192.168.1.1",
        "lan_prefix": "24",
        "dhcp_start": "192.168.1.100",
        "dhcp_end": "192.168.1.250",
        "dhcp_lease": "12h",
        "dns_domain": "lan",
        "dns_upstream": "1.1.1.1",
        "gui_bind": "192.168.1.1",
        "gui_port": "8080",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.gui.bind == "192.168.1.1"
    assert cfg.interfaces.lan_ip == "192.168.1.1"
    assert cfg.interfaces.dhcp_enabled is False


def test_gui_network_form_sets_hostname(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/network", data={
        "csrf_token": "testtoken",
        "hostname": "bassie-router",
        "wan": "eth0",
        "lan_ports": "eth1",
        "lan_bridge": "br0",
        "lan_ip": "192.168.1.1",
        "lan_prefix": "24",
        "dhcp_start": "192.168.1.100",
        "dhcp_end": "192.168.1.250",
        "dhcp_lease": "12h",
        "dns_domain": "lan",
        "dns_upstream": "1.1.1.1",
        "gui_bind": "192.168.1.1",
        "gui_port": "8080",
    })
    assert response.status_code == 302
    assert load_config(cfg_file).hostname == "bassie-router"


def test_gui_provider_form_sets_wan_port_vlan_and_static_address(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/pppoe", data={
        "csrf_token": "testtoken",
        "wan": "eth0",
        "wan_vlan_id": "6",
        "wan_nic_mode": "static",
        "wan_static_ip": "10.0.0.2",
        "wan_static_prefix": "24",
        "wan_static_gateway": "10.0.0.1",
        "wan_static_dns": "1.1.1.1, 8.8.8.8",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.interfaces.wan == "eth0"
    assert cfg.interfaces.wan_vlan_id == 6
    assert cfg.interfaces.wan_mode == "static"
    assert cfg.interfaces.wan_static_ip == "10.0.0.2"
    assert cfg.interfaces.wan_static_gateway == "10.0.0.1"
    assert cfg.interfaces.wan_static_dns == ["1.1.1.1", "8.8.8.8"]


def test_gui_network_form_can_enable_dhcp_server(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/network", data={
        "csrf_token": "testtoken",
        "wan": "eth0",
        "lan_ports": "eth1",
        "lan_bridge": "br0",
        "lan_ip": "192.168.1.1",
        "lan_prefix": "24",
        "dhcp_enabled": "on",
        "dhcp_start": "192.168.1.100",
        "dhcp_end": "192.168.1.250",
        "dhcp_lease": "12h",
        "dns_domain": "lan",
        "dns_upstream": "1.1.1.1",
        "gui_bind": "192.168.1.1",
        "gui_port": "8080",
    })
    assert response.status_code == 302
    assert load_config(cfg_file).interfaces.dhcp_enabled is True


def test_provider_static_saves_and_applies_without_pppoe_account(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/pppoe", data={
        "csrf_token": "testtoken",
        "wan": "eth0",
        "wan_nic_mode": "static",
        "wan_static_ip": "192.168.1.2",
        "wan_static_prefix": "24",
        "wan_static_gateway": "192.168.1.1",
        "wan_static_dns": "1.1.1.1",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.interfaces.wan_mode == "static"
    assert cfg.pppoe_client.enabled is False
    cfg.bootstrapped = True
    script = render_links_script(cfg)
    assert '$IP addr add "192.168.1.2/24" dev "$WAN_IFACE"' in script
    assert '$IP route replace default via "192.168.1.1" dev "$WAN_IFACE"' in script


def test_provider_pppoe_uses_dhcp_nic_then_ppp0(tmp_path):
    client, cfg_file = make_client(tmp_path)
    response = client.post("/pppoe", data={
        "csrf_token": "testtoken",
        "wan": "eth0",
        "wan_nic_mode": "dhcp",
        "client_enabled": "on",
        "client_username": "user",
        "client_password": "secret",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert cfg.interfaces.wan_mode == "pppoe"
    assert cfg.pppoe_client.enabled is True


def test_form_buttons_only_after_change(tmp_path):
    client, _ = make_client(tmp_path)
    page = client.get("/network").data
    assert b"data-dirty-actions" in page
    assert b"data-form-actions hidden" in page
    assert b"Wijzigingen Opslaan" in page
    assert b">Negeren</button>" in page
    assert b">Toepassen nu</button>" not in page
