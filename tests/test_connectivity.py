from bassierouter.config import Config
from bassierouter.connectivity import report, run_checks
from bassierouter.runner import CommandResult, FakeRunner


def _responses() -> dict[str, CommandResult]:
    return {
        "ip -4 -o addr show dev switch scope global": CommandResult(
            [], stdout="3: switch inet 192.168.88.1/24 scope global switch\n"
        ),
        "ip -4 route show default": CommandResult(
            [], stdout="default via 192.168.1.1 dev eth0\n"
        ),
        "ip -br link show dev eth0": CommandResult(
            [], stdout="eth0 UP 00:11:22:33:44:55 <UP,BROADCAST> mtu 1500\n"
        ),
        "ping -4 -c 1 -W 2 -q -n -I 192.168.88.1 192.168.1.1": CommandResult(
            [], stdout="1 packets received, 0% packet loss\n"
        ),
        "ping -4 -c 1 -W 2 -q -n 1.1.1.1": CommandResult(
            [], stdout="1 packets received, 0% packet loss\n"
        ),
        "getent ahostsv4 debian.org": CommandResult([], stdout="151.101.1.1 debian.org\n"),
        "nft list table ip bassie": CommandResult([], stdout="table ip bassie {\n}\n"),
    }


def test_connectivity_checks_are_green_for_plain_router():
    cfg = Config()
    cfg.bootstrapped = True
    cfg.interfaces.wan_mode = "static"
    cfg.interfaces.wan_static_ip = "192.168.1.2"
    cfg.interfaces.wan_static_gateway = "192.168.1.1"

    checks = {c.name: c for c in run_checks(cfg, FakeRunner(_responses()))}
    assert len(checks) == 7
    assert all(c.ok for c in checks.values())
    assert "7/7 checks geslaagd" in report(cfg, FakeRunner(_responses()))


def test_linkdown_default_route_and_wan_link_are_reported():
    cfg = Config()
    cfg.bootstrapped = True
    cfg.interfaces.wan_mode = "static"
    cfg.interfaces.wan_static_gateway = "192.168.1.1"
    responses = _responses()
    responses["ip -4 route show default"] = CommandResult(
        [], stdout="default via 192.168.1.1 dev eth0 linkdown\n"
    )
    responses["ip -br link show dev eth0"] = CommandResult(
        [], stdout="eth0 DOWN 00:11:22:33:44:55 mtu 1500\n"
    )

    checks = {c.name: c for c in run_checks(cfg, FakeRunner(responses))}
    assert checks["Default route via WAN"].ok is False
    assert checks["WAN-link actief"].ok is False


def test_connectivity_reports_unreachable_modem_before_pppoe():
    cfg = Config()
    cfg.bootstrapped = True
    cfg.interfaces.wan_mode = "pppoe"
    cfg.interfaces.wan_static_gateway = "192.168.1.1"
    cfg.pppoe_client.enabled = True
    responses = _responses()
    responses["ping -4 -c 1 -W 2 -q -n 192.168.1.1"] = CommandResult(
        [], returncode=1, stdout=""
    )

    checks = {c.name: c for c in run_checks(cfg, FakeRunner(responses))}
    assert checks["DSL-modem bereikbaar"].ok is False
    assert "192.168.1.1" in checks["DSL-modem bereikbaar"].detail
