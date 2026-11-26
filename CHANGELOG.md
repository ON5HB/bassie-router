# Changelog

## 0.1.59 — 2026-11-26

- Status: vriendelijke servicenamen, functionele DNS/DHCP-checks en fabrieksreset uit lijst

## 0.1.58 — 2026-11-26

- Status opgeschoond: Actief/Uit bij Services, WAN-VLAN naar Provider, DHCP-kaart verplaatst en eenvoudige VLAN-tekst

## 0.1.57 — 2026-11-26

- Korte duidelijke Provider-teksten, DHCP-info in GUI en instelbare PPPoE-idle-timeout (300 s)

## 0.1.56 — 2026-11-26

- PPPoE-VLAN alleen actief bij PPPoE aan (waarde blijft bewaard); nftables-wildcard ppp* gecorrigeerd

## 0.1.55 — 2026-11-26

- install.sh herkent git-checkout in /opt/bassie-router en kopieert src niet naar zichzelf

## 0.1.54 — 2026-11-26

- git als eerste pakket in install.sh, zodat updates via git pull altijd mogelijk zijn

## 0.1.53 — 2026-11-26

- LCP-keepalive: bij één gemiste echo van 30 seconden direct verbreken en opnieuw verbinden

## 0.1.52 — 2026-11-26

- WAN-regels expliciet voor ppp+, bridge zonder VLAN-filtering en VLAN-id bij PPPoE in de GUI

## 0.1.51 — 2026-11-26

- TCP-MSS clamping naar de PPPoE-route-MTU, zodat websites vanaf het LAN wél laden

## 0.1.50 — 2026-11-26

- bassie-links en PPPoE-client herstarten na toepassen, zodat een nieuw WAN-VLAN direct als subinterface bestaat

## 0.1.49 — 2026-11-26

- PPPoE-interface voortaan met nic- prefix, zodat pppd een VLAN-subinterface accepteert

## 0.1.48 — 2026-11-26

- PPPoE-start blokkeert niet meer op een onbereikbare modem-ping; groene melding donkergroen met witte tekst

## 0.1.47 — 2026-11-25

- dnsmasq start na bassie-links met bind-dynamic; geen bind-fout meer bij vroeg opstarten

## 0.1.46 — 2026-11-25

- GUI-controlekaart config versus actieve staat, waarschuwing bij DNS-afwijking en expliciete nftables-herlaad na toepassen

## 0.1.45 — 2026-11-25

- filter-AAAA met hoofdletters hersteld (dnsmasq start weer), onnodig pppoe-pakket verwijderd; ppp blijft voor latere PPPoE

## 0.1.44 — 2026-11-25

- DNS van LAN standaard via router afdwingen (uitzetbaar voor latere filtering), GUI-herstart bij opslaan verholpen, filter-aaaa en lokale DNS-namen

## 0.1.43 — 2026-11-25

- DHCP-tab met klantenlijst, vastzetten op MAC/IP, hernoemen en lokale DNS-namen zonder WAN-lek

## 0.1.42 — 2026-11-25

- VLAN 0 betekent geen VLAN en ruimt oude subinterface op, duidelijke actief/niet-actief status, Negeren op wijzigingenpagina en WAN-link-check

## 0.1.41 — 2026-11-25

- WAN-NIC DHCP of statisch, PPPoE optioneel als stap 2, bridge switch, opslaan/negeren en verbindingschecks

## 0.1.40 — 2026-11-24

- install.sh eindigt nu expliciet met REBOOT! vlak voor de automatische reboot.

## 0.1.39 — 2026-11-24

- Factory-reset-boot start links, LAN-DHCP en GUI direct met enable --now, zodat één boot genoeg is; install.sh reboot automatisch na afloop.

## 0.1.38 — 2026-11-24

- install.sh reboot na afloop automatisch, zodat br0 en de GUI na elke update schoon omhoog komen.

## 0.1.37 — 2026-11-24

- GRUB-keuze 'Bassie-Router: verse installatie (factory reset)' toegevoegd: start met standaardwaarden, verwijdert onze configs, backups blijven staan; kernelupdates volgen via /vmlinuz en /initrd.img.

## 0.1.36 — 2026-11-24

- LAN-poorten worden vóór het masteren leeggemaakt, zodat een oud DHCP-adres niet zowel op de fysieke poort als op br0 blijft staan.

## 0.1.35 — 2026-11-24

- Deadlock in LAN-DHCP opgeheven (geen ExecStartPre meer); install.sh voert nu volledige apply uit zodat links-, DHCP- en GUI-services na reboot actief zijn; GUI volgt bridge-IP ook tijdens initiele setup.

## 0.1.34 — 2026-11-24

- Bridge-aanmaak verplaatst vóór udevadm settle, met modprobe bridge en 10 pogingen; settle nu begrensd tot 10 s zodat br0 na reboot niet ontbreekt.

## 0.1.33 — 2026-11-24

- GUI wacht nu op DHCP-adres via ExecStartPre en unit-volgorde; sysctl gebruikt -e tegen ontbrekende nf_conntrack-keys; install-melding verduidelijkt.

## 0.1.32 — 2026-11-24

- MOTD toont nu bij elke login het actuele LAN-adres via update-motd.d, in plaats van een verouderd IP uit config.json.

## 0.1.31 — 2026-11-24

- GUI-bind volgt na reboot het echte LAN-bridge-adres als DHCP een ander IP geeft; nooit 0.0.0.0, korte wacht op lease en fallback naar 127.0.0.1.

## 0.1.30 — 2026-11-24

- LAN-IP na reboot hersteld: links-script wacht op udev-namen en breekt niet meer af op falende link-acties; nieuwe bassie-lan-dhcp.service blijft DHCP-lease vragen tot de bridge een IP heeft.

## 0.1.29 — 2026-11-24

- Reboot en Shutdown hebben nu dubbele zekerheid: verplicht vinkje én Ja/Nee-popup; pas na beide gaat de router rebooten of afsluiten.

## 0.1.28 — 2026-11-24

- Reboot vraagt nu eerst via popup 'Weet je zeker dat de router moet rebooten?' met Ja/Nee knoppen; pas bij Ja wordt er echt gereboot.

## 0.1.27 — 2026-11-24

- CPU-profiel knop Toepassen past direct toe met temperatuur bij refresh; Backup/Herstellen kreeg Configuratie uploaden met Bladeren-knop.

## 0.1.26 — 2026-11-24

- Nominale CPUsnelheid dynamisch uit /proc/cpuinfo, voorstel powersave met 50% minimum; config-pad in GUI opgelost (None) en Backup/Herstellen als één sectie.

## 0.1.25 — 2026-11-24

- CPU-profiel fallback: sysfs-write wordt niet meer overgeslagen door een vroegtijdige exit 0 als cpupower aanwezig is maar frequency-set faalt; max blijft 1600 MHz (geen boost).

## 0.1.24 — 2026-11-24

- CPU-tools installeren zonder harde fout: meerdere package-namen proberen, fallback naar cpufreq-set en rechtstreeks sysfs waardoor 800-1600 MHz powersave ook zonder cpupower werkt.

## 0.1.23 — 2026-11-24

- CPU-profiel toegevoegd: temperatuur op Status, cpupower min/max met instelbare waarden (default 800-1600 MHz, powersave, geen boost) en bassie-cpu.service.

## 0.1.22 — 2026-11-24

- Systeem-tab toegevoegd: backup-download, restore via upload, reboot, shutdown en uitloggen; alle acties met bevestiging en CSRF.

## 0.1.21 — 2026-11-24

- WAN & VLAN en WAN-adres verhuisd van Netwerk naar Provider; opslaan daar verwerkt poort, VLAN en modus; typfout 'ándere' ook in README gecorrigeerd.

## 0.1.20 — 2026-11-24

- Typfout in install.sh: accent op de A in 'ándere poort' verwijderd.

## 0.1.19 — 2026-11-24

- WAN-adresmodi toegevoegd: PPPoE, DHCP-client of statisch IP+gateway+DNS; links-script, PPPoE-service en default route volgen de gekozen modus.

## 0.1.18 — 2026-11-24

- Generieke WAN-uitleg: modem in bridge-mode of Fiber ONT, ISP-gegevens bij Provider; geen merkgebonden Zyxel-aanname meer.

## 0.1.17 — 2026-11-24

- Status toont het echte LAN-adres bij DHCP-server uit (niet meer de ingestelde 192.168.88.1); Zyxel-bridge-aannames uit GUI en README vervangen door ISP-gegevens.

## 0.1.16 — 2026-11-24

- Installatie zet hostname nu betrouwbaar uit de config via hostnamectl (fallback hostname -F) en print de actieve naam; config-override blijft leidend.

## 0.1.15 — 2026-11-24

- Credit-url toegevoegd: https://opencode.ai in GUI-footer, SSH-motd en README, zodat mensen Big Pickle/OpenCode kunnen vinden.

## 0.1.14 — 2026-11-24

- Credits: Bas Heijermans (ON5HB, radioamateur) in GUI, SSH-motd, README en LICENSE.

## 0.1.13 — 2026-11-24

- Hostname bassie-router instelbaar in Netwerk en toepasbaar met backup/rollback; SSH-motd met credits en actuele GUI-locatie.

## 0.1.12 — 2026-11-24

- Debian 13: PAM-login via pamtester-CLI als python3-pam ontbreekt; installatie weigert zonder PAM-backend.

## 0.1.11 — 2026-11-24

- Credits toegevoegd: samenwerking Bas + Big Pickle in README en GUI-footer; reclame voor het open-source project.

## 0.1.10 — 2026-11-24

- DHCP-server standaard uit met waarschuwing; router blijft dan DHCP-klant op zijn eigen adres; GUI herstart bij upgrade en PAM verplicht; geen verzonnen LAN-IP meer in meldingen; MIT-licentie.

## 0.1.9 — 2026-11-24

- Verduidelijkt: router is eerst DHCP-klant, na Toepassen zelf DHCP-server met het ingestelde LAN-IP.

## 0.1.8 — 2026-11-24

- Login via root/PAM met 30-minuten sessie; versie en datum rechtsboven; curl in install; meldingen tonen het echte router-LAN-IP; oude zips blijven staan.

## 0.1.7 — 2026-11-24

- Versie en releasedatum rechtsboven in de GUI; curl toegevoegd aan de installatie.

## 0.1.6 — 2026-11-24

- Fix CLI: --config/--root werken nu ook ná het subcommand; install.sh gebruikt veilige volgorde.

## 0.1.5 — 2026-11-24

- Geen login in de GUI; tab/status hernoemd naar Provider met account-info; gele melding met Toepassen/Negeren bij openstaande wijzigingen.

## 0.1.4 — 2026-11-24

- Geen login meer in de GUI (iedereen op LAN); tab PPPoE hernoemd naar Provider met account-info in de status.

## 0.1.3 — 2026-11-24

- Fix dubbel IP op br0 en IPv6-resten bij initiele setup; GUI-bind volgt nu het DHCP-adres ook bij migratie van oude configs.

## 0.1.2 — 2026-11-24

- Configuratie-migratie bij installatie: backup + defaults aanvullen, oude waarden behouden en melding bij aanpassingen.

## 0.1.1 — 2026-11-24

- Initiele setup: alle poorten LAN met DHCP op br0; WAN kiezen in de GUI; kabelwaarschuwing; GUI verhuist na Toepassen naar LAN-IP.

## 0.1.0 — 2026-11-24

- Eerste versie: PPPoE-client, WAN-VLAN, LAN-bridge, nftables, dnsmasq, Flask-GUI, PAM-login en rollback.

