import json

from bassierouter.config import (
    Config,
    PortForward,
    load_config,
    migrate_config,
    save_config,
    validate,
    WifiAp,
)


def test_default_config_is_valid():
    assert validate(Config()) == []


def test_wifi_country_must_be_known_code():
    cfg = Config()
    cfg.wifi.enabled = True
    cfg.wifi.ssid = "Bassie"
    cfg.wifi.password = "wachtwoord123"
    cfg.wifi.country = "ZZ"
    assert "wifi: onbekende landcode" in validate(cfg)
    cfg.wifi.country = "00"
    assert validate(cfg) == []


def test_wan_vlan_derived_name():
    cfg = Config()
    cfg.interfaces.wan_vlan_id = 6
    assert cfg.interfaces.wan_iface == "eth0.6"
    assert cfg.interfaces.lan_iface == "switch"
    # Zonder PPPoE blijft het id bewaard, maar is het niet actief.
    assert cfg.active_wan_iface == "eth0"
    assert cfg.active_vlan_id is None
    assert cfg.pppoe_client.iface(cfg) == "eth0"
    cfg.pppoe_client.enabled = True
    assert cfg.active_wan_iface == "eth0.6"
    assert cfg.active_vlan_id == 6
    assert cfg.pppoe_client.iface(cfg) == "eth0.6"


def test_vlan_zero_means_no_vlan_also_from_old_configs():
    cfg = Config.from_dict({"interfaces": {"wan": "enp1s0", "wan_vlan_id": 0}})
    assert cfg.interfaces.wan_vlan_id is None
    assert cfg.interfaces.wan_iface == "enp1s0"
    assert validate(cfg) == []


def test_lan_has_no_vlan_option():
    assert "lan_vlan_id" not in Config().interfaces.__dict__


def test_wifi_is_off_by_default_and_needs_credentials():
    cfg = Config()
    assert cfg.wifi.enabled is False
    assert cfg.wifi.aps == []
    assert validate(cfg) == []
    cfg.wifi.enabled = True
    cfg.wifi.ssid = "Bassie"
    cfg.wifi.password = "wachtwoord123"
    assert validate(cfg) == []
    cfg.wifi.aps.append(WifiAp(interface="wlx0", band="6", channel=0))
    assert any("wifi: band" in e for e in validate(cfg))
    cfg.wifi.aps[0].band = "5"
    cfg.wifi.aps[0].channel = 11
    assert any("wifi: kanaal voor 5 GHz" in e for e in validate(cfg))


def test_wifi_width_is_per_band():
    cfg = Config()
    cfg.wifi.enabled = True
    cfg.wifi.ssid = "Bassie"
    cfg.wifi.password = "wachtwoord123"
    cfg.wifi.aps = [
        WifiAp(interface="wlx0", band="2.4", channel=11, width="40"),
        WifiAp(interface="wlx1", band="5", channel=36, width="80"),
    ]
    assert validate(cfg) == []
    cfg.wifi.aps[0].width = "80"
    assert any("bandbreedte" in e for e in validate(cfg))
    cfg.wifi.aps[0].width = "40"
    cfg.wifi.aps[1].channel = 100
    assert any("80 MHz" in e for e in validate(cfg))
    cfg.wifi.aps[1].channel = 149
    cfg.wifi.aps[1].width = "160"
    assert validate(cfg) == []

def test_wifi_round_trip_with_two_radios(tmp_path):
    cfg = Config()
    cfg.wifi.enabled = True
    cfg.wifi.ssid = "Bassie"
    cfg.wifi.password = "wachtwoord123"
    cfg.wifi.aps = [
        WifiAp(interface="wlx98254a16b8da", band="2.4", channel=11, width="40"),
        WifiAp(
            interface="wlx98254a16c878", band="5", channel=36, width="80",
            enabled=False,
        ),
    ]
    path = tmp_path / "config.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.wifi.aps[0].band == "2.4"
    assert loaded.wifi.aps[0].width == "40"
    assert loaded.wifi.aps[1].interface == "wlx98254a16c878"
    assert loaded.wifi.aps[1].channel == 36
    assert loaded.wifi.aps[1].width == "80"
    assert loaded.wifi.aps[1].enabled is False
    assert validate(loaded) == []


def test_custom_bridge_name_is_kept_everywhere(tmp_path):
    from bassierouter.services import render_lan_dhcp_script, render_links_script

    cfg = Config.from_dict({"interfaces": {"lan_bridge": "switch"}})
    assert cfg.interfaces.lan_bridge == "switch"
    assert 'BR="switch"' in render_links_script(cfg)
    assert "switch" in render_lan_dhcp_script(cfg)
    path = tmp_path / "config.json"
    save_config(cfg, path)
    assert load_config(path).interfaces.lan_bridge == "switch"
    assert validate(cfg) == []


def test_pppoe_modem_management_address_validates():
    cfg = Config()
    cfg.interfaces.wan_static_ip = "192.168.1.2"
    cfg.interfaces.wan_static_gateway = "192.168.1.1"
    assert validate(cfg) == []
    cfg.interfaces.wan_static_gateway = "10.0.0.1"
    assert any("WAN-gateway" in e for e in validate(cfg))


def test_portforward_model(tmp_path):
    cfg = Config()
    cfg.firewall.forwards.append(
        PortForward(name="web", protocol="tcp", external_port=8080,
                    internal_ip="192.168.88.10", internal_port=80)
    )
    assert validate(cfg) == []
    path = tmp_path / "config.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.firewall.forwards[0].protocol == "tcp"
    assert loaded.firewall.forwards[0].internal_ip == "192.168.88.10"


def test_validation_catches_bad_dhcp_and_credentials():
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    cfg.interfaces.dhcp_start = "10.0.0.5"
    cfg.pppoe_client.enabled = True
    cfg.interfaces.wan_mode = "pppoe"
    errors = validate(cfg)
    assert any("DHCP-start" in e for e in errors)
    assert any("username" in e for e in errors)


def test_bad_dhcp_range_is_ignored_when_dhcp_server_off():
    cfg = Config()
    cfg.interfaces.dhcp_start = "10.0.0.5"
    assert validate(cfg) == []


def test_wan_modes_validate():
    cfg = Config()
    cfg.interfaces.wan_mode = "dhcp"
    assert validate(cfg) == []
    cfg.interfaces.wan_mode = "static"
    assert any("statisch WAN-adres" in e for e in validate(cfg))
    cfg.interfaces.wan_static_ip = "10.0.0.2"
    cfg.interfaces.wan_static_gateway = "10.0.0.1"
    assert validate(cfg) == []
    cfg.interfaces.wan_static_gateway = "192.168.1.1"
    assert any("WAN-gateway" in e for e in validate(cfg))
    cfg.interfaces.wan_mode = "onzin"
    assert any("WAN-modus" in e for e in validate(cfg))


def test_cpu_profile_defaults_and_validation():
    cfg = Config()
    assert cfg.system.cpu_governor == "powersave"
    assert cfg.system.cpu_min_mhz == 800
    assert cfg.system.cpu_max_mhz == 1600
    assert validate(cfg) == []

    cfg.system.cpu_min_mhz = 2500
    assert any("CPU-min" in e for e in validate(cfg))
    cfg.system.cpu_min_mhz = 800
    cfg.system.cpu_max_mhz = 0
    assert any("CPU-max" in e for e in validate(cfg))
    cfg.system.cpu_max_mhz = 1600
    cfg.system.cpu_governor = "onzin"
    assert any("CPU-governor" in e for e in validate(cfg))


def test_hostname_must_be_simple():
    cfg = Config()
    assert validate(cfg) == []
    cfg.hostname = "bassie router"
    assert any("hostname" in e for e in validate(cfg))
    cfg.hostname = "bassie-router"
    assert validate(cfg) == []


def test_gui_bind_cannot_be_wan_or_any():
    cfg = Config()
    cfg.bootstrapped = True
    cfg.interfaces.dhcp_enabled = True
    cfg.firewall.allow_gui_bootstrap = False
    cfg.gui.bind = "0.0.0.0"
    assert any("GUI-bind" in e for e in validate(cfg))
    cfg.gui.bind = "8.8.8.8"
    assert any("GUI-bind" in e for e in validate(cfg))
    cfg.gui.bind = cfg.interfaces.lan_ip
    assert validate(cfg) == []
    cfg.firewall.allow_gui_bootstrap = True
    assert any("initiele setup" in e for e in validate(cfg))


def test_gui_bind_accepts_external_dhcp_address_when_dhcp_server_off():
    cfg = Config()
    cfg.bootstrapped = True
    cfg.firewall.allow_gui_bootstrap = False
    cfg.gui.bind = "192.168.1.24"
    assert validate(cfg) == []


def test_bootstrap_allows_temporary_dhcp_bind_and_no_wan_yet():
    cfg = Config()
    cfg.interfaces.wan = "kies-poort"
    cfg.interfaces.lan_ports = ["enp1s0", "enp2s0"]
    cfg.gui.bind = "192.168.1.24"
    assert validate(cfg) == []


def test_wan_port_cannot_also_be_lan_port():
    cfg = Config()
    cfg.interfaces.lan_ports = ["eth0", "eth1"]
    assert any("WAN-poort" in e for e in validate(cfg))


def test_no_track_cannot_overlap_udp_forward():
    cfg = Config()
    cfg.firewall.no_track_udp_ports = [123]
    cfg.firewall.forwards.append(
        PortForward(name="ntp", protocol="udp", external_port=123,
                    internal_ip="192.168.88.30", internal_port=123)
    )
    assert any("no-track" in e for e in validate(cfg))


def test_no_pppoe_server_in_model():
    assert "pppoe_server" not in Config().__dict__


def test_migrate_preserves_old_values_and_adds_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "version": 1,
        "interfaces": {
            "wan": "enp1s0",
            "lan_ports": ["enp2s0"],
            "lan_ip": "192.168.88.1",
        },
        "gui": {"bind": "192.168.88.1", "port": 8080},
        "firewall": {"wan_input_policy": "drop"},
        "pppoe_server": {"enabled": True},
    }), encoding="utf-8")
    report = migrate_config(path, backup_dir=tmp_path / "backups")
    assert report.errors == []
    assert report.backup is not None and report.backup.exists()
    assert "firewall.allow_gui_bootstrap" in report.added
    assert "pppoe_server.enabled" in report.removed
    cfg = load_config(path)
    assert cfg.interfaces.wan == "enp1s0"
    assert cfg.interfaces.lan_ports == ["enp2s0"]
    assert cfg.firewall.wan_input_policy == "drop"
    assert cfg.bootstrapped is False
    assert cfg.firewall.allow_gui_bootstrap is True
    assert validate(cfg) == []


def test_migrate_legacy_config_starts_in_bootstrap(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "interfaces": {"wan": "eth0", "lan_ports": ["eth1"]},
        "gui": {"bind": "127.0.0.1", "port": 8080},
    }), encoding="utf-8")
    report = migrate_config(path, backup_dir=tmp_path / "backups")
    assert report.errors == []
    cfg = load_config(path)
    assert cfg.bootstrapped is False
    assert cfg.firewall.allow_gui_bootstrap is True
