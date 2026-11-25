#!/bin/bash
# Simuleert LAN -> WAN op een Debian-machine met network namespaces.
# Vereist root: ip/netns en nftables zijn nodig. Raakt de echte router NIET aan.
#
# Gebruik op de router:
#   bash simulate-routing.sh
# Of met een andere config:
#   CONFIG=/etc/bassie-router/config.json BASSIE_SRC=/opt/bassie-router/src \
#       bash simulate-routing.sh
set -uo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "FOUT: deze simulatie moet als root draaien (network namespaces)." >&2
    exit 1
fi

CONFIG=${CONFIG:-/etc/bassie-router/config.json}
BASSIE_SRC=${BASSIE_SRC:-/opt/bassie-router/src}
NS_LAN=bassie-sim-lan
NS_ROUTER=bassie-sim-router
NS_WAN=bassie-sim-wan
RULES=$(mktemp)
FAILED=0

for command in ip nft ping python3; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "FOUT: '$command' ontbreekt." >&2
        exit 1
    fi
done
if [[ ! -f "$CONFIG" ]]; then
    echo "FOUT: configuratie niet gevonden: $CONFIG" >&2
    exit 1
fi

cleanup() {
    ip link del brsim-a >/dev/null 2>&1 || true
    ip link del brsim-b >/dev/null 2>&1 || true
    for ns in "$NS_LAN" "$NS_ROUTER" "$NS_WAN"; do
        ip netns del "$ns" >/dev/null 2>&1 || true
    done
    rm -f "$RULES"
}
trap cleanup EXIT
cleanup

# Lees precies dezelfde waarden als de GUI gebruikt.
eval "$(
python3 - "$CONFIG" <<'PY'
import ipaddress
import json
import shlex
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    cfg = json.load(handle)
i = cfg.get("interfaces", {})
wan = i.get("wan") or "eth0"
vlan = i.get("wan_vlan_id")
wan_iface = f"{wan}.{vlan}" if vlan else wan
lan_ip = i.get("lan_ip") or "192.168.88.1"
prefix = int(i.get("lan_prefix", 24))
net = ipaddress.ip_network(f"{lan_ip}/{prefix}", strict=False)
client = net.network_address + 10
if str(client) == lan_ip:
    client = net.network_address + 20

def out(key, value):
    print(f"{key}={shlex.quote(str(value))}")

out("BRIDGE", i.get("lan_bridge") or "switch")
out("LAN_IP", lan_ip)
out("PREFIX", prefix)
out("LAN_CLIENT", client)
out("LAN_NET", net)
out("WAN_IFACE", wan_iface)
out("VLAN_ID", vlan or "")
PY
)"

echo "Bassie-Router routing-simulatie zonder PPPoE (config: $CONFIG)"
echo "  Eerst testen: de WAN-NIC (DHCP of statisch) als uplink."
echo "  LAN: $LAN_CLIENT/$PREFIX -> $LAN_IP op '$BRIDGE'"
echo "  WAN: 192.0.2.1/24 -> 192.0.2.2 via '$WAN_IFACE'"

ip netns add "$NS_LAN"
ip netns add "$NS_ROUTER"
ip netns add "$NS_WAN"

# LAN-kant: virtuele host met default route via de router.
ip link add brsim-a type veth peer name brsim-b
ip link set brsim-a netns "$NS_LAN"
ip link set brsim-b netns "$NS_ROUTER"
ip -n "$NS_LAN" link set brsim-a name lan0
ip -n "$NS_LAN" addr add "$LAN_CLIENT/$PREFIX" dev lan0
ip -n "$NS_LAN" link set lan0 up
ip -n "$NS_LAN" route replace default via "$LAN_IP" dev lan0

# Router: bridge met de ingestelde naam, precies zoals bassie-setup-links.
ip netns exec "$NS_ROUTER" ip link add name "$BRIDGE" type bridge
ip -n "$NS_ROUTER" link set brsim-b master "$BRIDGE"
ip -n "$NS_ROUTER" link set brsim-b up
ip -n "$NS_ROUTER" addr add "$LAN_IP/$PREFIX" dev "$BRIDGE"
ip -n "$NS_ROUTER" link set "$BRIDGE" up

# WAN-kant: bij VLAN eerst een parent-link, daar een 802.1Q-interface over.
ip link add brsim-c type veth peer name brsim-d
ip link set brsim-c netns "$NS_ROUTER"
ip link set brsim-d netns "$NS_WAN"
if [[ -n "$VLAN_ID" ]]; then
    ip -n "$NS_ROUTER" link set brsim-c name simparent
    ip -n "$NS_ROUTER" link add link simparent name "$WAN_IFACE" type vlan id "$VLAN_ID"
    ip -n "$NS_ROUTER" link set simparent up
else
    ip -n "$NS_ROUTER" link set brsim-c name "$WAN_IFACE"
fi
ip -n "$NS_ROUTER" link set "$WAN_IFACE" up
ip -n "$NS_ROUTER" addr add 192.0.2.1/24 dev "$WAN_IFACE"
ip -n "$NS_ROUTER" route replace default via 192.0.2.2 dev "$WAN_IFACE"
ip netns exec "$NS_ROUTER" sysctl -w net.ipv4.ip_forward=1 >/dev/null

ip -n "$NS_WAN" link set brsim-d name wan0
ip -n "$NS_WAN" addr add 192.0.2.2/24 dev wan0
ip -n "$NS_WAN" link set wan0 up
# Nog geen route naar het LAN: een ping kan alleen terugkomen als NAT werkt.

# Dezelfde nftables-regels als de echte router, in de router-namespace.
PYTHONPATH="$BASSIE_SRC" python3 -m bassierouter --config "$CONFIG" render nft > "$RULES"
ip netns exec "$NS_ROUTER" nft -f "$RULES"

echo
echo "Test 1: LAN -> WAN via NAT"
if ip netns exec "$NS_LAN" ping -4 -c 2 -W 2 -q 192.0.2.2 >/dev/null 2>&1; then
    echo "  [OK] LAN-client bereikt de WAN-kant (masquerade werkt)."
else
    echo "  [FAIL] LAN-client bereikt de WAN-kant niet." >&2
    FAILED=1
fi

echo "Test 2: WAN -> LAN blijft dicht"
# Nu pas een route terug toevoegen: zonder firewall zou deze ping wél lukken.
ip -n "$NS_WAN" route replace "$LAN_NET" via 192.0.2.1 dev wan0
if ip netns exec "$NS_WAN" ping -4 -c 1 -W 2 -q "$LAN_CLIENT" >/dev/null 2>&1; then
    echo "  [FAIL] WAN-kant bereikt het LAN; firewall blokkeert niet." >&2
    FAILED=1
else
    echo "  [OK] WAN-kant kan het LAN niet bereiken."
fi

echo
if [[ $FAILED -eq 0 ]]; then
    echo "Simulatie geslaagd: LAN -> WAN werkt, WAN -> LAN is dicht."
else
    echo "Simulatie gefaald: bekijk nftables-regels en WAN/LAN-adressen." >&2
fi
exit "$FAILED"
