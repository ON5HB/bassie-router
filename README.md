# Bassie-Router

Versie: 0.1.57

IPv4-router op Debian 13 (Trixie), AMD64-PC, volledig via CLI en HTTP-GUI te beheren.

**Open source en gratis voor iedereen** (MIT-licentie, zie `LICENSE`): je mag
het gebruiken, aanpassen en delen. Geen account, geen abonnement, geen
telemetrie — draait volledig op je eigen hardware.

## Samenwerking

Dit project is niet uit het niets gevallen: het is **samen tot stand gekomen**.
**Bas Heijermans**, radioamateur met roepletters **ON5HB**, bepaalt de koers,
test op de echte router (Zyxel, PPPoE, de fysieke poorten) en houdt ons scherp
als iets niet klopt — zoals een verzonnen IP-adres of een GUI die stiekem
zonder login opende. **Big Pickle**, de AI-assistent in
[OpenCode](https://opencode.ai), schrijft de code: nftables, PPPoE-client,
bridge/DHCP/DNS, de Flask-GUI, de upgrade- en rollback-logica en de tests.
Elke fix krijgt een versie in git, zodat je altijd terug kunt.

Wil je de assistent zelf vinden of een soortgelijk project bouwen? Kijk op
**https://opencode.ai** — de open source coding agent waar Big Pickle in draait.

Die samenwerking is ook de basis van dit project: geen grote fabrikant, geen
betaalde firmware, geen cloud. Gewoon iemand met een router en een assistent
die niet ophoudt tot het goed werkt. Gebruik het, verbeter het, deel het.

## Wat zit erin

- **PPPoE-client** naar de ISP, optioneel over een **802.1Q WAN-VLAN** (bijv. `eth0.6`).
- **nftables** (alleen `table ip`): WAN → LAN standaard dicht, LAN → WAN open via NAT/masquerade, portforwards per tcp/udp.
- **dnsmasq** voor DHCP + DNS op het LAN (IPv4-only).
- **Bridge** voor alle LAN-NIC's: ze gedragen zich als één switch.
- **Flask GUI** met status, netwerk, Provider (PPPoE), firewall en toepassen/rollback.
- **Hostname** (`bassie-router`) instelbaar in **Netwerk** en mee in opslaan/rollback.
- **SSH-welkomstbericht** (`/etc/motd`) met credits en de actuele GUI-locatie.
- Alles draait **als root**, geen sudo. Backups vóór elke wijziging.

## Hardware

- Alle fysieke NIC's zijn na installatie **LAN** en zitten in bridge `switch`.
- Je kiest zelf welke poort WAN wordt; die gaat naar een modem in bridge-mode
  of een Fiber ONT (niet specifiek één merk). De ISP-gegevens voor PPPoE vul je
  in bij **Provider**.
- Die gekozen poort verlaat de bridge en luistert daarna niet meer op LAN;
  alle overige poorten blijven LAN.
- Werkt ook met een 2-NIC machine: kies er één als WAN, de ander blijft LAN.

Vind de NIC-namen met:

```sh
ip -br link
```

In de GUI kies je de WAN-interface bij **Provider → WAN & VLAN** en de
LAN-poorten bij **Netwerk**, uit dropdowns met de werkelijk aanwezige NIC's.
De **Provider VLAN-id** staat bij **Provider → Stap 2: PPPoE**.
Bestaande waarden blijven zichtbaar als de NIC op dat moment niet aanwezig is.

## Installatie (Debian 13, headless, root)

```sh
git clone https://github.com/ON5HB/bassie-router.git /opt/bassie-router
cd /opt/bassie-router
bash install.sh
```

Het script installeert als eerste `git`, daarna `nftables ppp dnsmasq
iproute2 isc-dhcp-client curl python3 python3-flask linux-tools-common`
(voor `cpupower`) en, als
`python3-pam` ontbreekt, `pamtester`. `git` is nodig voor latere updates.
De checkout is `/opt/bassie-router`; `install.sh` herkent dat en kopieert
`src/` niet naar zichzelf. Hij maakt
`/etc/bassie-router/config.json` en start de GUI-service.

Voor PPPoE in de volgende fase is alléén `ppp` nodig: dat levert `pppd` en de
`rp-pppoe.so`-plugin die de router gebruikt. Het losse `pppoe`-pakket wordt
niet gebruikt en wordt niet meer geïnstalleerd.

## Bestaande configuratie bijwerken

`install.sh` **verwijdert nooit** een bestaande `/etc/bassie-router/config.json`.
Bestaat hij, dan maakt het script eerst een backup in
`/var/backups/bassie-router/config-<datum>.json` en vult het ontbrekende
parameters met hun default aan (`migrate`). Oude waarden blijven behouden;
verouderde/onbekende parameters worden gemeld en niet overgenomen. Een config
zonder `bootstrapped` (oud formaat) wordt als initiele setup behandeld: alle
poorten zijn LAN en de GUI-bind wordt aangepast naar het DHCP-adres van `switch`.

De hostname wordt bij elke installatie uit de config gezet (default
`bassie-router`) met `hostnamectl`, met `hostname -F` als fallback. Staat er in
de config een andere naam, dan wint die; het script print de actieve naam. Je
huidige SSH-prompt verandert pas bij een nieuwe login/sessie.
Het script meldt daarna dat je de GUI moet controleren.

Hetzelfde kun je later zelf doen met:

```sh
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter migrate
```

## Eerste setup (belangrijk)

Direct na installatie weet de router **nog niet** wat WAN is. Alle poorten zijn
LAN; de bridge is dan nog **DHCP-klant** en haalt een adres uit je bestaande
netwerk (bijv. `192.168.1.24`). Open de GUI op dat adres:

```text
http://<dhcp-adres-van-de-router>:8080
```

Ga naar **Provider → WAN & VLAN**, kies de poort die WAN moet worden
(bijv. `enp1s0`) en klik op **Wijzigingen Opslaan**.

> ⚠️ **Sluit je PC vóór Wijzigingen Opslaan aan op een andere poort** dan de gekozen
> WAN-poort. Daarna is die poort geen LAN meer.

De **DHCP-server staat standaard uit**. Zet bij **Netwerk → DHCP-server aan**
alleen aan als deze router zelf adressen moet uitgeven:

- **DHCP uit (standaard):** de router blijft DHCP-klant en houdt het adres dat
  hij van de bestaande DHCP-server krijgt (bij jou `192.168.1.24`). De GUI
  blijft op `http://192.168.1.24:8080` en LAN-clients krijgen hun adres van de
  bestaande DHCP-server.
- **DHCP aan:** de router stopt met DHCP-klant zijn, geeft zelf adressen uit
  via dnsmasq en gebruikt het ingestelde LAN-IP. De GUI verhuist naar
  `http://<ingesteld-LAN-IP>:8080`.

In beide gevallen is de gekozen poort WAN (VLAN/PPPoE) zonder LAN-IP en zijn de
overige poorten LAN in `switch`. De GUI luistert nooit op WAN.

## GUI bereiken

De GUI luistert op het **eigen adres van de router**: met DHCP uit op het
adres van de externe DHCP-server (bijv. `192.168.1.24:8080`), met DHCP aan op
het ingestelde LAN-IP. Je kunt ook kiezen voor `127.0.0.1` en via een tunnel
werken:

```sh
ssh -L 8080:127.0.0.1:8080 root@<router-ip>
```

Open dan `http://localhost:8080` op je werkstation.

**Inloggen verplicht:** gebruik `root` + hetzelfde wachtwoord als bij SSH. De
GUI authenticeert via PAM (`/etc/pam.d/bassie-router`). Op Debian 13 is
`python3-pam` er niet meer; `install.sh` installeert dan automatisch de
`pamtester`-CLI, die het wachtwoord via PAM controleert. Zonder één van beide
weigert de installatie — er komt dus nooit een GUI zónder login.

Na aanmelden blijft de sessie **30 minuten** geldig; daarna moet je opnieuw
inloggen. Met de knop **Uitloggen** beëindig je de sessie direct.

De duur is instelbaar met `BASSIE_GUI_SESSION_MINUTES` (default `30`). Alleen
voor tests kun je PAM uitzetten (`BASSIE_GUI_PAM=0`) en een fallback gebruiken
via `BASSIE_GUI_USER` / `BASSIE_GUI_PASSWORD`; op de router wordt PAM gebruikt.

De GUI luistert uitsluitend op `127.0.0.1` of het LAN-adres van de router,
nooit op WAN. `install.sh` controleert of er een PAM-backend beschikbaar is en
herstart de GUI-service na een upgrade, zodat je niet per ongeluk op de oude
versie zonder login blijft zitten.

Rechtsboven in de GUI staat altijd `Versie: x.x.x · datum`, zodat je direct
ziet welke release er draait. Na een SSH-login toont `/etc/motd` dezelfde
informatie, de credits en de GUI-URL. Vanaf de CLI:

```sh
curl -s http://192.168.1.24:8080/login | grep -o 'Versie: [0-9.]* · [0-9-]*'
```

Dit is **gewoon HTTP** (geen TLS), dus alleen op een vertrouwd LAN of via een
SSH-tunnel gebruiken. Wijzigingen worden actief na klik op **Wijzigingen Opslaan**.

## Systeem-tab

**Systeem** bevat de beheersacties:

- **Backup**: download de huidige `config.json` (inclusief PPPoE-wachtwoorden
  — dus veilig bewaren).
- **Herstellen** (onder Backup): klik op **Bladeren**, kies een eerder
  gedownload bestand en bevestig. Daarna
  zijn de instellingen nog niet actief; klik op **Wijzigingen Opslaan** of gebruik
  **Negeren** om terug te gaan naar de laatst toegepaste configuratie.
- **Reboot** en **Shutdown**: dubbele zekerheid — eerst het vinkje
  "Ik weet het zeker" aanvinken, daarna een popup ("Weet je zeker dat de router
  moet rebooten / afsluiten?") met **Ja** / **Nee**. Pas bij **Ja** draait
  `systemctl reboot` / `systemctl poweroff`. Beide zijn CSRF-beveiligd.
- **Uitloggen** (staat ook rechtsboven naast de versie).

## CPU-profiel en temperatuur

De nominale CPUsnelheid (zonder turbo) wordt gelezen uit de `model name` in
`/proc/cpuinfo` (bij de N3710: 1600 MHz). **Systeem → CPU-profiel** toont die
waarde en stelt `powersave` voor met 50% daarvan als minimum (800 MHz) en de
nominale snelheid als maximum — geen boost, lage temperatuur. Met **Gebruik dit
voorstel** vul je die waarden in; met **CPU nu toepassen** in het formulier gaan ze
meteen in.

- **Status** toont de temperaturen per core (uit `/sys/class/hwmon`, met
  fallback naar de ACPI-thermal zone), de huidige governor en min/max.
- **Systeem → CPU-profiel (cpupower)** toont alle instelbare frequenties uit
  `scaling_available_frequencies` als datalist, zodat je ziet welke waarden je
  kunt kiezen. Governor, minimum en maximum worden opgeslagen in `config.json`
  en met **CPU nu toepassen** meteen ingeschakeld (opnieuw uitlezen van de temperatuur
  gebeurt bij elke pagina-refresh).
- `cpupower` is de eerste keuze; als dat ontbreekt of faalt, gebruikt het profiel
  `cpufreq-set` en daarna rechtstreeks sysfs. `install.sh` probeert meerdere
  pakketnamen zonder af te breken.

Blijft de kast heet ondanks een lage CPU-belasting, dan is het koeling/omgeving:
controleer de ventilator, stof en of het apparaat in een gesloten kastje staat.

## LAN-adres na reboot (DHCP-server uit)

Als de DHCP-server uit staat, is de router zelf DHCP-klant op de LAN-bridge.
`bassie-setup-links` maakt de bridge nu eerst aan (met `modprobe bridge` en
tien pogingen) en wacht daarna max. 10 seconden met `udevadm settle` tot de
poorten hun definitieve naam hebben (`enp1s0` enz.). Een onbegrensd
`udevadm settle` kon de service namelijk blokkeren, waardoor `switch` na een
reboot helemaal niet bestond. Het script faalt ook niet meer halverwege door
`set -e`. Voordat een poort in de bridge gaat, worden oude adressen op die
poort gewist; anders kan hetzelfde IP zowel op `enp1s0` als op `switch` staan.

Het adres wordt nu door `bassie-lan-dhcp.service` beheerd: een permanente
`dhclient -4 -d` met `Restart=always`. Komt de externe DHCP-server pas later
bij, dan probeert de service het opnieuw tot er een lease is. De lease staat in
`/var/lib/dhcp/dhclient-bassie-lan.leases` (het Debian-pad), niet in
`/var/lib/bassie-router`; dat voorkomt AppArmor- en rechtenfouten. Controleer op
de console met:

```sh
systemctl is-active bassie-lan-dhcp.service
ip -4 addr show switch
```

De GUI volgt dit adres: als DHCP een ander adres geeft dan `gui.bind`, luistert
de GUI op het adres dat de bridge werkelijk heeft (nooit op `0.0.0.0`). Tijdens
de initiele setup is `127.0.0.1` tijdelijk en volgt de GUI de bridge zodra er
een lease is; als je het later bewust instelt, blijft hij lokaal. Zolang er nog
geen adres is, wacht de GUI kort en valt hij terug op `127.0.0.1`. Ook de
**MOTD** toont bij elke login het actuele adres via
`/etc/update-motd.d/40-bassie-router-ip` — geen verouderd IP meer in
`/etc/motd`.

`bassie-lan-dhcp.service` start direct `dhclient`. Een eerdere `ExecStartPre`
die op een IP wachtte veroorzaakte een deadlock: de service wachtte op een adres
die alleen hij zelf kon halen. `install.sh` voert nu een volledige `apply` uit en reboot daarna automatisch.
Daardoor zijn `bassie-links.service`, `bassie-lan-dhcp.service` en de GUI ook
ná een update weer actief, met `switch` en een vers DHCP-adres. `sysctl` gebruikt `-e`, zodat ontbrekende
`nf_conntrack`-keys geen foutmelding geven zolang de kernelmodule nog niet
geladen is. Bij DHCP-server **aan** wordt de DHCP-client-service
uitgeschakeld en staat het LAN-IP vast in `config.json`.

## WAN-adres: eerst de NIC, daarna eventueel PPPoE

**Provider → Stap 1: WAN-adres (NIC)** bepaalt hoe de WAN-poort aan een adres
komt. Welke van de twee je nodig hebt, hangt af van de DSL-modem:

- **DHCP** (standaard): `dhclient -4 -1` haalt adres, gateway en default route
  bij de modem/ONT. De router routeert direct via de WAN-NIC.
- **Statisch IP + gateway**: vul WAN-IP, prefix, gateway en optioneel WAN-DNS in.
  De router zet `ip addr add` en `ip route replace default via …` op de WAN-poort.

**Provider → Stap 2: PPPoE (optioneel)** zet daar een PPPoE-sessie overheen:

- Uit (standaard): `ppp0` bestaat niet; de WAN-NIC is de uplink.
- Aan: de NIC houdt eerst zijn DHCP- of beheeradres; daarna bouwt `pppd`
  `ppp0` op. `ppp0` krijgt pas een eigen IP nadat de provider contact gemaakt
  heeft, en vervangt de default route (`defaultroute`,
  `replacedefaultroute`, `usepeerdns`).
- Vul je bij PPPoE een beheeradres in (bijv. `192.168.1.2` bij modem
  `192.168.1.1`), dan krijgt de NIC dat adres mét een route naar de modem,
  zonder default route. De router pingt dat modem-adres nog wel, maar start
  PPPoE ook als de modem niet antwoordt: in bridge-mode hoeft de modem niet op
  een ping te reageren. Een mislukte ping is dus alleen een waarschuwing.
- De modem moet in bridge-mode staan, of het is een Fiber ONT die PPPoE
  doorgeeft. Het is geen telefoonmodem.
- De sessie wordt elke 30 seconden gecontroleerd met een LCP-echo. Eén gemiste
  echo is genoeg om te verbreken (`lcp-echo-failure 1`); daarna probeert pppd
  direct opnieuw. Vroeger duurde dat ~2 minuten.

De keuze staat in `config.json` als `interfaces.wan_mode` (`dhcp`, `static` of
`pppoe`). `wan_mode=pppoe` ontstaat alleen als PPPoE écht is ingeschakeld.

## VLAN van de provider

Via de tab **Provider → Stap 2: PPPoE → Provider VLAN-id**:

- Provider geeft bijv. VLAN 6 → vul `6` in. De router maakt `eth0.6` en PPPoE
  loopt daarover. Controle: `ip -d link show eth0.6`.
- Geeft de provider geen VLAN, of haalt de modem/ONT de tag eraf → veld leeg
  laten (of `0`; dat betekent hetzelfde). De router ruimt dan ook een oude
  subinterface zoals `enp1s0.10` op.
- Zet je PPPoE uit, dan blijft het ingestelde VLAN-id in de GUI staan, maar het
  wordt niet toegepast: de WAN-NIC gebruikt dan rechtstreeks DHCP of het statische
  adres en een oude VLAN-subinterface wordt opgeruimd. Zet je PPPoE weer aan,
  dan komt het VLAN-id vanzelf terug.
- Het VLAN geldt **alleen voor WAN**. De tag wordt op de WAN-subinterface
  getermineerd; de bridge `switch` staat expliciet met `vlan_filtering 0`, dus
  er komt geen 802.1Q-tag op LAN terecht. Het LAN is IPv4-only.
- nftables schrijft WAN-regels twee keer uit: één voor de fysieke NIC en één
  losse met `oifname "ppp*"`. nftables gebruikt `*` als wildcard; `ppp+` is
  iptables-syntax en matcht nooit `ppp0`. Daardoor vielen NAT en forward voor
  PPPoE weg, terwijl de router zelf wél internet had.

## DHCP-klanten

De tab **DHCP** (tussen Firewall en Systeem) laat alle huidige DHCP-uitgaven
zien: MAC-adres, IP-adres, naam en wanneer de lease is gezien.

- **Vastzetten** koppelt de MAC aan een gekozen IP. Het apparaat krijgt dan
  altijd dat adres; de lease verloopt niet.
- Je kunt het IP vóór het vastzetten aanpassen en de naam wijzigen, bijvoorbeeld
  van `HPLaserjet56712367.local` naar `Printer`. De router bewaart alleen de
  korte naam (`Printer`).
- Wil je dat DNS hem als `Printer.local` uitgeeft, zet dan bij **Netwerk** het
  DNS-domein op `local`. Zonder domeinwerking blijft `Printer` ook werken.
- Bij **Vaste adressen** kun je de naam later nog aanpassen (**Naam opslaan**)
  of de koppeling verwijderen (**Verwijderen**).
- Lokale namen worden met `local=/<domein>/` niet naar de WAN-DNS-upstream
  gestuurd. `filter-AAAA` houdt IPv6-antwoorden uit het LAN.
- De firewall blokkeert standaard DNS (tcp/udp 53) van LAN naar WAN, met het
  commentaar `DNS alleen via router`. Elke query gaat zo expliciet via de
  router, die bepaalt of er een forward is. Dat maakt latere DNS-filtering
  mogelijk. Schakelaar: **Firewall → DNS van LAN altijd via router**; met die
  checkbox uit mag een klant zelf een externe DNS-server vragen. De router zelf
  mag altijd naar zijn upstream.
  Test vanaf een LAN-PC: `nslookup workstation 192.168.88.1` moet antwoorden,
  `nslookup workstation 192.168.1.1` moet falen (met de schakelaar aan).
- Opslaan herstart de GUI niet meer; alleen als het bindadres of de GUI-poort
  verandert, volgt een uitgestelde herstart.
- Op **Status** staat een kaart **Configuratie-controle**: die vergelijkt de
  opgeslagen stand met wat er echt actief is (DNS via router, DHCP-server en
  PPPoE). Bij **WIJKT AF** staat op **Firewall** ook een waarschuwing.
- nftables wordt na elke toepassing expliciet herladen met
  `nft -f /etc/nftables.conf`; `enable --now` herlaadt een draaiende service
  niet. Daardoor kunnen config en actieve regels niet uit sync raken.
- Ook `bassie-links.service` en de PPPoE-client worden na toepassen expliciet
  herstart. Zonder dat blijft een nieuw WAN-VLAN-id ontbreken als
  `enp1s0.<id>` en gebruikt pppd oude instellingen.
- nftables zet de TCP-MSS naar de route-MTU (`tcp option maxseg size set rt
  mtu`). PPPoE heeft MTU 1492 terwijl het LAN 1500 gebruikt; zonder clamping
  werken ping en DNS, maar blijven grote TCP-pakketten (websites, HTTPS)
  hangen met `Frag needed and DF set (mtu = 1492)`. De LAN-MTU blijft 1500.
- dnsmasq start pas ná `bassie-links.service` (drop-in
  `/etc/systemd/system/dnsmasq.service.d/10-bassie.conf`) en gebruikt
  `bind-dynamic`. Zo wacht hij op het LAN-adres in plaats van te falen met
  `failed to create listening socket for 192.168.88.1`.

Op **Status** staat expliciet: **DHCP-server op LAN — AAN — Dnsmasq geeft
adressen uit op de poort switch**, met een knop naar de DHCP-klanten.

## Portforwards

**Firewall → Portforwards**: één regel is `WAN + poort → LAN-IP + poort`, met
protocol `tcp` of `udp`. Optioneel beperk je de bron met een IPv4-netwerk
(bijv. `1.2.3.0/24`); `any` staat voor iedereen.

Voorbeeld: `tcp 8080 → 192.168.88.10:80`.

De firewall-regels zijn dan: DNAT in `prerouting` + een `accept` in `forward`
alleen voor dat IP en die poort. Al het overige WAN → LAN blijft vallen.

### UDP NAT-timeout en no-track

- Globaal: standaard **30 seconden** (`firewall.udp_nat_timeout`); `0` = kernel-default.
- **No-track UDP-poorten** (bijv. `123` voor NTP): geldt voor verkeer van/naar
  de router zelf. Vul de poorten komma-gescheiden in; je kunt ze zelf toevoegen
  en verwijderen.
- **Per poortforward**: kolom *UDP-timeout (s)*. Zet bij een UDP-server achter NAT
  bijv. `1` seconde. Echte `notrack` kan niet met DNAT, want NAT heeft conntrack
  nodig; een korte timeout is hier het juiste mechanisme.

Conntrack blijft dus aan voor al het verkeer dat vertaald moet worden.

## IPv6

IPv6 staat volledig uit via `/etc/sysctl.d/99-bassie-router.conf`.
De firewall gebruikt alleen `table ip`.

## CLI

```sh
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter validate
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter render nft
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter apply --dry-run
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter apply
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter migrate
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter pending
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter discard
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter mark-applied
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter rollback
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter status
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter check
PYTHONPATH=/opt/bassie-router/src python3 -m bassierouter simulate --nic-only
```

`install.sh` maakt ook de kortere alias `/usr/local/bin/bassie-router`, dus op
de router kun je gewoon typen:

```sh
bassie-router check          # LAN-IP, route, ping, DNS, firewall
bassie-router simulate --nic-only   # beleid zonder PPPoE controleren
```

Met `bassie-simulate-routing` (root) kun je daarnaast echte ping-tests tussen
virtuele LAN- en WAN-kanten doen, zonder de productie-interface aan te raken.

## Openstaande wijzigingen

Zodra opgeslagen instellingen afwijken van wat actief is, zie je bovenaan elke
GUI-pagina een gele melding met **Wijzigingen bekijken**. Die melding betekent:
**nog NIET actief**. Op de pagina **Wijzigingen bekijken** staat het ook expliciet
in het rood (of in het groen: **Wél actief**).

In de formulieren verschijnen de knoppen pas nadat je iets in een veld hebt
gewijzigd:

- **Wijzigingen Opslaan**: bewaart én maakt de instellingen meteen actief.
- **Negeren**: slaat niets op en zet de velden terug naar de laatst toegepaste
  versie (uit `/var/lib/bassie-router/applied-config.json`); er worden geen
  services of firewallregels aangepast.

Hetzelfde kan vanaf de CLI met `bassie-router pending` en `bassie-router discard`.

## Wijzigingen Opslaan (toepassen) en rollback

Bij **Wijzigingen Opslaan** gebeurt, in deze volgorde:

1. Configuratie valideren.
2. `nft -c` syntaxcheck.
3. Backup maken van alle bestaande bestanden in `/var/backups/bassie-router/`.
4. Bestanden atomisch schrijven (nftables, dnsmasq, sysctl, PPP, units, links-script).
5. `sysctl`, `systemd daemon-reload`, links, nftables, dnsmasq, PPPoE, GUI herstarten.

Bij een fout wordt automatisch teruggerold. Met **Laatste backup terugzetten**
(of `bassierouter rollback`) herstel je handmatig.

**Tip:** sluit tijdens de eerste keer een console/keyboard aan op de router.
Een verkeerde WAN-regel kan je SSH-verbinding verbreken.

## Factory reset via GRUB

In het opstartmenu staat **Bassie-Router: verse installatie (factory reset)**.
Die keuze start de router met de standaardwaarden: alle fysieke poorten zijn
LAN, de bridge haalt via DHCP een adres en de GUI volgt dat adres. De
backups onder `/var/backups/bassie-router/` blijven staan.

De keuze werkt met `bassie_factory=1` op de kernelregel. `/etc/grub.d/40_custom`
gebruikt `/vmlinuz` en `/initrd.img`, dus kernelupdates volgen automatisch;
`grub-mkconfig` wordt bij **Wijzigingen Opslaan** opnieuw gedraaid. Alleen bij een aparte
`/boot`-partitie wordt de nieuwste kernelnaam ingevuld en moet de keuze na een
kernelupdate opnieuw worden toegepast.

De reset start de link-, DHCP- en GUI-services direct (`enable --now`), zodat
één boot genoeg is. Daarna kun je in de GUI via **Systeem → Herstellen** een
eerder gedownloade backup terugzetten en opnieuw **Wijzigingen Opslaan** klikken.

## Tests

```sh
python3 -m venv /tmp/opencode/routerctl-venv
/tmp/opencode/routerctl-venv/bin/pip install -e '.[dev]'
PYTHONPATH=src /tmp/opencode/routerctl-venv/bin/python -m pytest -q
```

## Screenshots

Demobeelden staan in `screenshots/`: `dashboard.png`, `network.png`, `provider.png`, `firewall.png`, `apply.png` (met de waarschuwing voor de eerste keer en de gele melding voor openstaande wijzigingen).

Updates gaan zonder zip. Op de router:

```sh
cd /opt/bassie-router
git pull
bash install.sh
```

`install.sh` reboot automatisch; de laatste zichtbare regel is `REBOOT!`.
