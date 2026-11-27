#!/bin/bash
# Bassie-Router installatie op Debian 13 (Trixie), headless, als root.
# Direct na installatie zijn ALLE fysieke poorten LAN: de bridge haalt een IP
# via DHCP uit het bestaande netwerk. Pas als je in de GUI een WAN-poort kiest
# en Toepassen klikt, wordt die poort WAN en de rest LAN.
# Geen sudo: draai dit script als root.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Dit script moet als root draaien." >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG=/etc/bassie-router/config.json

echo "== Pakketten installeren =="
# ppp levert pppd en de rp-pppoe.so-plugin voor optionele PPPoE; het losse
# pppoe-pakket wordt niet gebruikt.
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git \
    nftables \
    ppp \
    dnsmasq \
    hostapd \
    iw \
    iproute2 \
    isc-dhcp-client \
    iputils-ping \
    curl \
    python3 \
    python3-flask

# CPU-profiel: package-naam verschilt per Debian/kernel. Probeer candidates
# zonder de installatie te laten falen als één van ze niet bestaat.
if ! command -v cpupower >/dev/null 2>&1; then
    for pkg in "linux-tools-$(uname -r)" linux-tools-generic linux-tools-common cpufrequtils; do
        if apt-get install -y "$pkg" >/dev/null 2>&1; then
            echo "CPU-tools geïnstalleerd via $pkg"
            break
        fi
    done
fi
if ! command -v cpupower >/dev/null 2>&1 && ! command -v cpufreq-set >/dev/null 2>&1; then
    echo "Let op: geen cpupower/cpufreq-set gevonden; CPU-profiel wordt niet toegepast."
fi

# Login via PAM. Debian 13 levert geen bruikbare python3-pam module meer; dan
# gebruiken we de pamtester-CLI. Zonder één van beide weigeren we te installeren,
# want een GUI zonder login is niet acceptabel.
if ! python3 -c 'import pam' >/dev/null 2>&1; then
    echo "python3-pam niet beschikbaar; pamtester wordt gebruikt."
    apt-get install -y pamtester
fi
if ! python3 -c 'import pam' >/dev/null 2>&1 && ! command -v pamtester >/dev/null 2>&1; then
    echo "FOUT: geen PAM-backend (python3-pam of pamtester); GUI zou zonder login starten." >&2
    exit 1
fi

echo "== Bestanden installeren =="
if [[ "$SRC_DIR" == "/opt/bassie-router" ]]; then
    # De git-checkout ís de installatie: 'git pull' vervangt de code direct.
    # Een self-copy van src/. naar src/ zou 'same file'-fouten geven.
    echo "Git-checkout gebruikt als installatiedirectory."
else
    install -d -m 0755 /opt/bassie-router/src
    cp -a "$SRC_DIR/src/." /opt/bassie-router/src/
fi
install -m 0755 "$SRC_DIR/simulate-routing.sh" /usr/local/sbin/bassie-simulate-routing
cat > /usr/local/bin/bassie-router <<'CLI'
#!/bin/sh
PYTHONPATH=/opt/bassie-router/src exec /usr/bin/python3 -m bassierouter "$@"
CLI
chmod 0755 /usr/local/bin/bassie-router
install -d -m 0755 /etc/bassie-router
if [[ ! -f "$CONFIG" ]]; then
    install -m 0600 "$SRC_DIR/etc/bassie-router/config.json" "$CONFIG"

    echo "== Initiele setup: alle poorten zijn LAN =="
    mapfile -t PORTS < <(
        for entry in /sys/class/net/*; do
            name=${entry##*/}
            case "$name" in
                lo|br*|ppp*|veth*|docker*|virbr*|wl*|*.*) ;;
                *) echo "$name" ;;
            esac
        done
    )
    if [[ ${#PORTS[@]} -eq 0 ]]; then
        PORTS=(eth1)
    fi
    python3 - "$CONFIG" "${PORTS[@]}" <<'PY'
import json, sys
path, ports = sys.argv[1], sys.argv[2:]
with open(path, encoding="utf-8") as handle:
    cfg = json.load(handle)
cfg["bootstrapped"] = False
cfg["interfaces"]["wan"] = "kies-poort"
cfg["interfaces"]["lan_ports"] = ports
cfg["firewall"]["allow_gui_bootstrap"] = True
cfg["gui"]["bind"] = "127.0.0.1"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(cfg, handle, indent=2, ensure_ascii=False)
    handle.write("\n")
PY
else
    echo "== Bestaande configuratie bijwerken =="
    echo "Oude waarden blijven behouden; ontbrekende parameters krijgen een default."
    PYTHONPATH=/opt/bassie-router/src /usr/bin/python3 -m bassierouter \
        --config "$CONFIG" migrate
    echo "Controleer in de GUI (Netwerk) of WAN-poort, LAN-poorten en GUI-bind nog kloppen."
fi

# Bij een upgrade van een al ingestelde router: markeer de huidige config als
# toegepast, zodat de GUI niet ten onrechte 'initiele setup' meldt.
if [[ ! -f /var/lib/bassie-router/applied-config.json ]] && \
   python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("bootstrapped", False) else 1)' "$CONFIG"; then
    PYTHONPATH=/opt/bassie-router/src /usr/bin/python3 -m bassierouter \
        --config "$CONFIG" mark-applied || true
fi

# IPv4-only: zet IPv6 direct uit, nog vóór de links worden aangemaakt.
PYTHONPATH=/opt/bassie-router/src /usr/bin/python3 -m bassierouter render sysctl \
    > /etc/sysctl.d/99-bassie-router.conf
sysctl -e -p /etc/sysctl.d/99-bassie-router.conf >/dev/null || true

# Maak direct de bridge aan met alle LAN-poorten.
PYTHONPATH=/opt/bassie-router/src /usr/bin/python3 -m bassierouter render links \
    > /usr/local/sbin/bassie-setup-links
chmod 0755 /usr/local/sbin/bassie-setup-links
/usr/local/sbin/bassie-setup-links

BOOTSTRAPPED=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("bootstrapped", False))' "$CONFIG")
BR=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["interfaces"]["lan_bridge"])' "$CONFIG")
if [[ "$BOOTSTRAPPED" != "True" ]]; then
    # Initiele setup: vraag één lease aan zodat de GUI meteen bereikbaar is.
    # De permanente DHCP-client start hierna via 'apply'.
    timeout 30 dhclient -4 -1 "$BR" 2>/dev/null || true
    DHCP_IP=$(ip -4 -o addr show dev "$BR" scope global | awk '{print $4}' | cut -d/ -f1 | head -1 || true)
    if [[ -n "$DHCP_IP" ]]; then
        python3 - "$CONFIG" "$DHCP_IP" <<'PY'
import json, sys
path, ipaddr = sys.argv[1:3]
with open(path, encoding="utf-8") as handle:
    cfg = json.load(handle)
cfg["gui"]["bind"] = ipaddr
with open(path, "w", encoding="utf-8") as handle:
    json.dump(cfg, handle, indent=2, ensure_ascii=False)
    handle.write("\n")
PY
        echo "GUI-bind aangepast naar ${DHCP_IP} (initiele setup)."
    else
        echo "Nog geen DHCP-adres op ${BR}; de GUI volgt het adres zodra de lease binnenkomt." >&2
    fi
fi

# Hostname en SSH-welkomstbericht (met de actuele GUI-locatie) bij de installatie.
PYTHONPATH=/opt/bassie-router/src /usr/bin/python3 - "$CONFIG" <<'PY'
import sys
from pathlib import Path
from bassierouter.config import load_config
from bassierouter.services import render_hostname, render_hosts, render_motd

cfg = load_config(sys.argv[1])
old_host = Path("/etc/hostname").read_text(encoding="utf-8").strip() \
    if Path("/etc/hostname").exists() else ""
hosts = Path("/etc/hosts")
Path("/etc/hostname").write_text(render_hostname(cfg), encoding="utf-8")
hosts.write_text(
    render_hosts(cfg, hosts.read_text(encoding="utf-8") if hosts.exists() else "", old_host),
    encoding="utf-8",
)
Path("/etc/motd").write_text(render_motd(cfg), encoding="utf-8")
PY
# Zet de hostname écht actief. De config is leidend: wie daar een andere naam
# heeft ingevuld, krijgt die; anders bassie-router. hostnamectl werkt op Debian
# 13 het betrouwbaarst (ook tegenover systemd-hostnamed); -F is de fallback.
WANTED_HOSTNAME=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("hostname", "bassie-router"))' "$CONFIG")
if command -v hostnamectl >/dev/null 2>&1; then
    hostnamectl set-hostname "$WANTED_HOSTNAME" || hostname -F /etc/hostname
else
    hostname -F /etc/hostname
fi
echo "Hostname ingesteld op $(hostname) (volgens config: ${WANTED_HOSTNAME})."

# PAM-service voor de GUI: root + SSH-wachtwoord.
cat > /etc/pam.d/bassie-router <<'PAM'
auth required pam_unix.so
account required pam_unix.so
PAM

# Genereer alle units/scripts en activeer de volledige bootketen.
# 'apply' schrijft ook de GUI-unit. Eerder schreef install.sh een oude unit
# zonder bassie-links/bassie-lan-dhcp, waardoor br0 na een reboot ontbrak.
PYTHONPATH=/opt/bassie-router/src /usr/bin/python3 -m bassierouter \
    --config "$CONFIG" apply

GUI_IP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["gui"]["bind"])' "$CONFIG")
GUI_PORT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["gui"]["port"])' "$CONFIG")
LAN_IP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["interfaces"]["lan_ip"])' "$CONFIG")
DHCP_ENABLED=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["interfaces"].get("dhcp_enabled", False))' "$CONFIG")
echo
echo "Bassie-Router GUI draait op http://${GUI_IP}:${GUI_PORT}"
echo "Alle poorten zijn nu LAN; kies in de GUI eerst de WAN-poort en klik op Wijzigingen Opslaan."
echo "LET OP: sluit je PC vóór Wijzigingen Opslaan aan op een andere poort dan de WAN-poort."
if [[ "$DHCP_ENABLED" == "True" ]]; then
    echo "DHCP-server aan: na Toepassen luistert de GUI op http://${LAN_IP}:${GUI_PORT}."
else
    echo "DHCP-server uit: de router blijft DHCP-klant en de GUI blijft op http://${GUI_IP}:${GUI_PORT}."
    echo "Zet in de GUI bij Netwerk 'DHCP-server aan' als deze router zelf adressen moet uitgeven."
fi
echo "Login: root + je SSH-wachtwoord. Sessie 30 minuten. Alleen op een vertrouwd LAN gebruiken."
echo "Na de reboot: Status -> Verbindingschecks, of op de router: bassie-router check"
echo "REBOOT!"
sleep 3
systemctl reboot
