import json

from bassierouter.apply import (
    apply_config,
    apply_cpu_profile,
    build_commands,
    build_files,
    config_consistency,
    discard_changes,
    pending_changes,
    rollback,
    service_status,
)
from bassierouter.config import Config, PortForward, WifiAp, load_config, save_config
from bassierouter.runner import CommandResult, FakeRunner


def _dhcp_root(tmp_path) -> str:
    conf = tmp_path / "etc" / "dnsmasq.d" / "bassie-router.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("dhcp-range=192.168.88.10,192.168.88.200,10m\n", encoding="utf-8")
    return str(tmp_path)


def test_service_status_checks_dns_and_dhcp_function(tmp_path):
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    runner = FakeRunner({
        "systemctl is-active dnsmasq.service": CommandResult([], stdout="active\n"),
        "getent ahostsv4 debian.org": CommandResult([], stdout="87.118.104.17\n"),
    })
    services = {s["name"]: s for s in service_status(
        cfg, runner, root=_dhcp_root(tmp_path)
    )}
    assert services["DNS server"]["active"] == "active"
    assert services["DHCP server"]["active"] == "active"


def test_service_status_detects_other_dhcp_server(tmp_path):
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    runner = FakeRunner({
        "systemctl is-active dnsmasq.service": CommandResult([], stdout="active\n"),
        "systemctl is-active bassie-lan-dhcp.service": CommandResult([], stdout="active\n"),
        "getent ahostsv4 debian.org": CommandResult([], stdout="87.118.104.17\n"),
    })
    services = {s["name"]: s for s in service_status(
        cfg, runner, root=_dhcp_root(tmp_path)
    )}
    assert services["DHCP server"]["active"] == "inactive"


def test_dry_run_does_not_write(tmp_path):
    cfg = Config()
    root = str(tmp_path)
    runner = FakeRunner()
    report = apply_config(cfg, root=root, runner=runner, dry_run=True)
    assert report.ok
    assert report.checks == ["nftables-syntax OK", "dry-run: niets gewijzigd"]
    assert any(call[0][0] == "nft" for call in runner.calls)
    assert not (tmp_path / "etc").exists()


def test_wifi_services_follow_enabled_flag():
    cfg = Config()
    for index in (1, 2):
        assert [
            "systemctl", "disable", "--now", f"bassie-wifi-{index}.service"
        ] in build_commands(cfg)
    cfg.wifi.enabled = True
    cfg.wifi.ssid = "Bassie"
    cfg.wifi.password = "wachtwoord123"
    cfg.wifi.aps = [
        WifiAp(interface="wlx98254a16b8da", band="2.4"),
        WifiAp(interface="wlx98254a16c878", band="5", channel=36),
    ]
    commands = build_commands(cfg)
    for index in (1, 2):
        unit = f"bassie-wifi-{index}.service"
        assert ["systemctl", "enable", "--now", unit] in commands
        assert ["systemctl", "restart", unit] in commands
    files = build_files(cfg)
    assert "ssid=Bassie" in files["/etc/hostapd/bassie-router-1.conf"]
    assert "channel=36" in files["/etc/hostapd/bassie-router-2.conf"]
    assert "/etc/systemd/system/bassie-wifi-2.service" in files
    services = {s["name"]: s for s in service_status(cfg, FakeRunner())}
    assert services["Wifi accesspoint"]["needed"] is True


def test_wifi_service_not_needed_when_off():
    services = {s["name"]: s for s in service_status(Config(), FakeRunner())}
    assert services["Wifi accesspoint"]["needed"] is False


def test_pppoe_service_only_enabled_in_pppoe_mode():
    cfg = Config()
    cfg.pppoe_client.enabled = True
    cfg.interfaces.wan_mode = "pppoe"  # stap 2: PPPoE over de WAN-NIC
    assert [
        "systemctl", "enable", "--now", "bassie-pppoe-client.service"
    ] in build_commands(cfg)
    cfg.interfaces.wan_mode = "dhcp"
    assert [
        "systemctl", "disable", "--now", "bassie-pppoe-client.service"
    ] in build_commands(cfg)


def test_apply_does_not_restart_gui_on_every_change():
    commands = build_commands(Config())
    assert not any(
        command[:2] == ["systemctl", "restart"]
        and any("bassie-router.service" in part for part in command)
        for command in commands
    )


def test_gui_bind_change_schedules_delayed_restart(tmp_path):
    cfg = Config()
    cfg.bootstrapped = True
    cfg.firewall.allow_gui_bootstrap = False
    config_file = tmp_path / "config.json"
    root = str(tmp_path)
    save_config(cfg, config_file)
    apply_config(cfg, root=root, runner=FakeRunner(), config_file=str(config_file))

    changed = Config.from_dict(cfg.to_dict())
    changed.gui.bind = "192.168.88.2"
    runner = FakeRunner()
    report = apply_config(
        changed, root=root, runner=runner, config_file=str(config_file)
    )
    assert report.ok, report.errors
    assert any(call[0][0] == "systemd-run" for call in runner.calls)
    assert not any(
        call[0][:2] == ["systemctl", "restart"]
        and any("bassie-router.service" in part for part in call[0])
        for call in runner.calls
    )


def test_lan_dhcp_service_disabled_when_dhcp_server_on():
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    assert [
        "systemctl", "disable", "--now", "bassie-lan-dhcp.service"
    ] in build_commands(cfg)
    files = build_files(cfg, root="/tmp/opencode/fake-root")
    assert "/usr/local/sbin/bassie-lan-dhcp" in files
    assert "/etc/systemd/system/bassie-lan-dhcp.service" in files


def test_cpu_profile_is_applied_and_enabled():
    cfg = Config()
    commands = build_commands(cfg)
    assert ["systemctl", "enable", "--now", "bassie-cpu.service"] in commands

    files = build_files(cfg, root="/tmp/opencode/fake-root")
    assert "/usr/local/sbin/bassie-cpu-profile" in files
    assert "/etc/systemd/system/bassie-cpu.service" in files


def test_apply_installs_grub_factory_boot():
    cfg = Config()
    files = build_files(cfg, root="/tmp/opencode/fake-root")
    assert "/etc/grub.d/40_custom" in files
    assert "bassie_factory=1" in files["/etc/grub.d/40_custom"]
    assert "/usr/local/sbin/bassie-factory-boot" in files
    assert "/etc/systemd/system/bassie-factory-boot.service" in files
    assert ["systemctl", "enable", "--now", "bassie-factory-boot.service"] in build_commands(cfg)
    assert any(
        command[0] == "sh" and "grub-mkconfig" in command[-1]
        for command in build_commands(cfg)
    )


def test_apply_reloads_nftables_after_writing_rules():
    assert ["nft", "-f", "/etc/nftables.conf"] in build_commands(Config())


def test_apply_reloads_links_and_pppoe_after_vlan_change():
    cfg = Config()
    cfg.interfaces.wan = "enp1s0"
    cfg.interfaces.wan_vlan_id = 10
    cfg.interfaces.wan_mode = "pppoe"
    cfg.pppoe_client.enabled = True
    commands = build_commands(cfg)
    assert ["systemctl", "restart", "bassie-links.service"] in commands
    assert ["systemctl", "restart", "bassie-pppoe-client.service"] in commands


def test_config_consistency_compares_config_with_active_state():
    cfg = Config()
    runner = FakeRunner({
        "nft list ruleset": CommandResult(
            [], stdout='comment "DNS alleen via router"'
        ),
        "systemctl is-active bassie-pppoe-client.service": CommandResult(
            [], stdout="inactive"
        ),
    })
    checks = {c["name"]: c for c in config_consistency(cfg, runner)}
    assert checks["DNS van LAN via router"]["ok"] is True
    assert "actief: aan" in checks["DNS van LAN via router"]["detail"]

    cfg.firewall.force_lan_dns_via_router = False
    checks = {c["name"]: c for c in config_consistency(cfg, runner)}
    assert checks["DNS van LAN via router"]["ok"] is False
    assert "config: uit" in checks["DNS van LAN via router"]["detail"]


def test_dnsmasq_waits_for_lan_links():
    files = build_files(Config(), root="/tmp/opencode/fake-root")
    dropin = files["/etc/systemd/system/dnsmasq.service.d/10-bassie.conf"]
    assert "After=bassie-links.service" in dropin
    assert "Wants=bassie-links.service" in dropin


def test_apply_uses_ignore_errors_for_sysctl():
    commands = build_commands(Config())
    assert ["sysctl", "-e", "-p", "/etc/sysctl.d/99-bassie-router.conf"] in commands
    files = build_files(Config(), root="/tmp/opencode/fake-root")
    assert "/usr/local/sbin/bassie-wait-lan-ip" not in files


def test_apply_writes_dynamic_motd_ip_script(tmp_path):
    files = build_files(Config(), root=str(tmp_path))
    assert "/etc/update-motd.d/40-bassie-router-ip" in files
    assert "ip -4 -o addr show dev switch" in files["/etc/update-motd.d/40-bassie-router-ip"]
    assert "http://192.168.88.1:8080" not in files["/etc/motd"]


def test_apply_cpu_profile_writes_and_activates(tmp_path):
    cfg = Config()
    runner = FakeRunner()
    report = apply_cpu_profile(cfg, runner, root=str(tmp_path))
    assert report.ok
    script = tmp_path / "usr/local/sbin/bassie-cpu-profile"
    assert script.exists()
    assert script.stat().st_mode & 0o777 == 0o755
    calls = [call[0] for call in runner.calls]
    assert ["systemctl", "enable", "--now", "bassie-cpu.service"] in calls
    assert ["systemctl", "restart", "bassie-cpu.service"] in calls


def test_apply_writes_files_and_backup_then_rollback(tmp_path):
    cfg = Config()
    cfg.firewall.forwards.append(
        PortForward(name="web", protocol="tcp", external_port=8080,
                    internal_ip="192.168.88.10", internal_port=80)
    )
    cfg.pppoe_client.username = "isp-user"
    root = str(tmp_path)
    runner = FakeRunner()
    report = apply_config(cfg, root=root, runner=runner)
    assert report.ok, report.errors
    assert report.backup
    nft_file = tmp_path / "etc/nftables.conf"
    assert "dnat to 192.168.88.10:80" in nft_file.read_text()
    assert (tmp_path / "usr/local/sbin/bassie-setup-links").stat().st_mode & 0o777 == 0o755
    manifest_path = tmp_path / report.backup.lstrip("/")
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert any(f["path"] == "/etc/nftables.conf" and f["content"] is None for f in manifest["files"])
    saved = json.loads((tmp_path / "etc/bassie-router/config.json").read_text())
    assert saved["bootstrapped"] is True
    assert saved["gui"]["bind"] == "192.168.88.1"
    assert saved["hostname"] == "bassie-router"
    assert saved["firewall"]["allow_gui_bootstrap"] is False
    assert (tmp_path / "etc/hostname").read_text() == "bassie-router\n"
    motd = (tmp_path / "etc/motd").read_text()
    assert "Bassie-Router v" in motd
    assert "http://" not in motd
    assert "http://$IP:8080" in (
        tmp_path / "etc/update-motd.d/40-bassie-router-ip"
    ).read_text()
    assert "Big Pickle" in motd
    assert "https://opencode.ai" in motd
    assert any(call[0][0] == "hostname" for call in runner.calls)

    restored = rollback(root=root, runner=runner)
    assert restored.ok
    assert not nft_file.exists()


def test_pending_changes_and_discard(tmp_path):
    cfg = Config()
    root = str(tmp_path)
    runner = FakeRunner()
    config_file = tmp_path / "etc/bassie-router/config.json"

    assert pending_changes(cfg, root=root) is True
    assert apply_config(cfg, root=root, runner=runner).ok
    applied = load_config(config_file)
    assert applied.bootstrapped is True
    assert pending_changes(applied, root=root) is False

    applied.firewall.udp_nat_timeout = 15
    save_config(applied, config_file)
    assert pending_changes(load_config(config_file), root=root) is True

    report = discard_changes(root=root)
    assert report.ok, report.errors
    restored = load_config(config_file)
    assert restored.firewall.udp_nat_timeout == 30
    assert pending_changes(restored, root=root) is False


def test_invalid_config_is_rejected(tmp_path):
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    cfg.interfaces.dhcp_start = "172.16.0.2"
    report = apply_config(cfg, root=str(tmp_path), runner=FakeRunner())
    assert not report.ok
    assert any("DHCP-start" in e for e in report.errors)
    assert not (tmp_path / "etc").exists()


def test_apply_keeps_current_gui_bind_when_dhcp_server_off(tmp_path):
    cfg = Config()
    cfg.gui.bind = "192.168.1.24"
    cfg.interfaces.lan_ip = "192.168.88.1"
    report = apply_config(cfg, root=str(tmp_path), runner=FakeRunner())
    assert report.ok, report.errors
    saved = json.loads((tmp_path / "etc/bassie-router/config.json").read_text())
    assert saved["gui"]["bind"] == "192.168.1.24"
    links = (tmp_path / "usr/local/sbin/bassie-setup-links").read_text()
    assert "bassie-lan-dhcp.service" in links
    assert "addr add" not in links
    assert (tmp_path / "usr/local/sbin/bassie-lan-dhcp").exists()
    assert ["systemctl", "enable", "--now", "bassie-lan-dhcp.service"] in build_commands(cfg)


def test_apply_moves_gui_bind_when_dhcp_server_on(tmp_path):
    cfg = Config()
    cfg.gui.bind = "192.168.1.24"
    cfg.interfaces.dhcp_enabled = True
    report = apply_config(cfg, root=str(tmp_path), runner=FakeRunner())
    assert report.ok, report.errors
    saved = json.loads((tmp_path / "etc/bassie-router/config.json").read_text())
    assert saved["gui"]["bind"] == "192.168.88.1"
    assert saved["bootstrapped"] is True


def test_nft_syntax_failure_blocks_apply(tmp_path):
    runner = FakeRunner({"nft -c -f -": CommandResult(["nft"], returncode=1, stderr="syntax error")})
    report = apply_config(Config(), root=str(tmp_path), runner=runner)
    assert not report.ok
    assert "syntaxcheck" in report.errors[0]
    assert not (tmp_path / "etc").exists()
