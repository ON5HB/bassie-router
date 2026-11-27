import time

from bassierouter.config import Config, DhcpReservation, load_config, save_config, validate
from bassierouter.dhcp import parse_leases, read_wifi_macs
from bassierouter.runner import CommandResult, FakeRunner
from bassierouter.services import render_dnsmasq
from bassierouter.web import create_app

LEASES = (
    "1764050000 aa:bb:cc:dd:ee:01 192.168.88.101 printer 01:aa:bb:cc:dd:ee:01\n"
    "1764050100 aa:bb:cc:dd:ee:02 192.168.88.102 telefoon *\n"
)


def _client(tmp_path, *, dhcp_enabled=True):
    cfg_file = tmp_path / "config.json"
    cfg = Config()
    cfg.interfaces.dhcp_enabled = dhcp_enabled
    save_config(cfg, cfg_file)
    runner = FakeRunner({
        "cat /var/lib/misc/dnsmasq.leases": CommandResult([], stdout=LEASES)
    })
    app = create_app(
        config_file=str(cfg_file), runner=runner, root=str(tmp_path)
    )
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = "testtoken"
        sess["user"] = "root"
        sess["login_at"] = time.time()
    return client, cfg_file


def test_parse_leases_reads_mac_ip_and_name():
    leases = parse_leases(LEASES)
    assert len(leases) == 2
    assert leases[0].mac == "aa:bb:cc:dd:ee:01"
    assert leases[0].ip == "192.168.88.101"
    assert leases[0].name == "printer"
    assert leases[1].name == "telefoon"
    assert leases[0].pin_name == "printer"
    assert parse_leases(
        "1764050000 aa:bb:cc:dd:ee:09 192.168.88.109 HPLaserjet56712367.local *\n"
    )[0].pin_name == "HPLaserjet56712367"


def test_wifi_macs_come_from_station_dump():
    runner = FakeRunner({
        "iw dev wlan0 station dump": CommandResult(
            [], stdout="Station aa:bb:cc:dd:ee:01 (on wlan0)\n\tsignal: -50 dBm\n"
        )
    })
    macs, ok = read_wifi_macs(runner, ["wlan0"])
    assert ok is True
    assert macs == {"aa:bb:cc:dd:ee:01"}
    assert read_wifi_macs(FakeRunner(), []) == (set(), True)


def test_dhcp_page_marks_wifi_and_cable_clients(tmp_path, monkeypatch):
    from bassierouter import web

    monkeypatch.setattr(web, "detect_wireless_interfaces", lambda: ["wlan0"])
    cfg_file = tmp_path / "config.json"
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    save_config(cfg, cfg_file)
    runner = FakeRunner({
        "cat /var/lib/misc/dnsmasq.leases": CommandResult([], stdout=LEASES),
        "iw dev wlan0 station dump": CommandResult(
            [], stdout="Station aa:bb:cc:dd:ee:01 (on wlan0)\n"
        ),
    })
    app = create_app(
        config_file=str(cfg_file), runner=runner, root=str(tmp_path)
    )
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = "testtoken"
        sess["user"] = "root"
        sess["login_at"] = time.time()
    body = client.get("/dhcp").data
    assert b"<th>Via</th>" in body
    assert b">Wifi</td>" in body
    assert b">Kabel</td>" in body


def test_reservation_validation_and_dnsmasq_rendering():
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    cfg.interfaces.dhcp_reservations.append(DhcpReservation(
        mac="AA:BB:CC:DD:EE:01", ip="192.168.88.50", name="printer",
    ))
    assert validate(cfg) == []
    text = render_dnsmasq(cfg)
    assert "dhcp-host=aa:bb:cc:dd:ee:01,192.168.88.50,printer" in text
    assert f"local=/{cfg.interfaces.dns_domain}/" in text
    assert ",infinite" not in text

    cfg.interfaces.dhcp_reservations.append(DhcpReservation(
        mac="aa:bb:cc:dd:ee:01", ip="192.168.88.51"
    ))
    assert any("tweemaal" in error for error in validate(cfg))

    bad = Config()
    bad.interfaces.dhcp_reservations.append(DhcpReservation(
        mac="niet-een-mac", ip="10.0.0.5"
    ))
    errors = validate(bad)
    assert any("MAC-adres" in error for error in errors)
    assert any("ligt niet in" in error for error in errors)


def test_dhcp_page_lists_leases_and_server_state(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/dhcp").data
    assert b"aa:bb:cc:dd:ee:01" in body
    assert b"192.168.88.101" in body
    assert b"printer" in body
    assert b"Vastzetten" in body
    assert b"lease die niet verloopt" in body
    assert b"DHCP-server op LAN:" in body
    assert b"AAN" in body
    assert b'href="/firewall"' in body
    assert b'href="/dhcp"' in body
    assert body.index(b'href="/firewall"') < body.index(b'href="/dhcp"') < body.index(b'href="/system"')


def test_dhcp_page_can_pin_lease(tmp_path):
    client, cfg_file = _client(tmp_path)
    response = client.post("/dhcp", data={
        "csrf_token": "testtoken",
        "action": "add",
        "mac": "aa:bb:cc:dd:ee:01",
        "ip": "192.168.88.101",
        "name": "printer",
    })
    assert response.status_code == 302
    cfg = load_config(cfg_file)
    assert len(cfg.interfaces.dhcp_reservations) == 1
    reservation = cfg.interfaces.dhcp_reservations[0]
    assert (reservation.mac, reservation.ip, reservation.name) == (
        "aa:bb:cc:dd:ee:01", "192.168.88.101", "printer"
    )
    assert "dhcp-host=aa:bb:cc:dd:ee:01,192.168.88.101,printer" in render_dnsmasq(cfg)


def test_pinning_a_lease_can_use_a_chosen_ip(tmp_path):
    client, cfg_file = _client(tmp_path)
    response = client.post("/dhcp", data={
        "csrf_token": "testtoken",
        "action": "add",
        "mac": "aa:bb:cc:dd:ee:02",
        "ip": "192.168.88.77",
        "name": "telefoon",
    })
    assert response.status_code == 302
    reservation = load_config(cfg_file).interfaces.dhcp_reservations[0]
    assert reservation.ip == "192.168.88.77"


def test_reservation_name_is_stored_without_extension(tmp_path):
    client, cfg_file = _client(tmp_path)
    response = client.post("/dhcp", data={
        "csrf_token": "testtoken",
        "action": "add",
        "mac": "aa:bb:cc:dd:ee:09",
        "ip": "192.168.88.109",
        "name": "HPLaserjet56712367.local",
    })
    assert response.status_code == 302
    assert load_config(cfg_file).interfaces.dhcp_reservations[0].name == (
        "HPLaserjet56712367"
    )


def test_reservation_name_can_be_renamed(tmp_path):
    client, cfg_file = _client(tmp_path)
    cfg = load_config(cfg_file)
    cfg.interfaces.dhcp_reservations.append(DhcpReservation(
        mac="aa:bb:cc:dd:ee:01", ip="192.168.88.101", name="HPLaserjet56712367"
    ))
    save_config(cfg, cfg_file)

    response = client.post("/dhcp", data={
        "csrf_token": "testtoken",
        "action": "rename",
        "index": "0",
        "name": "Printer.local",
    })
    assert response.status_code == 302
    reservation = load_config(cfg_file).interfaces.dhcp_reservations[0]
    assert reservation.name == "Printer"
    assert "dhcp-host=aa:bb:cc:dd:ee:01,192.168.88.101,Printer" in render_dnsmasq(
        load_config(cfg_file)
    )


def test_dhcp_page_can_remove_reservation(tmp_path):
    client, cfg_file = _client(tmp_path)
    cfg = load_config(cfg_file)
    cfg.interfaces.dhcp_reservations.append(DhcpReservation(
        mac="aa:bb:cc:dd:ee:01", ip="192.168.88.101"
    ))
    save_config(cfg, cfg_file)

    response = client.post("/dhcp", data={
        "csrf_token": "testtoken",
        "action": "remove",
        "index": "0",
    })
    assert response.status_code == 302
    assert load_config(cfg_file).interfaces.dhcp_reservations == []


def test_dhcp_page_shows_dhcp_server_active(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/dhcp").data
    assert b"DHCP-server op LAN:" in body
    assert b"<code>switch</code>" in body
