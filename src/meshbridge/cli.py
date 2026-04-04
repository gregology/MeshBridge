"""Command-line interface for MeshBridge."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="meshbridge",
        description="MeshBridge: bridge MeshCore mesh radios to Discord and beyond",
    )

    subparsers = parser.add_subparsers(dest="command")

    config_arg = argparse.ArgumentParser(add_help=False)
    config_arg.add_argument("-c", "--config", default=None, help="Path to config.yaml")

    # meshbridge run
    run_parser = subparsers.add_parser("run", parents=[config_arg], help="Start the MeshBridge service")
    run_parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    # meshbridge setup
    subparsers.add_parser("setup", parents=[config_arg], help="Interactive setup wizard")

    # meshbridge status
    subparsers.add_parser("status", help="Show bridge service status")

    # meshbridge logs
    logs_parser = subparsers.add_parser("logs", help="Tail bridge logs")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    logs_parser.add_argument("-n", "--lines", type=int, default=50, help="Number of lines")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        _cmd_run(args)
    elif args.command == "setup":
        _cmd_setup(args)
    elif args.command == "status":
        _cmd_status()
    elif args.command == "logs":
        _cmd_logs(args)


def _cmd_run(args) -> None:
    """Start the MeshBridge service."""
    from meshbridge.app import App

    if args.debug:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    app = App(config_path=args.config)
    asyncio.run(app.run())


def _cmd_setup(args) -> None:
    """Run the interactive setup wizard."""
    from meshbridge.wizard import run_wizard

    run_wizard(config_path=args.config)


def _cmd_status() -> None:
    """Show systemd service status."""
    try:
        subprocess.run(["systemctl", "status", "meshbridge"], check=False)
    except FileNotFoundError:
        print("systemctl not found. Are you on a systemd-based system?")
        sys.exit(1)


def _cmd_logs(args) -> None:
    """Tail journalctl logs."""
    cmd = ["journalctl", "-u", "meshbridge", f"-n{args.lines}"]
    if args.follow:
        cmd.append("-f")
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print("journalctl not found. Are you on a systemd-based system?")
        sys.exit(1)
