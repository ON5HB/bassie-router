import subprocess

from bassierouter.config import Config, PortForward
from bassierouter.nft import render_nft
from bassierouter.services import (
    BEGIN,
    END,
    merge_managed_file,
    render_cpu_script,
    render_factory_boot_script,
    render_grub_factory,
    render_dnsmasq,
    render_hostname,
    render_hosts,
    render_lan_dhcp_script,
    render_links_script,
    render_motd,
    render_motd_ip_script,
    render_client_options,
    render_pppoe_check_script,
    render_sysctl,
    render_systemd_units,
)


def test_nft_is_ipv4_only_and_wan_closed():
    rules = render_nft(Config())
    assert "table ip bassie" in rules
    assert "table inet" not in rules
    assert "table ip6" not in rules
    assert "policy drop;" in rules
    assert "oifname $WAN masquerade" in rules
    assert 'oifname "ppp+" masquerade comment "NAT naar WAN/PPPoE"' in rules
    assert "tcp option maxseg size set rt mtu" in rules
    assert 'oifname "ppp+" tcp flags syn tcp option maxseg size set rt mtu' in rules
    assert "iifname $LAN oifname $WAN accept" in rules
    assert 'iifname $LAN oifname "ppp+" accept' in rules
    assert 'iifname $LAN oifname $WAN udp dport 53 drop comment "DNS alleen via router"' in rules
    assert 'iifname $LAN oifname $WAN tcp dport 53 drop comment "DNS alleen via router"' in rules


def test_nft_dns_enforcement_can_be_disabled():
    cfg = Config()
    cfg.firewall.force_lan_dns_via_router = False
    rules = render_nft(cfg)
    assert "DNS alleen via router" not in rules
    assert "iifname $LAN oifname $WAN accept" in rules


def test_nft_portforward_tcp_and_udp():
    cfg = Config()
    cfg.firewall.forwards = [
        PortForward(name="web", protocol="tcp", external_port=8080,
                    internal_ip="192.168.88.10", internal_port=80),
        PortForward(name="dns", protocol="udp", external_port=53,
                    internal_ip="192.168.88.20", internal_port=53),
    ]
    rules = render_nft(cfg)
    assert 'tcp dport 8080 dnat to 192.168.88.10:80' in rules
    assert 'udp dport 53 dnat to 192.168.88.20:53' in rules
    # Twee regels per forward: fysieke WAN + PPPoE (ppp+).
    assert rules.count('comment "web"') == 4


def test_nft_escapes_forward_names():
    cfg = Config()
    cfg.firewall.forwards = [PortForward(
        name='web "special"', protocol="tcp", external_port=80,
        internal_ip="192.168.88.10", internal_port=80)]
    rules = render_nft(cfg)
    assert 'web \\"special\\"' in rules


def test_per_forward_udp_timeout():
    cfg = Config()
    cfg.firewall.forwards.append(
        PortForward(name="ntp", protocol="udp", external_port=123,
                    internal_ip="192.168.88.30", internal_port=123, udp_timeout=1)
    )
    rules = render_nft(cfg)
    assert 'ct timeout fwd_0 {' in rules
    assert 'policy = { established: 1 }' in rules
    assert 'ct timeout set "fwd_0"' in rules


def test_no_track_udp_ports():
    cfg = Config()
    cfg.firewall.no_track_udp_ports = [123]
    rules = render_nft(cfg)
    assert "define NO_TRACK = { 123 }" in rules
    assert "udp dport $NO_TRACK notrack" in rules
    assert "udp sport $NO_TRACK notrack" in rules
    assert "iifname $WAN udp sport $NO_TRACK accept" in rules
    assert "chain output" in rules


def test_sysctl_ipv6_off_and_udp_timeout():
    cfg = Config()
    text = render_sysctl(cfg)
    assert "net.ipv4.ip_forward=1" in text
    assert "net.ipv6.conf.all.disable_ipv6=1" in text
    assert "net.netfilter.nf_conntrack_udp_timeout=30" in text
    cfg.firewall.udp_nat_timeout = 0
    assert "nf_conntrack_udp_timeout" not in render_sysctl(cfg)


def test_dnsmasq_dhcp_is_off_by_default():
    text = render_dnsmasq(Config())
    assert "interface=switch" in text
    assert "bind-dynamic" in text
    assert "no-resolv" in text
    assert "dhcp-range=" not in text
    assert "listen-address=" not in text
    assert "dhcp-option=3," not in text
    assert "filter-AAAA" in text
    assert f"local=/{Config().interfaces.dns_domain}/" in text


def test_dnsmasq_ipv4_dhcp_when_enabled():
    cfg = Config()
    cfg.interfaces.dhcp_enabled = True
    text = render_dnsmasq(cfg)
    assert "listen-address=192.168.88.1" in text
    assert "dhcp-range=192.168.88.100,192.168.88.250,12h" in text
    assert "dhcp-option=3,192.168.88.1" in text
    assert "dhcp-option=6,192.168.88.1" in text


def test_links_script_handles_wan_vlan_and_multiple_lan_ports():
    cfg = Config()
    cfg.interfaces.wan_vlan_id = 6
    cfg.interfaces.lan_ports = ["eth1", "eth2", "eth3"]
    text = render_links_script(cfg)
    assert 'link add link "$WAN" name "eth0.6" type vlan id 6' in text
    assert '$IP link set dev "eth0.6" up' in text
    assert 'PORTS="eth1 eth2 eth3"' in text
    assert '$IP addr flush dev "$port"' in text
    assert "type bridge" in text
    assert "vlan_filtering 0" in text


def test_links_script_without_vlan():
    text = render_links_script(Config())
    assert "type vlan" not in text
    assert "eth0.6" not in text
    assert "Oude WAN-VLAN-subinterfaces opruimen" in text
    assert '$IP link del "$WAN.$vid"' in text
    result = subprocess.run(["sh", "-n"], input=text, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_links_script_bootstrap_uses_dhcp_on_bridge():
    text = render_links_script(Config())
    assert 'BOOTSTRAP="1"' in text
    assert "bassie-lan-dhcp.service" in text
    assert "timeout 10 udevadm settle" in text
    assert "modprobe bridge" in text
    assert "\nset -eu\n" not in text
    assert "\nset -u\n" in text
    assert '$IP addr flush dev "$BR"' in text
    assert '$IP -6 addr flush dev "$BR"' in text
    assert "addr add" not in text
    assert "nomaster" not in text


def test_links_script_final_detaches_unselected_ports():
    cfg = Config()
    cfg.bootstrapped = True
    cfg.interfaces.dhcp_enabled = True
    cfg.interfaces.lan_ports = ["eth1", "eth2"]
    text = render_links_script(cfg)
    assert 'BOOTSTRAP="0"' in text
    assert "nomaster" in text
    assert '$IP addr add "${LAN_IP}/${PREFIX}" dev "$BR"' in text


def test_links_script_stays_dhcp_client_when_dhcp_server_off():
    cfg = Config()
    cfg.bootstrapped = True
    text = render_links_script(cfg)
    assert "bassie-lan-dhcp.service" in text
    assert "addr add" not in text


def test_links_script_wan_address_modes():
    cfg = Config()
    cfg.bootstrapped = True
    cfg.interfaces.wan_mode = "dhcp"
    text = render_links_script(cfg)
    assert 'dhclient -4 -1 "$WAN_IFACE"' in text
    assert '$IP route del default dev "$BR"' in text

    cfg.interfaces.wan_mode = "static"
    cfg.interfaces.wan_static_ip = "10.0.0.2"
    cfg.interfaces.wan_static_gateway = "10.0.0.1"
    cfg.interfaces.wan_static_dns = ["1.1.1.1"]
    text = render_links_script(cfg)
    assert '$IP addr add "10.0.0.2/24" dev "$WAN_IFACE"' in text
    assert '$IP route replace default via "10.0.0.1" dev "$WAN_IFACE"' in text
    assert "nameserver 1.1.1.1" in text
    assert 'dhclient -4 -1 "$WAN_IFACE"' not in text

    cfg.interfaces.wan_mode = "pppoe"
    text = render_links_script(cfg)
    # PPPoE met beheeradres: NIC krijgt het IP en een route naar de modem,
    # maar geen default route (ppp0 blijft de uplink).
    assert '$IP addr add "10.0.0.2/24" dev "$WAN_IFACE"' in text
    assert '$IP route replace "10.0.0.1/32" dev "$WAN_IFACE"' in text
    assert '/run/bassie-router/modem-ip' in text
    assert "$IP route replace default" not in text
    cfg.interfaces.wan_static_ip = ""
    cfg.interfaces.wan_static_gateway = ""
    text = render_links_script(cfg)
    # PPPoE met DHCP op de NIC: modem geeft eerst een adres, ppp0 doet de rest.
    assert '/usr/sbin/dhclient -4 -1 "$WAN_IFACE"' in text
    assert '/run/bassie-router/modem-ip' in text
    assert '$IP route del default dev "$WAN_IFACE"' in text
    assert "$IP route replace default" not in text
    assert "$IP addr add" not in text

    # Alle drie de varianten moeten geldige /bin/sh-scripts zijn.
    for mode in ("pppoe", "dhcp", "static"):
        cfg.interfaces.wan_mode = mode
        result = subprocess.run(
            ["sh", "-n"], input=render_links_script(cfg), text=True, capture_output=True
        )
        assert result.returncode == 0, f"{mode}: {result.stderr}"


def test_grub_factory_entry_is_kernel_update_safe(monkeypatch):
    monkeypatch.setattr(
        "bassierouter.services._grub_boot_info",
        lambda: {
            "search_uuid": "root-uuid",
            "root_uuid": "root-uuid",
            "separate": False,
            "kernel": "/vmlinuz",
            "initrd": "/initrd.img",
        },
    )
    text = render_grub_factory("# eigen regels\n")
    assert "bassie_factory=1" in text
    assert "menuentry 'Bassie-Router: verse installatie (factory reset)'" in text
    assert "# eigen regels" in text
    # /vmlinuz en /initrd.img zijn symlinks:/kernelupdates volgen zichzelf.
    assert "/vmlinuz" in text
    assert "/initrd.img" in text
    assert "vmlinuz-" not in text.split("menuentry", 1)[1]


def test_factory_boot_script_clears_configs_and_restores_defaults():
    text = render_factory_boot_script(Config())
    assert "grep -qw 'bassie_factory=1' /proc/cmdline" in text
    assert "rm -f /etc/bassie-router/config.json" in text
    assert "cfg.bootstrapped = False" in text
    assert 'cfg.interfaces.wan = "kies-poort"' in text
    assert "cfg.gui.bind = \"127.0.0.1\"" in text
    assert "systemctl enable --now bassie-links.service bassie-lan-dhcp.service" in text
    result = subprocess.run(["sh", "-n"], input=text, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    unit = render_systemd_units(Config())["/etc/systemd/system/bassie-factory-boot.service"]
    assert "Before=bassie-links.service" in unit
    assert "ExecStart=/usr/local/sbin/bassie-factory-boot" in unit


def test_lan_dhcp_client_keeps_retrying():
    cfg = Config()
    cfg.interfaces.lan_bridge = "br0"
    text = render_lan_dhcp_script(cfg)
    assert "/usr/sbin/dhclient -4 -d" in text
    # Standaard Debian-pad: ook met AppArmor mag dhclient hier schrijven.
    assert 'LEASE=/var/lib/dhcp/dhclient-bassie-lan.leases' in text
    assert 'mkdir -p /var/lib/dhcp' in text
    assert 'chmod 0755 /var/lib/dhcp' in text
    assert '[ -f "$LEASE" ] || : > "$LEASE"' in text
    assert 'chmod 0644 "$LEASE"' in text
    assert '-lf "$LEASE"' in text
    assert "br0" in text
    unit = render_systemd_units(cfg)["/etc/systemd/system/bassie-lan-dhcp.service"]
    assert "Restart=always" in unit
    assert "After=bassie-links.service" in unit
    # Geen ExecStartPre: die wachtte op een IP dat alleen deze service zelf
    # moest halen, en veroorzaakte daarmee een deadlock bij boot.
    assert "ExecStartPre" not in unit
    gui = render_systemd_units(cfg)["/etc/systemd/system/bassie-router.service"]
    assert "After=network-online.target bassie-links.service bassie-lan-dhcp.service" in gui
    result = subprocess.run(["sh", "-n"], input=text, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_pppoe_options_use_nic_prefix_for_vlan_interface():
    cfg = Config()
    cfg.interfaces.wan = "enp1s0"
    cfg.interfaces.wan_vlan_id = 10
    text = render_client_options(cfg)
    assert "plugin rp-pppoe.so" in text
    assert "nic-enp1s0.10" in text
    assert "\nenp1s0.10\n" not in text


def test_pppoe_check_pings_modem_before_starting_pppd():
    cfg = Config()
    cfg.interfaces.wan_static_gateway = "192.168.1.1"
    text = render_pppoe_check_script(cfg)
    assert 'MODEM="192.168.1.1"' in text
    assert '/run/bassie-router/modem-ip' in text
    assert '"$PING" -4 -c 1 -W 1 -q -n "$MODEM"' in text
    assert "PPPoE start toch" in text
    assert "exit 0" in text
    result = subprocess.run(["sh", "-n"], input=text, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    unit = render_systemd_units(cfg)["/etc/systemd/system/bassie-pppoe-client.service"]
    assert "ExecStartPre=/usr/local/sbin/bassie-pppoe-check" in unit


def test_cpu_script_uses_cpupower_and_default_profile():
    cfg = Config()
    text = render_cpu_script(cfg)
    assert "GOV=powersave" in text
    assert '$CPUPOWER frequency-set --governor "$GOV"' in text
    assert 'MIN="800"' in text
    assert 'MAX="1600"' in text
    assert '$CPUFREQSET -d "${MIN}MHz"' in text
    assert "/sys/devices/system/cpu/cpu*/cpufreq" in text
    assert "exit 0" not in text  # sysfs-fallback mag niet worden overgeslagen
    result = subprocess.run(["sh", "-n"], input=text, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr

    cfg.system.cpu_min_mhz = None
    cfg.system.cpu_max_mhz = None
    text = render_cpu_script(cfg)
    assert 'MIN=""' in text
    assert 'MAX=""' in text


def test_cpu_systemd_unit_is_rendered():
    units = render_systemd_units(Config())
    assert "/etc/systemd/system/bassie-cpu.service" in units
    assert "bassie-cpu-profile" in units["/etc/systemd/system/bassie-cpu.service"]


def test_nft_bootstrap_gui_rule_disappears_after_setup():
    cfg = Config()
    assert "GUI (initiele setup)" in render_nft(cfg)
    cfg.bootstrapped = True
    cfg.firewall.allow_gui_bootstrap = False
    assert "GUI (initiele setup)" not in render_nft(cfg)


def test_secrets_merge_keeps_unrelated_lines():
    existing = '"other" * "keepme" *\n'
    block = f'{BEGIN}\n"bob" * "secret" *\n{END}\n'
    merged = merge_managed_file(existing, block)
    assert '"other" * "keepme" *' in merged
    assert merged.count(BEGIN) == 1
    updated = merge_managed_file(merged, f'{BEGIN}\n"alice" * "x" *\n{END}\n')
    assert "bob" not in updated
    assert '"other" * "keepme" *' in updated


def test_hostname_and_hosts_are_rendered():
    cfg = Config()
    assert render_hostname(cfg) == "bassie-router\n"
    hosts = render_hosts(cfg, "127.0.0.1 localhost\n127.0.1.1 debian\n", "debian")
    assert "127.0.1.1 bassie-router" in hosts
    assert "debian" not in hosts
    cfg.hostname = "router2"
    hosts = render_hosts(cfg, "127.0.0.1 localhost\n127.0.1.1 bassie-router\n", "bassie-router")
    assert "127.0.1.1 router2" in hosts


def test_motd_has_credits_but_no_stale_ip():
    motd = render_motd(Config())
    assert "Bassie-Router v" in motd
    assert "http://" not in motd
    assert "Big Pickle" in motd
    assert "Bas Heijermans (ON5HB, radioamateur)" in motd
    assert "https://opencode.ai" in motd
    assert "MIT" in motd


def test_motd_ip_script_reads_actual_bridge_address():
    cfg = Config()
    cfg.interfaces.lan_bridge = "br0"
    text = render_motd_ip_script(cfg)
    assert "ip -4 -o addr show dev br0 scope global" in text
    assert 'echo "GUI: http://$IP:8080"' in text
    assert "127.0.0.1:8080" in text
    result = subprocess.run(["sh", "-n"], input=text, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_systemd_units_use_root_without_sudo():
    cfg = Config()
    cfg.pppoe_client.enabled = True
    units = render_systemd_units(cfg)
    assert "sudo" not in "".join(units.values())
    assert "/usr/sbin/pppd call bassie-isp" in units["/etc/systemd/system/bassie-pppoe-client.service"]
    assert not any("pppoe-server" in name for name in units)
    assert "Environment=BASSIE_GUI_PAM=1" in units["/etc/systemd/system/bassie-router.service"]
