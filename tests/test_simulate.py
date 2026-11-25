from bassierouter.config import Config
from bassierouter.simulate import report, simulate


def test_nic_only_simulation_uses_dhcp_without_pppoe():
    cfg = Config()
    cfg.interfaces.wan_mode = "pppoe"  # oude/ingestelde waarde
    cfg.pppoe_client.enabled = True
    cfg.interfaces.wan_static_ip = ""

    checks = simulate(cfg, nic_only=True)
    by_name = {c.name: c for c in checks}
    assert by_name["Test zonder PPPoE: WAN-NIC is de uplink"].ok
    assert by_name["WAN-NIC haalt adres via DHCP"].ok
    assert "statisch" not in checks[0].detail
    assert "zonder PPPoE" in report(cfg, nic_only=True)


def test_nic_only_simulation_uses_static_address_when_set():
    cfg = Config()
    cfg.interfaces.wan_mode = "pppoe"
    cfg.interfaces.wan_static_ip = "192.168.1.2"
    cfg.interfaces.wan_static_gateway = "192.168.1.1"

    checks = {c.name: c for c in simulate(cfg, nic_only=True)}
    assert checks["Standaardroute via WAN-NIC"].ok
    assert checks["Test zonder PPPoE: WAN-NIC is de uplink"].detail == "statisch"
