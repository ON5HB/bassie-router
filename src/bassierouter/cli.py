"""Command-line interface voor Bassie-Router."""
from __future__ import annotations

import argparse
import json
import sys

from . import connectivity, services, simulate
from .apply import (
    apply_config,
    discard_changes,
    pending_changes,
    rollback,
    snapshot_applied,
    status,
)
from .config import load_config, migrate_config, validate
from .nft import render_nft
from .web import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bassie-router", description="Bassie-Router beheer")
    parser.add_argument("--config", help="pad naar config.json")
    parser.add_argument("--root", default="/", help="root voor bestanden (voor tests/dry-run)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="controleer de configuratie")
    sub.add_parser("check", help="controleer of de verbinding op de router werkt")
    simulate_cmd = sub.add_parser("simulate", help="simuleer het routerbeleid zonder hardware")
    simulate_cmd.add_argument(
        "--nic-only",
        action="store_true",
        help="test alléén de WAN-NIC (DHCP/statisch), zonder PPPoE",
    )

    render = sub.add_parser("render", help="toon gegenereerde bestanden")
    render.add_argument(
        "what",
        nargs="?",
        default="all",
        choices=["all", "nft", "dnsmasq", "sysctl", "links", "pppoe", "units"],
    )

    apply_cmd = sub.add_parser("apply", help="pas de configuratie toe")
    apply_cmd.add_argument("--dry-run", action="store_true", help="alleen tonen, niets wijzigen")

    sub.add_parser("migrate", help="vul ontbrekende parameters aan (met backup)")
    sub.add_parser("pending", help="toon of er niet-toegepaste wijzigingen zijn")
    sub.add_parser("discard", help="negeer niet-toegepaste wijzigingen")
    sub.add_parser("mark-applied", help="markeer de huidige configuratie als toegepast")

    sub.add_parser("rollback", help="zet de laatste backup terug")
    sub.add_parser("status", help="toon status als JSON")

    serve = sub.add_parser("serve", help="start de HTTP-GUI")
    serve.add_argument("--bind")
    serve.add_argument("--port", type=int)

    # --config/--root mogen zowel vóór als ná het subcommand staan.
    # SUPPRESS zorgt dat een subparser een eerdere globale waarde niet
    # overschrijft met zijn default.
    for command_parser in sub.choices.values():
        command_parser.add_argument(
            "--config", default=argparse.SUPPRESS, help="pad naar config.json"
        )
        command_parser.add_argument(
            "--root", default=argparse.SUPPRESS, help="root voor bestanden"
        )

    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    if args.command == "validate":
        errors = validate(cfg)
        if errors:
            for error in errors:
                print(f"FOUT: {error}", file=sys.stderr)
            return 1
        print("Configuratie geldig.")
        return 0

    if args.command == "check":
        print(connectivity.report(cfg))
        return 0

    if args.command == "simulate":
        checks = simulate.simulate(cfg, nic_only=args.nic_only)
        print(simulate.report(cfg, nic_only=args.nic_only))
        return 0 if all(check.ok for check in checks) else 1

    if args.command == "render":
        outputs: dict[str, str] = {}
        if args.what in ("all", "nft"):
            outputs["/etc/nftables.conf"] = render_nft(cfg)
        if args.what in ("all", "dnsmasq"):
            outputs["/etc/dnsmasq.d/bassie-router.conf"] = services.render_dnsmasq(cfg)
        if args.what in ("all", "sysctl"):
            outputs["/etc/sysctl.d/99-bassie-router.conf"] = services.render_sysctl(cfg)
        if args.what in ("all", "links"):
            outputs["/usr/local/sbin/bassie-setup-links"] = services.render_links_script(cfg)
        if args.what in ("all", "pppoe"):
            outputs["/etc/ppp/peers/bassie-isp"] = services.render_client_options(cfg)
            outputs["/etc/ppp/chap-secrets"] = services.render_secrets_block(cfg)
        if args.what in ("all", "units"):
            outputs.update(services.render_systemd_units(cfg))
        if args.what == "all":
            for path, content in outputs.items():
                print(f"--- {path} ---")
                print(content, end="" if content.endswith("\n") else "\n")
        else:
            print("\n".join(outputs.values()), end="")
        return 0

    if args.command == "apply":
        report = apply_config(
            cfg, root=args.root, dry_run=args.dry_run, config_file=args.config
        )
        if args.dry_run:
            print("--- nftables ---")
            print(report.files.get("/etc/nftables.conf", ""), end="")
            print("--- commando's ---")
            for command in report.commands:
                print(command)
        for check in report.checks:
            print(check)
        for error in report.errors:
            print(f"FOUT: {error}", file=sys.stderr)
        if report.backup:
            print(f"Backup: {report.backup}")
        return 0 if report.ok else 1

    if args.command == "migrate":
        result = migrate_config(args.config)
        print(f"Configuratie: {result.path}")
        if result.backup:
            print(f"Backup van origineel: {result.backup}")
        for note in result.notes:
            print(f"Afgeleid: {note}")
        for key in result.added:
            print(f"Toegevoegd met default: {key}")
        for key in result.removed:
            print(f"Verouderd/onbekend, niet overgenomen: {key}")
        for error in validate(load_config(args.config)):
            print(f"CONTROLEER IN DE GUI: {error}", file=sys.stderr)
        for error in result.errors:
            print(f"FOUT: {error}", file=sys.stderr)
        if result.added or result.removed or result.notes:
            print("Controleer de GUI (Netwerk) op de aangepaste/bijgevoegde parameters.")
        if not result.errors and not result.added and not result.removed:
            print("Configuratie was al actueel.")
        return 0 if not result.errors else 1

    if args.command == "pending":
        pending = pending_changes(cfg, args.config, root=args.root)
        print("Wijzigingen openstaan" if pending else "Alles toegepast")
        return 0

    if args.command == "discard":
        report = discard_changes(args.config, root=args.root)
        for error in report.errors:
            print(f"FOUT: {error}", file=sys.stderr)
        if report.ok:
            print("Niet-toegepaste wijzigingen genegeerd.")
        return 0 if report.ok else 1

    if args.command == "mark-applied":
        try:
            target = snapshot_applied(cfg, args.config, root=args.root)
        except OSError as exc:
            print(f"FOUT: {exc}", file=sys.stderr)
            return 1
        print(f"Toegepaste configuratie opgeslagen: {target}")
        return 0

    if args.command == "rollback":
        report = rollback(root=args.root, config_file=args.config)
        for error in report.errors:
            print(f"FOUT: {error}", file=sys.stderr)
        return 0 if report.ok else 1

    if args.command == "status":
        print(json.dumps(status(cfg, config_file=args.config, root=args.root), indent=2))
        return 0

    if args.command == "serve":
        bind = args.bind or cfg.gui.bind
        port = args.port or cfg.gui.port
        app = create_app(config_file=args.config)
        app.run(host=bind, port=port, threaded=True)
        return 0

    return 2
